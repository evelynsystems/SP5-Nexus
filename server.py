#!/usr/bin/env python3
"""SP5 Nexus local desktop UI server. Binds loopback only; no remote service.

Communication path: web UI (JavaScript) → Qt WebEngine → this controller → Rust backend
(nexus_native) or the verified Python implementation → system / AnyPS5 engine.
Native work runs on background threads; results are stored in STATE and read by the UI.
"""
from __future__ import annotations
import atexit, json, mimetypes, os, secrets, subprocess, threading, time, webbrowser, shutil, signal, tempfile
from datetime import datetime
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from uuid import uuid4
import backend as B
import engine_manager as E
import integrations as I
import library as L
import nexus_native as N
import updater as U
import artwork as A
import controllers as C
import hostcheck as H

ROOT=Path(__file__).resolve().parent
B.migrate_legacy_config()
TOKEN=secrets.token_urlsafe(32)
LOCK=threading.RLock()
PROCESSES = set()          # Python-managed subprocess.Popen objects (fallback path)
NATIVE_PROCESSES = set()   # Rust process-controller ids
CANCEL = threading.Event()
STATE={'busy':False,'job':'','recent':[], 'engine_progress':{'stage':'Not started','step':0,'total':0,'last_activity':0,'started':0,'status':'idle','detail':''},
       'scan':None,'process_stats':None,'engine_probe':None,'readiness':None,'notifications':[],'active_game':None,
       'conversion_check':None,'pad_bridge':False}
PAD = None          # controllers.ControllerService when the Qt desktop runs it
_notice_id = 0
_engines_cache = {'at': 0.0, 'key': None, 'value': []}

def art_dir(): return B.APP_DIR / 'art'

def level_of(text):
    t = text.lower()
    if t.startswith('error') or 'failed' in t or 'disconnected' in t: return 'error' if t.startswith('error') or 'failed' in t else 'warning'
    if t.startswith(('imported', 'converted', 'controller connected', 'anyps5 engine compiled')) or 'exited with code 0' in t: return 'success'
    return 'info'

def notify(text, level=None):
    """Notifications shown in the Control Center and as toasts (also written to the log)."""
    global _notice_id
    with LOCK:
        _notice_id += 1
        STATE['notifications'] = (STATE['notifications'] + [{'id': _notice_id, 'time': time.strftime('%H:%M'), 'text': text[:300], 'level': level or level_of(text)}])[-30:]
ART_TYPES = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}
_engine_cache = {'at':0.0,'key':None,'value':None}

IMPORTANT_EVENTS = ('ERROR', 'Imported', 'Converted', 'AnyPS5 engine compiled', 'Library scan', 'Update check', 'Relinker check', 'Conversion check')

def event(s):
    B.log(s)
    with LOCK:
        STATE['recent']=(STATE['recent']+[{'time':time.strftime('%H:%M'),'text':s}])[-35:]
    if s.startswith(IMPORTANT_EVENTS) or ' exited with code ' in s:
        notify(s)

NATIVE = N.init()
event(f"{NATIVE['state']}" + (f" {NATIVE['version']} ({NATIVE['binding']})" if NATIVE['version'] else f": {NATIVE['detail']}"))

def native_or_python(name, payload, fallback):
    """Use the verified Rust implementation when active; otherwise (or if it raises an
    unexpected internal error) use the Python implementation. User-facing validation errors
    (ValueError, FileNotFoundError, PermissionError) are passed through unchanged."""
    if N.active():
        try:
            return N.call(name, payload)
        except (ValueError, FileNotFoundError, PermissionError):
            raise
        except Exception as exc:
            event(f'Rust backend error in {name}: {exc}. Using the Python implementation.')
    return fallback()

def engine_progress(stage, step, total):
    with LOCK:
        STATE['engine_progress'].update(stage=stage,step=step,total=total,last_activity=time.time(),status='running',detail='')
    event('Engine stage: ' + stage)

def track_engine_process(process, active):
    with LOCK:
        if active: PROCESSES.add(process)
        else: PROCESSES.discard(process)

def engine_event(message):
    event(message)
    with LOCK:
        STATE['engine_progress'].update(last_activity=time.time(), detail=str(message)[-350:])
        if 'Cloning into ' in message and '3rdparty/' in message:
            STATE['engine_progress']['stage']='Downloading dependency: '+message.split('3rdparty/',1)[1].split("'",1)[0].split('/',1)[0]

# Routing decisions come from tools/benchmark.py (see docs/PERFORMANCE.md). Rust versions of
# record validation, search/sort and duplicate detection pass the parity tests but were
# measured 3-8x SLOWER, because the whole library must cross the boundary as JSON, so the
# Python implementations stay in use for them. Rust is used where it measured faster or
# adds reliability: library scanning, process control, launch validation, runtime staging,
# engine status and hardware/Vulkan diagnosis.
def load_with_issues():
    raw = B.load_json_recover(B.GAMES_FILE, [])
    return B.normalize_games(raw)

def load(): return load_with_issues()[0]

def settings_with_issues():
    raw = B.load_json_recover(B.CONFIG_FILE, {})
    return B.validate_settings(raw)

def settings(): return settings_with_issues()[0]

def worker(job, func):
    with LOCK:
        if STATE['busy']: raise ValueError('Another task is already running')
        STATE['busy']=True; STATE['job']=job
        CANCEL.clear()
    def run():
        try:func()
        except Exception as e:
            event('ERROR: '+str(e))
            if job=='Building AnyPS5 engine':
                with LOCK: STATE['engine_progress'].update(status='failed',detail=str(e)[-350:],last_activity=time.time())
        finally:
            with LOCK:STATE['busy']=False;STATE['job']='';STATE['process_stats']=None
    threading.Thread(target=run,daemon=True).start()

def _kill_group_later(proc, grace=3.0):
    """SIGTERM was sent; escalate to SIGKILL only for our own still-running child group."""
    def escalate():
        try: proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid, signal.SIGKILL); event('Process did not stop after SIGTERM; sent SIGKILL')
            except ProcessLookupError: pass
    threading.Thread(target=escalate,daemon=True).start()

def _execute_python(args,cwd=None,env=None):
    with subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',bufsize=1,start_new_session=True) as p:
        with LOCK: PROCESSES.add(p)
        try:
            for line in p.stdout:
                if CANCEL.is_set():
                    os.killpg(p.pid, signal.SIGTERM)
                    _kill_group_later(p)
                    raise RuntimeError('Operation cancelled')
                line=line.strip()
                if line:event(line[:600])
            try: rc=p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Output closed but the program kept running: it is still ours, so stop it.
                os.killpg(p.pid, signal.SIGTERM); _kill_group_later(p); rc=p.wait()
        finally:
            with LOCK: PROCESSES.discard(p)
    return rc

def _execute_native(args,cwd=None,env=None,key=''):
    base=os.environ
    overrides={k:v for k,v in (env or {}).items() if base.get(k)!=v}
    spawned=N.call('process_spawn',{'argv':[str(a) for a in args],'cwd':str(cwd or ''),'env':overrides,'key':key})
    pid=spawned['id']
    with LOCK: NATIVE_PROCESSES.add(pid)
    cancelled=False; last_stats=0.0; status={}
    try:
        while True:
            status=N.call('process_poll',{'id':pid})
            for line in status['lines']:
                line=line.strip()
                if line: event(line[:600])
            if status['finished']: break
            if CANCEL.is_set() and status['running'] and not cancelled:
                cancelled=True
                final=N.call('process_terminate',{'id':pid,'grace_ms':3000})
                if final.get('escalated'): event('Process did not stop after SIGTERM; sent SIGKILL')
            now=time.time()
            if status['running'] and now-last_stats>=2:
                last_stats=now
                try:
                    stats=N.call('process_stats',{'id':pid})
                    with LOCK: STATE['process_stats']={**stats,'runtime_seconds':status['runtime_seconds'],'program':status['program']}
                except Exception: pass
            time.sleep(0.2)
    finally:
        try: N.call('process_release',{'id':pid})
        except Exception: pass
        with LOCK: NATIVE_PROCESSES.discard(pid)
    if cancelled: raise RuntimeError('Operation cancelled')
    code=status.get('exit_code')
    return code if code is not None else -int(status.get('signal') or 0)

def execute(args,cwd=None,env=None,key=''):
    event('Running: '+ ' '.join(map(str,args)))
    rc=_execute_native(args,cwd,env,key) if N.active() else _execute_python(args,cwd,env)
    event('Process finished with exit code '+str(rc))
    return rc

def stop_all(grace=3.0):
    CANCEL.set()
    with LOCK:
        running=list(PROCESSES); native=list(NATIVE_PROCESSES)
    for proc in running:
        if proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGTERM); _kill_group_later(proc, grace)
            except ProcessLookupError: pass
    for pid in native:
        threading.Thread(target=lambda p=pid: _safe_native('process_terminate',{'id':p,'grace_ms':int(grace*1000)}),daemon=True).start()
    with LOCK:
        if STATE.get('scan') and STATE['scan'].get('status')=='running' and STATE['scan'].get('task'):
            _safe_native('task_cancel',{'id':STATE['scan']['task']})

def _safe_native(name,payload):
    try: return N.call(name,payload)
    except Exception: return None

def shutdown():
    """Called when the desktop window closes: stop every process this session started."""
    stop_all(grace=2.0)
    alive=[]
    deadline=time.time()+3
    while time.time()<deadline:
        with LOCK:
            alive=[p for p in PROCESSES if p.poll() is None]
        if not alive: break
        time.sleep(0.1)
    for p in alive:
        try: os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    if N.active(): _safe_native('process_terminate_all',{'grace_ms':2000})
atexit.register(shutdown)

def now_iso(): return datetime.now().astimezone().isoformat(timespec='seconds')

def resolved(path):
    try: return str(Path(path).expanduser().resolve())
    except OSError: return str(path)

def add_games(paths_and_names):
    """Validate and add games; refuses entries whose executable is already in the library."""
    added, skipped = [], []
    def modify(games):
        known={resolved(g.get('elf','')) for g in games if isinstance(g,dict)}
        for path,name in paths_and_names:
            try: p=B.validate_elf(str(path))
            except ValueError as exc: skipped.append(f'{Path(str(path)).name}: {exc}'); continue
            if str(p) in known: skipped.append(f'{p.name}: already in the library'); continue
            default_name=p.parent.name if p.name.lower()=='eboot.bin' else p.stem
            game={**B.GAME_FIELDS,'id':uuid4().hex,'name':str(name or default_name)[:100],'elf':str(p),'added':now_iso(),
                  'collections':[],'art_source':{'title':'user'} if name else {},'settings':{},'history':[]}
            # Artwork and title from the game's own sce_sys folder (never downloaded).
            try: game.update(A.apply_discovered(game, art_dir()))
            except OSError: pass
            games.append(game); known.add(str(p)); added.append(game)
    B.update_json(B.GAMES_FILE,[],modify)
    return added, skipped

def find_game(game_id):
    game=next((g for g in load() if g['id']==game_id),None)
    if not game: raise ValueError('Select a game first')
    return game

def engine_choices(configured_relinker, configured_libs=None):
    if configured_libs is None: configured_libs=settings().get('libs','')
    key=(configured_relinker,configured_libs)
    with LOCK:
        if _engines_cache['key']==key and time.time()-_engines_cache['at']<15:
            return list(_engines_cache['value'])
    engines=E.list_engines(configured_relinker,configured_libs)
    with LOCK: _engines_cache.update(at=time.time(),key=key,value=engines)
    return engines

def engine_for(game, s):
    """(relinker, libs) for a game: its chosen engine if still installed, else the default."""
    chosen=game.get('engine') or ''
    if chosen:
        for e in engine_choices(s.get('relinker','')):
            if e['relinker']==chosen and e['ready']:
                return e['relinker'], e['libs']
        raise ValueError('The engine chosen for this game is no longer installed. Pick another engine in Game Hub.')
    return s['relinker'], s['libs']

def refresh_library():
    """Missing-game detection plus artwork/metadata from game folders (user choices kept)."""
    games=load(); missing=0; updated=0
    for g in games:
        if not Path(g['elf']).is_file():
            missing+=1; continue
        try: changes=A.apply_discovered(g, art_dir())
        except OSError: changes={}
        if changes:
            update_game(g['id'],changes); updated+=1
    event(f'Library refreshed: {len(games)} games, {missing} missing, {updated} updated from game folders')
    return {'games':len(games),'missing':missing,'updated':updated}

def update_game(game_id, changes):
    def modify(games):
        for g in games:
            if isinstance(g,dict) and g.get('id')==game_id:
                for k,v in changes.items():
                    g[k]=v(g) if callable(v) else v
    B.update_json(B.GAMES_FILE,[],modify)

def start_scan(roots):
    roots=[str(Path(r).expanduser()) for r in roots if str(r).strip()]
    if not roots: raise ValueError('Enter a folder to scan')
    for r in roots:
        if not Path(r).is_dir(): raise ValueError('Not a folder: '+r)
    with LOCK:
        if STATE.get('scan') and STATE['scan'].get('status')=='running': raise ValueError('A library scan is already running')
        STATE['scan']={'status':'running','roots':roots,'started':time.time(),'backend':'rust' if N.active() else 'python','result':None,'error':'','task':None}
    payload={'roots':roots,'max_depth':8,'max_files':200000}
    def finish(result=None,error=''):
        if result:
            known={resolved(g.get('elf','')) for g in load()}
            for c in result['candidates']: c['in_library']=resolved(c['path']) in known
        with LOCK:
            STATE['scan'].update(status='failed' if error else 'done',result=result,error=error,elapsed=round(time.time()-STATE['scan']['started'],3))
        event(f"Library scan {'failed: '+error if error else 'found '+str(len(result['candidates']))+' executable(s) in '+str(result['files_scanned'])+' files'}")
    def run():
        try:
            if N.active():
                task=N.call('task_start',{'kind':'scan_library','payload':payload})['id']
                with LOCK: STATE['scan']['task']=task
                while True:
                    st=N.call('task_status',{'id':task})
                    if st['state']!='running': break
                    time.sleep(0.1)
                _safe_native('task_release',{'id':task})
                if st['state']=='done': finish(st['result'])
                else: finish(error=st.get('error','scan failed'))
            else:
                finish(L.scan_library(roots,8,200000))
        except Exception as exc:
            finish(error=str(exc))
    threading.Thread(target=run,daemon=True).start()

def engine_state(configured):
    configured_relinker=Path(configured.get('relinker',''))
    source=configured_relinker.resolve().parents[3] if configured_relinker.name=='relinker' and configured_relinker.is_file() and len(configured_relinker.resolve().parents)>3 else E.DEFAULT_SOURCE
    key=(str(source),configured.get('relinker',''),configured.get('libs',''))
    with LOCK:
        if _engine_cache['key']==key and time.time()-_engine_cache['at']<10 and _engine_cache['value']:
            return dict(_engine_cache['value'])
    state=native_or_python('engine_status',{'source':str(source)},lambda: E.status(source))
    configured_libs=Path(configured.get('libs',''))
    state['configured_relinker_ready']=bool(configured_relinker.is_file() and os.access(configured_relinker,os.X_OK))
    state['configured_libs_count']=len(list(configured_libs.glob('*.prx'))) if configured_libs.is_dir() else 0
    with LOCK: _engine_cache.update(at=time.time(),key=key,value=dict(state))
    return state

def action(name,data):
    if name=='stop':
        stop_all()
        event('Stop requested')
        return {}
    if name=='add':
        added,skipped=add_games([(data.get('path',''),data.get('name'))])
        if not added: raise ValueError(skipped[0] if skipped else 'Game was not added')
        event('Imported '+added[0]['name']);return {'game':added[0]}
    if name=='import_games':
        items=data.get('items') or []
        if not isinstance(items,list) or not items: raise ValueError('Select at least one executable to import')
        added,skipped=add_games([(str(i.get('path','')),str(i.get('name','') or '')) for i in items[:500] if isinstance(i,dict)])
        event(f'Imported {len(added)} game(s)'+(f'; skipped {len(skipped)}' if skipped else ''))
        for s in skipped[:10]: event('Skipped '+s)
        return {'added':len(added),'skipped':skipped}
    if name=='scan_library':
        start_scan(data.get('roots') or [data.get('root','')])
        return {}
    if name=='refresh_library':
        return refresh_library()
    if name=='favorite':
        update_game(str(data.get('id','')),{'favorite':bool(data.get('value'))}); return {}
    if name=='set_art':
        game=find_game(str(data.get('id',''))); kind=data.get('kind')
        if kind not in ('cover','background'): raise ValueError('Unknown artwork kind')
        field='art' if kind=='cover' else 'background'
        source=dict(game.get('art_source') or {})
        if str(data.get('path','')).strip():
            path=A.cache_image(str(data['path']).strip(), art_dir(), game['id'], kind)
            source[kind]='user'
            update_game(game['id'],{field:path,'art_source':source})
            event(f"{'Cover' if kind=='cover' else 'Background'} set for {game['name']}")
        else:
            # Clearing returns to the game's own artwork if it has any, otherwise generated art.
            source.pop(kind,None)
            for old in (art_dir()/''.join(c for c in game['id'] if c.isalnum())).glob(kind+'.*'): old.unlink()
            update_game(game['id'],{field:'','art_source':source})
            g2=find_game(game['id']); changes=A.apply_discovered(g2, art_dir())
            if changes: update_game(game['id'],changes)
        return {}
    if name=='game_settings':
        game=find_game(str(data.get('id','')))
        clean=B.validate_runtime_settings(data.get('settings') or {})
        update_game(game['id'],{'settings':clean}); event(f"Game settings saved for {game['name']}"); return {'settings':clean}
    if name=='locate_game':
        game=find_game(str(data.get('id','')))
        p=B.validate_elf(str(data.get('path','')))
        if any(g['id']!=game['id'] and resolved(g['elf'])==str(p) for g in load()): raise ValueError('That executable is already in the library')
        update_game(game['id'],{'elf':str(p)}); event(f"Located {game['name']}: {p}"); return {}
    if name=='readiness':
        def check():
            r={**H.readiness(),'checked_at':now_iso()}
            with LOCK: STATE['readiness']=r
            notify('Host readiness: '+{'pass':'all documented requirements met','warn':'check the warnings','fail':'a documented requirement is not met'}[r['summary']],
                   {'pass':'success','warn':'warning','fail':'error'}[r['summary']])
        threading.Thread(target=check,daemon=True).start(); return {}
    if name=='check_conversion':
        game=find_game(str(data.get('id',''))); s=settings()
        relinker,_libs=engine_for(game,s)
        probe_out=Path(tempfile.mkdtemp(prefix='sp5-dryrun-'))/'app.elf'
        args=B.conversion_command(relinker,game['elf'],probe_out,intel=s['intel'],module_parent=game.get('module_parent') or None,dry_run=True)
        def run():
            try:
                rc=execute(args,cwd=Path(game['elf']).parent)
                ok=rc==0
                with LOCK: STATE['conversion_check']={'id':game['id'],'ok':ok,'message':B.relinker_exit_message(rc),'checked_at':now_iso()}
                event(f"Conversion check for {game['name']}: {'passed (relinker --dry-run, nothing written)' if ok else 'failed — relinker '+B.relinker_exit_message(rc)}")
            finally:
                shutil.rmtree(probe_out.parent,ignore_errors=True)
        worker('Checking conversion of '+game['name'],run); return {}
    if name=='remove':
        B.update_json(B.GAMES_FILE,[],lambda games: games.__setitem__(slice(None),[g for g in games if not isinstance(g,dict) or g.get('id')!=data.get('id')]));event('Library entry removed');return {}
    if name=='save_game':
        changes={}
        for k in ('name','module_parent','resources','genre','description','category'):
            if k in data:changes[k]=str(data[k])[:4000 if k=='description' else 2048].strip() if k!='module_parent' else str(data[k])[:2048]
        if 'name' in changes:
            if not changes['name']: raise ValueError('Game title cannot be empty')
            source=dict(find_game(str(data.get('id',''))).get('art_source') or {}); source['title']='user'; changes['art_source']=source
        if 'collections' in data:
            cols=data['collections']
            if not isinstance(cols,list) or any(not isinstance(c,str) for c in cols): raise ValueError('Collections must be a list of names')
            changes['collections']=sorted({c.strip()[:60] for c in cols if c.strip()})[:50]
        if 'favorite' in data: changes['favorite']=bool(data['favorite'])
        if 'engine' in data:
            chosen=str(data['engine'] or '')
            if chosen and chosen not in {e['relinker'] for e in engine_choices(settings().get('relinker','')) if e['ready']}:
                raise ValueError('Choose one of the installed engines')
            changes['engine']=chosen
        if 'art' in data:
            art=str(data['art']).strip()[:2048]
            if art:
                p=Path(art).expanduser()
                if p.suffix.lower() not in ART_TYPES: raise ValueError('Cover image must be a PNG, JPEG or WebP file')
                if not p.is_file(): raise ValueError('Cover image file does not exist: '+art)
                art=str(p.resolve())
            changes['art']=art
        if 'launch_args' in data:
            B.split_launch_args(data['launch_args'])  # validate
            changes['launch_args']=str(data['launch_args'])[:4096]
        if 'compat_note' in data:
            if data['compat_note'] not in B.COMPAT_NOTES: raise ValueError('Unknown compatibility note')
            changes['compat_note']=data['compat_note']
        update_game(str(data.get('id','')),changes);event('Game details saved');return {}
    if name=='settings':
        s=settings()
        for k in ('relinker','libs','output_dir','gpu'):
            if k in data:s[k]=str(data[k])[:4096]
        if 'gpu' in data and s['gpu'].strip(): B.validate_runtime_settings({'gpu':s['gpu']})
        if 'intel' in data:s['intel']=bool(data['intel'])
        for opt in ('use_gamescope','use_mangohud'):
            if opt in data:s[opt]=bool(data[opt])
        if data.get('library_sort') in L.SORTS: s['library_sort']=data['library_sort']
        if data.get('library_filter') in L.FILTERS: s['library_filter']=data['library_filter']
        # Interface and controller preferences: validated with the same rules used on load.
        prefs={k:data[k] for k in ('theme','motion','game_colors','pad_enabled','pad_deadzone','pad_repeat_ms','pad_confirm','pad_preferred') if k in data}
        if prefs:
            checked,issues=B.validate_settings({**s,**prefs})
            if issues and any(k in i for i in issues for k in prefs): raise ValueError('; '.join(i for i in issues if any(k in i for k in prefs)))
            s.update({k:checked[k] for k in prefs})
        B.save_json(B.CONFIG_FILE,s)
        quiet={'library_sort','library_filter','theme','motion','game_colors'}
        if set(data)-quiet: event('Settings saved')
        return {}
    if name=='diagnose':
        worker('Checking GPU and Vulkan',lambda:event('GPU diagnostics: '+json.dumps(run_diagnostics())))
        return {}
    if name=='verify_engine':
        relinker=settings().get('relinker','')
        def probe():
            if N.active():
                r=N.call('engine_probe',{'relinker':relinker})
            else:
                ok,msg=E.probe_relinker(relinker); r={'ok':ok,'message':msg}
            r['checked_at']=now_iso()
            with LOCK: STATE['engine_probe']=r; _engines_cache['at']=0
            event('Relinker check: '+r['message'])
        threading.Thread(target=probe,daemon=True).start()
        return {}
    if name=='engine':
        missing=E.status()['missing_commands']
        if missing:
            raise ValueError('Missing build tools: '+', '.join(missing)+'. Copy and run the Ubuntu dependencies command first, then try again.')
        def run():
            result=(U.build_new_engine(engine_event, settings().get('relinker',''), stage=engine_progress,on_process=track_engine_process,cancelled=CANCEL.is_set) if data.get('update') else E.install(engine_event,E.DEFAULT_SOURCE,False,stage=engine_progress,on_process=track_engine_process,cancelled=CANCEL.is_set))
            if result is None:
                event('AnyPS5 is already current; no changes made')
                with LOCK: STATE['engine_progress']['status']='complete'
                return
            s=settings();s['relinker']=result['relinker'];s['libs']=result['libs'];B.save_json(B.CONFIG_FILE,s)
            with LOCK: _engine_cache['at']=0; _engines_cache['at']=0
            event('AnyPS5 engine compiled and automatically configured')
            with LOCK: STATE['engine_progress']['status']='complete'
        with LOCK:
            if STATE['busy']: raise ValueError('Another task is already running')
            STATE['engine_progress']={'stage':'Preparing engine installation','step':0,'total':0,'last_activity':time.time(),'started':time.time(),'status':'running','detail':''}
        worker('Building AnyPS5 engine',run);return {}
    if name=='check_updates':
        def scan():
            result=U.check_engine(settings().get('relinker',''))
            with LOCK: STATE['update_check']=result
            event('Update check: '+result['message'])
        worker('Checking engine updates',scan)
        return {}
    if name in ('convert','launch'):
        game=next((g for g in load() if g['id']==data.get('id')),None)
        if not game:raise ValueError('Select a game first')
        s=settings()
        if name=='convert':
            dest=Path(s['output_dir']).expanduser()/game['id']/'app.elf'
            temporary=dest.with_name('app.pending.elf')
            relinker,libs=engine_for(game,s)
            args=B.conversion_command(relinker,game['elf'],temporary,intel=s['intel'],module_parent=game.get('module_parent') or None)
            def run():
                dest.parent.mkdir(parents=True,exist_ok=True)
                temporary.unlink(missing_ok=True)
                try:
                    rc=execute(args,cwd=Path(game['elf']).parent)
                    if rc!=0:
                        raise RuntimeError(f'Relinker {B.relinker_exit_message(rc)}; previous output preserved')
                    if not temporary.is_file() or temporary.stat().st_size < 20 or temporary.open('rb').read(4)!=b'\x7fELF':
                        raise RuntimeError('Relinker produced no valid ELF output')
                    resources=game.get('resources') or None
                    count=native_or_python('stage_runtime',{'output':str(temporary),'libs':libs,'resources':resources or ''},
                                           lambda: B.prepare_runtime(temporary,libs,resources))
                    os.replace(temporary,dest)
                    update_game(game['id'],{'output':str(dest)})
                finally:
                    temporary.unlink(missing_ok=True)
                event(f'Converted and staged {count} runtime libraries. Playability unverified.')
            worker('Converting '+game['name'],run)
        else:
            extra=B.split_launch_args(game.get('launch_args',''))
            runtime=B.runtime_env(game.get('settings') or {}, s.get('gpu',''))   # documented AnyPS5 variables only
            def python_plan():
                a,c,e=B.launch_command(game.get('output',''),s.get('gpu',''),game.get('launch_args',''))
                a,c,e=I.wrap_launch(a,c,e,gamescope=s.get('use_gamescope',False),mangohud=s.get('use_mangohud',False))
                if not (c/'libs').is_dir():raise ValueError('Runtime libraries folder missing. Convert and stage libraries first.')
                return {'argv':a,'cwd':str(c),'env':{k:v for k,v in e.items() if os.environ.get(k)!=v}}
            plan=native_or_python('launch_plan',{'output':game.get('output',''),'args':extra,'gpu':s.get('gpu',''),
                                                 'gamescope':s.get('use_gamescope',False),'mangohud':s.get('use_mangohud',False)},python_plan)
            plan_env={k:v for k,v in plan['env'].items() if k!='ANYPS5_GPU'}
            args,cwd,env=plan['argv'],Path(plan['cwd']),{**os.environ,**plan_env,**runtime}
            def run():
                started=time.time(); started_iso=now_iso(); rc=None
                update_game(game['id'],{'last_played':started_iso})
                with LOCK: STATE['active_game']={'id':game['id'],'name':game['name'],'started':started_iso,'settings':runtime}
                try:
                    rc=execute(args,cwd,env,key='game:'+game['id'])
                    event(f"{game['name']} exited with code {rc}" + (' (normal exit)' if rc==0 else ' — check the log above for the engine error'))
                finally:
                    played=int(time.time()-started)
                    session={'start':started_iso,'seconds':played,'exit':rc}
                    update_game(game['id'],{'playtime_seconds':lambda g: int(g.get('playtime_seconds') or 0)+played,
                                            'history':lambda g: ([h for h in (g.get('history') or []) if isinstance(h,dict)]+[session])[-B.HISTORY_LIMIT:]})
                    with LOCK: STATE['active_game']=None
            worker('Launching '+game['name'],run)
        return {}
    raise ValueError('Unknown action')

def run_diagnostics():
    result=native_or_python('diagnose',{},B.diagnose)
    with LOCK:
        STATE['diagnostics']={**result,'checked_at':now_iso()}
    return result

def _mtime(path):
    try: return int(Path(path).stat().st_mtime_ns//1000000)
    except OSError: return 0

def library_order(games,s):
    rows=[{**g,'output_exists':g.get('output_exists',False)} for g in games]
    sort=s.get('library_sort','name'); flt=s.get('library_filter','all')
    return L.query_library(rows,'',sort,flt), L.find_duplicates(rows)

def build_state():
    try: log=B.LOG_FILE.read_text(encoding='utf8',errors='replace').splitlines()[-60:]
    except OSError: log=[]
    if N.active():
        for line in (_safe_native('native_events',{}) or []): event('Rust backend: '+line)
    with LOCK:
        state=dict(STATE)
        state['engine_progress']=dict(STATE['engine_progress'])
    configured,setting_issues=settings_with_issues()
    games,game_issues=load_with_issues()
    games=[{**g,'output_exists':bool(g.get('output') and Path(g['output']).is_file()),
            'elf_exists':Path(g.get('elf','')).is_file(),
            'has_cover':bool(g.get('art')) and Path(g['art']).is_file(),
            'has_background':bool(g.get('background')) and Path(g['background']).is_file()} for g in games]
    for g in games:
        g['missing']=not g['elf_exists']
        # Art version (file mtime) lets the browser cache images until the file changes.
        g['art_v']=_mtime(g.get('art')) if g['has_cover'] else 0
        g['bg_v']=_mtime(g.get('background')) if g['has_background'] else 0
    order,dups=library_order(games,configured)
    warnings=list(B.CONFIG_WARNINGS)+setting_issues+game_issues
    names={g['id']:g['name'] for g in games}
    warnings+=['Duplicate library entries for one executable: '+', '.join(names.get(i,i) for i in group) for group in dups]
    return {'games':games,'library_order':order,'settings':configured,'engine':engine_state(configured),'state':state,'log':log,
            'diagnostics':state.get('diagnostics'),'requirements':'sudo apt update && sudo apt install '+' '.join(E.PACKAGES),
            'update_check':state.get('update_check'),'system_tools':U.system_tools(),'native':N.status(),
            'config_warnings':warnings,'compat_notes':list(B.COMPAT_NOTES),
            'engines':engine_choices(configured.get('relinker',''),configured.get('libs','')),'controllers':controller_state(),
            'runtime_options':{k:{'env':v[0],'kind':'bool' if v[1] is bool else 'text','label':v[3]} for k,v in B.RUNTIME_OPTIONS.items()},
            'collections':sorted({c for g in games for c in g.get('collections',[])}),
            'categories':sorted({g['category'] for g in games if g.get('category')}),
            'recent_ids':[g['id'] for g in sorted((g for g in games if g.get('last_played')),key=lambda g:g['last_played'],reverse=True)][:12]}

def controller_state():
    if PAD is not None:
        for text in PAD.take_notices(): notify(text, 'success' if 'connected:' in text else 'warning')
        st=PAD.status(); st['bridge']=True
        return st
    return {'running':False,'bridge':False,'active':None,'pads':C.detect(),'events_seen':0,'last_button':'','last_button_label':'','error':''}

SECURITY_HEADERS={'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                  'Content-Security-Policy':"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'web'),**kw)
    def log_message(self,*args):pass
    def end_headers(self):
        for k,v in SECURITY_HEADERS.items(): self.send_header(k,v)
        super().end_headers()
    def reply(self,obj,code=200):
        data=json.dumps(obj).encode()
        self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def host_ok(self):
        return self.headers.get('Host','') == f'127.0.0.1:{self.server.server_port}'
    def authorized(self):
        if not self.host_ok(): return False
        return secrets.compare_digest(self.headers.get('X-SP5-Token',''), TOKEN)
    def serve_art(self,game_id):
        # Images cannot send headers, so this route accepts the session token as a query value.
        query=parse_qs(urlparse(self.path).query)
        if not secrets.compare_digest(query.get('token',[''])[0], TOKEN):
            return self.reply({'error':'Invalid session'},403)
        field='background' if query.get('kind',['cover'])[0]=='background' else 'art'
        game=next((g for g in load() if g['id']==game_id),None)
        art=Path(game[field]) if game and game.get(field) else None
        if not art or art.suffix.lower() not in ART_TYPES or not art.is_file():
            return self.reply({'error':'Not found'},404)
        try: data=art.read_bytes()
        except OSError: return self.reply({'error':'Not readable'},404)
        if len(data)>20*1024*1024: return self.reply({'error':'Image too large'},413)
        self.send_response(200);self.send_header('Content-Type',ART_TYPES[art.suffix.lower()]);self.send_header('Cache-Control','private, max-age=86400');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        path=urlparse(self.path).path
        if not self.host_ok():
            return self.reply({'error':'Invalid Host'},403)
        if path.startswith('/api/art/'):
            return self.serve_art(path[len('/api/art/'):])
        if path.startswith('/api/') and not self.authorized():
            return self.reply({'error':'Invalid session'},403)
        try:
            if path=='/api/state':
                self.reply(build_state())
            elif path=='/api/integrations':
                self.reply(I.status())
            elif path=='/api/diagnostics':
                self.reply(run_diagnostics())
            elif path=='/api/native':
                self.reply(N.info())
            elif path=='/api/controllers':
                self.reply(controller_state())
            elif path=='/':
                if not secrets.compare_digest(parse_qs(urlparse(self.path).query).get('token',[''])[0], TOKEN):
                    return self.reply({'error':'Invalid session'},403)
                super().do_GET()
            elif path in ('/app.js','/style.css') or (path.startswith('/assets/') and '..' not in Path(path).parts):
                super().do_GET()
            else: self.reply({'error':'Not found'},404)
        except Exception as exc:
            # A failure must reach the interface as a message, not as a dropped connection.
            event(f'Request {path} failed: {exc}')
            self.reply({'error':str(exc)},500)
    def do_POST(self):
        if not self.authorized():return self.reply({'error':'Invalid session or Host'},403)
        if urlparse(self.path).path!='/api/action':return self.reply({'error':'Not found'},404)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<0 or n>16384:raise ValueError('Request too large')
            req=json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(req,dict) or not isinstance(req.get('data',{}),dict): raise ValueError('Malformed request')
            self.reply({'ok':True,'result':action(req.get('name'),req.get('data',{}))})
        except Exception as exc:self.reply({'ok':False,'error':str(exc)},400)

if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url=f'http://127.0.0.1:{server.server_port}/?token={TOKEN}'
    event('SP5 Nexus visual dashboard started. Powered by AnyPS5.')
    print('SP5 Nexus UI:',url,flush=True)
    if os.environ.get('SP5_NO_BROWSER')!='1':threading.Timer(.6,lambda:webbrowser.open(url)).start()
    def on_term(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,on_term)   # stop owned processes on a normal termination request
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        shutdown()
        server.server_close()
