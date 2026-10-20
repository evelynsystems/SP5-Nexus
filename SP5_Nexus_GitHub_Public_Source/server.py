#!/usr/bin/env python3
"""SP5 Nexus local desktop UI server. Binds loopback only; no remote service."""
from __future__ import annotations
import json, os, secrets, subprocess, threading, time, webbrowser, shutil, signal, tempfile
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from uuid import uuid4
import backend as B
import engine_manager as E
import integrations as I
import updater as U

ROOT=Path(__file__).resolve().parent
B.migrate_legacy_config()
TOKEN=secrets.token_urlsafe(32)
LOCK=threading.RLock()
PROCESSES = set()
CANCEL = threading.Event()
STATE={'busy':False,'job':'','recent':[], 'engine_progress':{'stage':'Not started','step':0,'total':0,'last_activity':0,'started':0,'status':'idle','detail':''}}

def event(s):
    B.log(s)
    with LOCK:
        STATE['recent']=(STATE['recent']+[{'time':time.strftime('%H:%M'),'text':s}])[-35:]

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

def load(): return B.load_json(B.GAMES_FILE,[])
def settings(): return B.defaults() | B.load_json(B.CONFIG_FILE,{})

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
            with LOCK:STATE['busy']=False;STATE['job']=''
    threading.Thread(target=run,daemon=True).start()

def execute(args,cwd=None,env=None):
    event('Running: '+ ' '.join(map(str,args)))
    with subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',bufsize=1,start_new_session=True) as p:
        with LOCK: PROCESSES.add(p)
        try:
            for line in p.stdout:
                if CANCEL.is_set():
                    os.killpg(p.pid, signal.SIGTERM)
                    raise RuntimeError('Operation cancelled')
                line=line.strip()
                if line:event(line[:600])
            rc=p.wait(timeout=10)
        finally:
            with LOCK: PROCESSES.discard(p)
    event('Process finished with exit code '+str(rc))
    return rc

def action(name,data):
    if name=='stop':
        CANCEL.set()
        with LOCK:
            running=list(PROCESSES)
        for proc in running:
            if proc.poll() is None:
                try: os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError: pass
        event('Stop requested')
        return {}
    if name=='add':
        p=B.validate_elf(str(data.get('path','')))
        game={'id':uuid4().hex,'name':str(data.get('name') or p.stem)[:100],'elf':str(p),'output':'','module_parent':'','resources':'','art':''}
        B.update_json(B.GAMES_FILE,[],lambda games: games.append(game));event('Imported '+game['name']);return {'game':game}
    if name=='remove':
        B.update_json(B.GAMES_FILE,[],lambda games: games.__setitem__(slice(None),[g for g in games if g['id']!=data['id']]));event('Library entry removed');return {}
    if name=='save_game':
        def modify(games):
            for g in games:
                if g['id']==data['id']:
                    for k in ('name','module_parent','resources','art'):
                        if k in data:g[k]=str(data[k])[:2048]
        B.update_json(B.GAMES_FILE,[],modify);return {}
    if name=='settings':
        s=settings()
        for k in ('relinker','libs','output_dir','gpu'):
            if k in data:s[k]=str(data[k])[:4096]
        if 'intel' in data:s['intel']=bool(data['intel'])
        for opt in ('use_gamescope','use_mangohud'):
            if opt in data:s[opt]=bool(data[opt])
        B.save_json(B.CONFIG_FILE,s);event('Settings saved');return {}
    if name=='diagnose':
        worker('Checking GPU and Vulkan',lambda:event('GPU diagnostics: '+json.dumps(B.diagnose())))
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
            args=B.conversion_command(s['relinker'],game['elf'],temporary,intel=s['intel'],module_parent=game.get('module_parent') or None)
            def run():
                dest.parent.mkdir(parents=True,exist_ok=True)
                temporary.unlink(missing_ok=True)
                try:
                    if execute(args,cwd=Path(game['elf']).parent)!=0:
                        raise RuntimeError('Relinker failed; previous output preserved')
                    if not temporary.is_file() or temporary.stat().st_size < 20 or temporary.open('rb').read(4)!=b'\x7fELF':
                        raise RuntimeError('Relinker produced no valid ELF output')
                    count=B.prepare_runtime(temporary,s['libs'],game.get('resources') or None)
                    os.replace(temporary,dest)
                    def mark(games):
                        for g in games:
                            if g['id']==game['id']: g['output']=str(dest)
                    B.update_json(B.GAMES_FILE,[],mark)
                finally:
                    temporary.unlink(missing_ok=True)
                event(f'Converted and staged {count} runtime libraries. Playability unverified.')
            worker('Converting '+game['name'],run)
        else:
            args,cwd,env=B.launch_command(game.get('output',''),s.get('gpu',''))
            args,cwd,env=I.wrap_launch(args,cwd,env,gamescope=s.get('use_gamescope',False),mangohud=s.get('use_mangohud',False))
            if not (cwd/'libs').is_dir():raise ValueError('Runtime libraries folder missing. Convert and stage libraries first.')
            worker('Launching '+game['name'],lambda:execute(args,cwd,env))
        return {}
    raise ValueError('Unknown action')

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT/'web'),**kw)
    def log_message(self,*args):pass
    def reply(self,obj,code=200):
        data=json.dumps(obj).encode()
        self.send_response(code);self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'");self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def authorized(self):
        host=self.headers.get('Host','')
        expected=f'127.0.0.1:{self.server.server_port}'
        if host != expected: return False
        return secrets.compare_digest(self.headers.get('X-SP5-Token',''), TOKEN)
    def do_GET(self):
        path=urlparse(self.path).path
        if self.headers.get('Host','') != f'127.0.0.1:{self.server.server_port}':
            return self.reply({'error':'Invalid Host'},403)
        if path.startswith('/api/') and not self.authorized():
            return self.reply({'error':'Invalid session'},403)
        if path=='/api/state':
            try: log=B.LOG_FILE.read_text(encoding='utf8').splitlines()[-60:]
            except OSError: log=[]
            with LOCK:
                state=dict(STATE)
                state['engine_progress']=dict(STATE['engine_progress'])
            configured=settings()
            configured_relinker=Path(configured.get('relinker',''))
            configured_libs=Path(configured.get('libs',''))
            engine_state=E.status(configured_relinker.resolve().parents[3] if configured_relinker.name=='relinker' and configured_relinker.is_file() else E.DEFAULT_SOURCE)
            engine_state['configured_relinker_ready']=bool(configured_relinker.is_file() and os.access(configured_relinker,os.X_OK))
            engine_state['configured_libs_count']=len(list(configured_libs.glob('*.prx'))) if configured_libs.is_dir() else 0
            self.reply({'games':[{**g,'output_exists':bool(g.get('output') and Path(g['output']).is_file())} for g in load()],'settings':settings(),'engine':engine_state,'state':state,'log':log,'diagnostics':state.get('diagnostics'), 'requirements':'sudo apt update && sudo apt install '+' '.join(E.PACKAGES), 'update_check':state.get('update_check'), 'system_tools':U.system_tools()})
        elif path=='/api/integrations':
            self.reply(I.status())
        elif urlparse(self.path).path=='/api/diagnostics':
            result=B.diagnose()
            with LOCK:
                STATE['diagnostics']={**result, 'checked_at':__import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')}
            self.reply(result)
        elif path=='/':
            from urllib.parse import parse_qs
            if not secrets.compare_digest(parse_qs(urlparse(self.path).query).get('token',[''])[0], TOKEN):
                return self.reply({'error':'Invalid session'},403)
            super().do_GET()
        elif path in ('/app.js','/style.css') or (path.startswith('/assets/') and '..' not in Path(path).parts):
            super().do_GET()
        else: self.reply({'error':'Not found'},404)
    def do_POST(self):
        if not self.authorized():return self.reply({'error':'Invalid session or Host'},403)
        if urlparse(self.path).path!='/api/action':return self.reply({'error':'Not found'},404)
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n>16384:raise ValueError('Request too large')
            req=json.loads(self.rfile.read(n))
            self.reply({'ok':True,'result':action(req.get('name'),req.get('data',{}))})
        except Exception as exc:self.reply({'ok':False,'error':str(exc)},400)

if __name__=='__main__':
    import socket
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    url=f'http://127.0.0.1:{server.server_port}/?token={TOKEN}'
    event('SP5 Nexus visual dashboard started. Powered by AnyPS5.')
    print('SP5 Nexus UI:',url,flush=True)
    if os.environ.get('SP5_NO_BROWSER')!='1':threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
