"""AnyPS5 Studio integration. No game files are distributed."""
from __future__ import annotations
import json, os, shutil, subprocess, threading, stat, tempfile, time, re
from datetime import datetime
from pathlib import Path

APP_DIR = Path(os.environ.get('SP5_CONFIG_DIR', str(Path.home() / '.config' / 'sp5-nexus')))
LEGACY_CONFIG_DIR = Path.home() / '.config' / 'anyps5-studio'
CONFIG_FILE = APP_DIR / 'settings.json'
GAMES_FILE = APP_DIR / 'games.json'
LOG_FILE = APP_DIR / 'activity.log'
def engine_root():
    """Where the locally built AnyPS5 checkout lives. SP5_ENGINE_DIR wins; otherwise the
    app folder is used when it is writable (source install, the original behaviour) and
    ~/.local/share/sp5-nexus/engine/AnyPS5 when it is not (AppImage, system install)."""
    if os.environ.get('SP5_ENGINE_DIR'):
        return Path(os.environ['SP5_ENGINE_DIR']).expanduser()
    app_engine = Path(__file__).resolve().parent / 'engine'
    if os.access(app_engine if app_engine.exists() else app_engine.parent, os.W_OK):
        return app_engine / 'AnyPS5'
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return data / 'sp5-nexus' / 'engine' / 'AnyPS5'


ENGINE_ROOT = engine_root()
LEGACY_ROOT = Path.home() / 'Documents' / 'AnyPS5'
DEFAULT_RELINKER = ENGINE_ROOT / 'build/core/relinker/relinker'
DEFAULT_LIBS = ENGINE_ROOT / 'build/core/libs/libs'


DATA_LOCK = threading.RLock()
LOG_LOCK = threading.Lock()
# Problems found while loading user data. Shown in the interface; never silently discarded.
CONFIG_WARNINGS = []

def _warn(message):
    with DATA_LOCK:
        if message not in CONFIG_WARNINGS:
            CONFIG_WARNINGS.append(message)
            del CONFIG_WARNINGS[:-20]

def load_json(path, default):
    with DATA_LOCK:
        if not path.exists():
            return default.copy() if isinstance(default, (dict, list)) else default
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, type(default)):
                raise ValueError('Unexpected JSON type')
            return value
        except (OSError, ValueError) as exc:
            raise ValueError(f'Cannot load {path}: {exc}. The original file was preserved. Repair or restore it before continuing.') from exc

def save_json(path, value):
    with DATA_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(value, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def migrate_legacy_config():
    """Copy old preferences only when new files do not exist; never delete originals."""
    if os.environ.get('SP5_CONFIG_DIR'):
        return
    for name in ('settings.json', 'games.json'):
        old = LEGACY_CONFIG_DIR / name
        new = APP_DIR / name
        if new.exists() or not old.is_file():
            continue
        try:
            # Verify before copying. Corrupt legacy data is left in place for repair.
            value = json.loads(old.read_text(encoding='utf-8'))
            if not isinstance(value, dict if name == 'settings.json' else list):
                continue
            save_json(new, value)
        except (OSError, ValueError):
            pass

def load_json_recover(path, default):
    """Load JSON; if the file is corrupt, preserve it under a .corrupt name and use defaults.

    Read permission problems are re-raised unchanged, because renaming a file we
    cannot read would hide a real system problem instead of recovering from corruption.
    """
    with DATA_LOCK:
        try:
            return load_json(path, default)
        except ValueError as exc:
            if isinstance(exc.__cause__, OSError):
                raise
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            keep = path.with_name(f'{path.name}.corrupt-{stamp}')
            try:
                os.replace(path, keep)
            except OSError as move_error:
                raise ValueError(f'{path} is corrupt and could not be preserved: {move_error}') from exc
            _warn(f'{path.name} was unreadable ({exc.__cause__ or exc}). It was preserved as {keep.name} and defaults are in use.')
            return default.copy() if isinstance(default, (dict, list)) else default


SETTING_TYPES = {'relinker': str, 'libs': str, 'output_dir': str, 'gpu': str,
                 'intel': bool, 'use_gamescope': bool, 'use_mangohud': bool,
                 # 3.0 interface and controller preferences
                 'theme': str, 'motion': str, 'game_colors': bool,
                 'pad_enabled': bool, 'pad_deadzone': int, 'pad_repeat_ms': int,
                 'pad_confirm': str, 'pad_preferred': str}
THEMES = ('nexus', 'aurora', 'ember', 'graphite')
MOTION_LEVELS = ('full', 'reduced', 'off')
SETTING_CHOICES = {'theme': THEMES, 'motion': MOTION_LEVELS, 'pad_confirm': ('south', 'east')}
SETTING_RANGES = {'pad_deadzone': (0, 60), 'pad_repeat_ms': (100, 800)}


def validate_settings(raw):
    """Return (settings, issues). Wrong value types fall back to defaults; unknown keys are kept."""
    base = defaults()
    result = dict(base)
    issues = []
    for key, value in (raw or {}).items():
        expected = SETTING_TYPES.get(key)
        if expected is None:
            result[key] = value
        elif (isinstance(value, expected) and not (expected is not bool and isinstance(value, bool))
              and value in SETTING_CHOICES.get(key, (value,))
              and SETTING_RANGES.get(key, (value, value))[0] <= value <= SETTING_RANGES.get(key, (value, value))[1]):
            result[key] = value
        else:
            issues.append(f'Setting "{key}" had an invalid value and the default is in use.')
    # Only paths the user saved are checked; built-in defaults point at a not-yet-built engine.
    for key, label in (('relinker', 'Relinker'), ('libs', 'PRX runtime folder')):
        if key in (raw or {}) and result.get(key) and result[key] != base.get(key) and not Path(str(result[key])).expanduser().exists():
            issues.append(f'{label} path does not exist: {result[key]}')
    return result, issues


GAME_FIELDS = {'name': '', 'elf': '', 'output': '', 'module_parent': '', 'resources': '', 'art': '',
               'launch_args': '', 'compat_note': 'Not tested', 'added': '', 'last_played': '',
               'playtime_seconds': 0,
               # 3.0 library fields (optional in older games.json files)
               'favorite': False, 'collections': [], 'category': '', 'background': '', 'genre': '',
               'description': '', 'title_id': '', 'engine': '', 'art_source': {}, 'settings': {},
               'history': []}
COMPAT_NOTES = ('Not tested', 'Conversion fails', 'Does not start', 'Starts', 'In game', 'Playable')
HISTORY_LIMIT = 20


def normalize_games(raw):
    """Fill missing fields without discarding user-supplied data. Non-object entries are reported."""
    games, issues, seen = [], [], set()
    for index, entry in enumerate(raw if isinstance(raw, list) else []):
        ident = entry.get('id') if isinstance(entry, dict) else None
        if not isinstance(ident, (str, int)) or isinstance(ident, bool) or not str(ident).strip():
            issues.append(f'Library entry {index + 1} is not a valid game record and is hidden.')
            continue
        game = {**GAME_FIELDS, **entry}
        game['id'] = str(game['id'])
        if game['id'] in seen:
            issues.append(f'Duplicate library id {game["id"][:8]} is hidden.')
            continue
        seen.add(game['id'])
        for key, default in GAME_FIELDS.items():
            value = game.get(key)
            if not isinstance(value, type(default)) or (isinstance(value, bool) and not isinstance(default, bool)):
                game[key] = default.copy() if isinstance(default, (list, dict)) else default
        game['collections'] = [c for c in game['collections'] if isinstance(c, str) and c.strip()]
        game['history'] = [h for h in game['history'] if isinstance(h, dict)][-HISTORY_LIMIT:]
        game['art_source'] = dict(game['art_source'])
        game['settings'] = dict(game['settings'])
        if not game['name']:
            game['name'] = Path(game['elf']).stem or 'Untitled'
        games.append(game)
    return games, issues

def update_json(path, default, modifier):
    with DATA_LOCK:
        data = load_json(path, default)
        result = modifier(data)
        save_json(path, data)
        return result

def log(message):
    """Append to the activity log. Logging must never crash a worker thread."""
    entry = f'[{datetime.now().strftime("%Y/%m/%d %H:%M:%S")}] {message}'
    with LOG_LOCK:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(APP_DIR, 0o700)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
                LOG_FILE.replace(LOG_FILE.with_suffix('.log.1'))
            with LOG_FILE.open('a', encoding='utf-8') as file:
                file.write(entry + '\n')
            os.chmod(LOG_FILE, 0o600)
        except OSError as exc:
            import sys
            print(f'SP5 Nexus could not write its log: {exc}', file=sys.stderr)
    return entry


def run_check(args, limit=3500):
    """Run a read-only diagnostic command. limit=None returns the complete output."""
    if not shutil.which(args[0]):
        return False, f'{args[0]} is not installed'
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=12, check=False,
                            stdin=subprocess.DEVNULL, errors='replace')
        result = (cp.stdout or cp.stderr).strip()
        return cp.returncode == 0, (result if limit is None else result[:limit]) or f'exited with {cp.returncode}'
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def cpu_info():
    """CPU model and core counts from /proc/cpuinfo (Python reference implementation)."""
    model, physical = '', set()
    phys_id = core_id = None
    try:
        text = Path('/proc/cpuinfo').read_text(encoding='utf-8', errors='replace')
    except OSError:
        text = ''
    for line in text.splitlines() + ['']:
        key, _, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if key == 'model name' and not model:
            model = value
        elif key == 'physical id':
            phys_id = value
        elif key == 'core id':
            core_id = value
        elif not line.strip():
            if core_id is not None:
                physical.add((phys_id, core_id))
            phys_id = core_id = None
    logical = os.cpu_count() or 0
    return {'model': model or 'Unknown CPU', 'logical_cores': logical,
            'physical_cores': len(physical) or logical}


def memory_info():
    values = {}
    try:
        for line in Path('/proc/meminfo').read_text(encoding='utf-8').splitlines():
            key, _, rest = line.partition(':')
            if key in ('MemTotal', 'MemAvailable'):
                values[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return {'total_bytes': values.get('MemTotal', 0), 'available_bytes': values.get('MemAvailable', 0)}


def parse_vulkan_devices(output):
    """Extract hardware devices from vulkaninfo --summary without assuming a GPU brand.

    vulkaninfo prints deviceType *before* deviceName inside each "GPUn:" block, so fields
    are grouped per block. The previous parser grouped by deviceName and gave each GPU the
    next device's type, which filtered out a real GPU listed before llvmpipe as "CPU".
    """
    devices = []
    current = None
    header_mode = False
    def flush(device):
        if device and device['name']:
            devices.append(device)
    for raw in output.splitlines():
        if re.fullmatch(r'\s*GPU\d+:\s*', raw):
            flush(current)
            current = {'name': '', 'driver': '', 'type': ''}
            header_mode = True
            continue
        match = re.search(r'^\s*deviceName\s*=\s*(.+?)\s*$', raw)
        if match:
            if current is not None and header_mode and not current['name']:
                current['name'] = match.group(1).strip()
            else:
                flush(current)
                current = {'name': match.group(1).strip(), 'driver': '', 'type': ''}
            continue
        if current:
            driver = re.search(r'^\s*driverName\s*=\s*(.+?)\s*$', raw)
            dtype = re.search(r'^\s*deviceType\s*=\s*(.+?)\s*$', raw)
            if driver: current['driver'] = driver.group(1).strip()
            if dtype: current['type'] = dtype.group(1).strip()
    flush(current)
    return [d for d in devices if not re.search(
        r'llvmpipe|lavapipe|software|softpipe|swrast|swiftshader|cpu',
        ' '.join((d['name'], d['driver'], d['type'])), re.I)]


def diagnose():
    """Detect AMD and NVIDIA hardware by Vulkan; NVIDIA CLI is optional."""
    nvidia_ok, nvidia_raw = run_check(['nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader'])
    # Parse the complete output: the device list is printed at the end of the summary,
    # so the previous 3500 character cut could hide every GPU on systems with many layers.
    vk_cmd_ok, vk_full = run_check(['vulkaninfo', '--summary'], limit=None)
    vk = vk_full[-6000:]
    nv_devices = []
    if nvidia_ok:
        for line in nvidia_raw.splitlines():
            parts = [part.strip() for part in line.split(',')]
            if len(parts) >= 3:
                nv_devices.append({'name': parts[0], 'driver': parts[1], 'memory': parts[2], 'vendor': 'NVIDIA'})
    vk_devices = parse_vulkan_devices(vk_full) if vk_cmd_ok else []
    gpus = []
    for device in vk_devices:
        name = device['name']
        vendor = ('AMD' if re.search(r'amd|radeon|radeonsi|\bradv\b', name + ' ' + device['driver'], re.I)
                  else 'NVIDIA' if re.search(r'nvidia|geforce|quadro|tesla', name + ' ' + device['driver'], re.I)
                  else 'Intel' if re.search(r'intel|\banv\b', name + ' ' + device['driver'], re.I)
                  else 'Other')
        match = next((n for n in nv_devices if n['name'].lower() == name.lower()), None)
        gpus.append({'name': name, 'vendor': vendor, 'driver': (match or {}).get('driver', device['driver']),
                     'memory': (match or {}).get('memory', ''), 'vulkan': True})
    # NVIDIA detection is still reported if Vulkan is absent, but readiness stays false.
    if not gpus:
        gpus = [{**n, 'vulkan': False} for n in nv_devices]
    gpu_ok = bool(gpus)
    vk_ok = bool(vk_cmd_ok and vk_devices)
    gpu_text = '\n'.join(f"{g['name']} | {g['driver']}" + (f" | {g['memory']}" if g['memory'] else '') for g in gpus)
    return {'gpu_ok': gpu_ok, 'gpu': gpu_text or 'No supported graphics device detected',
            'gpus': gpus, 'vulkan_ok': vk_ok, 'vulkan': vk,
            'cpu': cpu_info(), 'memory': memory_info(), 'source': 'python',
            'vulkan_devices': [d['name'] for d in vk_devices],
            'vulkan_message': ('Hardware Vulkan device detected' if vk_ok else
                               'No hardware Vulkan device detected, or vulkaninfo is unavailable')}

def validate_elf(path):
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError('Choose an existing input ELF file.')
    with p.open('rb') as file:
        header = file.read(20)
        if len(header) < 20 or header[:4] != b'\x7fELF':
            raise ValueError('Input is not a plain ELF. AnyPS5 does not accept an encrypted SELF container.')
        if header[4] != 2 or header[5] != 1:
            raise ValueError('A 64 bit little endian ELF is required.')
        machine = int.from_bytes(header[18:20], 'little')
        if machine == 62:
            raise ValueError('This is an ordinary x86_64 Linux ELF, not a PS5 input.')
        if machine not in (0, 21, 183):
            raise ValueError(f'Unsupported ELF machine architecture: {machine}')
    return p.resolve()


MODULE_FOLDERS = ('sce_module', 'sce_modules', 'prx')


def validate_modules(parent):
    """Upstream docs/user/USAGE.md (re-read 2026-11-07): modules live in sce_module/,
    sce_modules/ or prx/ under the module parent; prx/ may coexist with either of the
    others; sce_module/ and sce_modules/ together is an error; all three absent is an error."""
    parent = Path(parent)
    folders = [name for name in MODULE_FOLDERS if (parent / name).is_dir()]
    if not folders:
        raise ValueError('AnyPS5 needs an sce_module, sce_modules or prx folder in the module parent. None was found in ' + str(parent))
    if 'sce_module' in folders and 'sce_modules' in folders:
        raise ValueError('Both sce_module and sce_modules exist; AnyPS5 accepts only one of them.')
    return folders


def conversion_command(relinker, elf, output, *, intel=False, module_parent=None, dry_run=False):
    """Build relinker arguments using documented options only.
    --sce-module-path: "Parent of the module directories, not the module directory itself."
    --dry-run: converts in memory, writes nothing; failures return 2 (conversion check)."""
    binary = Path(relinker).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError('Set Settings → Relinker to a built, executable AnyPS5 relinker.')
    source = validate_elf(elf)
    output = Path(output).expanduser().resolve()
    if source == output:
        raise ValueError('Output cannot overwrite the source ELF.')
    parent = Path(module_parent).expanduser().resolve() if module_parent else source.parent
    if not parent.is_dir():
        raise ValueError('Module parent folder does not exist: ' + str(parent))
    validate_modules(parent)
    if output == binary.resolve():
        raise ValueError('Output cannot overwrite the relinker.')
    args = [str(binary)]
    if intel:
        args.append('--to-intel')
    if parent != source.parent:
        args.extend(['--sce-module-path', str(parent)])
    if dry_run:
        args.append('--dry-run')
    args.extend([str(source), str(output)])
    return args


# Per-game AnyPS5 runtime settings. Names and accepted values come from upstream
# docs/user/USAGE.md; AnyPS5 stops a game at start on invalid values, so Nexus validates
# them before launch and never passes an unchecked value.
def _int_range(lo, hi):
    def check(v):
        if not re.fullmatch(r'\d{1,7}', v) or not lo <= int(v) <= hi:
            raise ValueError(f'must be a whole number from {lo} to {hi}')
        return v
    return check

def _fps(v):
    if not re.fullmatch(r'\d{1,7}(\.\d{1,3})?', v) or not (float(v) == 0 or 0.5 <= float(v) <= 1_000_000):
        raise ValueError('must be 0 (no limit) or 0.5 to 1000000 with at most three decimals')
    return v

def _choice(*options):
    def check(v):
        if v not in options:
            raise ValueError('must be one of ' + ', '.join(options))
        return v
    return check

def _language(v):
    if v.lower() == 'auto':
        return 'auto'
    return _int_range(0, 30)(v)

def _resolution(v):
    m = re.fullmatch(r'(\d{1,5})[xX](\d{1,5})', v)
    if not m or int(m.group(1)) == 0 or int(m.group(2)) == 0:
        raise ValueError('must be WIDTHxHEIGHT, for example 3840x2160')
    return v

def _gpu(v):
    if len(v) > 120 or '\x00' in v:
        raise ValueError('is too long')
    return v

RUNTIME_OPTIONS = {
    # key: (environment variable, kind, validator, label)
    'fullscreen': ('ANYPS5_FULLSCREEN', bool, None, 'Start fullscreen'),
    'fps_limit': ('ANYPS5_FPS_LIMIT', str, _fps, 'Frame rate limit'),
    'present_mode': ('ANYPS5_PRESENT_MODE', str, _choice('fifo_latest_ready', 'mailbox', 'immediate'), 'Present mode'),
    'overlay': ('ANYPS5_OVERLAY', str, _choice('1', '2'), 'Performance overlay page'),
    'overlay_pos': ('ANYPS5_OVERLAY_POS', str, _choice('tl', 'tr', 'bl', 'br'), 'Overlay corner'),
    'language': ('ANYPS5_LANGUAGE', str, _language, 'Game language'),
    'volume': ('ANYPS5_VOLUME', str, _int_range(0, 100), 'Starting volume'),
    'rumble': ('ANYPS5_RUMBLE', str, _int_range(0, 100), 'Controller rumble strength'),
    'output_resolution': ('APS5_OUTPUT_RESOLUTION', str, _resolution, 'Reported output resolution'),
    'display_index': ('ANYPS5_DISPLAY_INDEX', str, _int_range(0, 63), 'Display number'),
    'mute_on_focus_loss': ('ANYPS5_MUTE_ON_FOCUS_LOSS', bool, None, 'Mute when unfocused'),
    'gpu': ('ANYPS5_GPU', str, _gpu, 'GPU (part of the device name)'),
}


def validate_runtime_settings(raw):
    """Return a clean settings dict (empty values removed). Raises ValueError naming the field."""
    clean = {}
    for key, value in (raw or {}).items():
        if key not in RUNTIME_OPTIONS:
            raise ValueError(f'Unknown game setting {key!r}')
        env, kind, check, label = RUNTIME_OPTIONS[key]
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f'{label} must be on or off')
            if value:
                clean[key] = True
            continue
        text = str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None
        if text is None:
            raise ValueError(f'{label} has an invalid value')
        if text == '':
            continue
        try:
            clean[key] = check(text)
        except ValueError as exc:
            raise ValueError(f'{label} {exc}') from None
    return clean


def runtime_env(game_settings, global_gpu=''):
    """Environment variables for one launch. Per-game GPU overrides the global GPU."""
    env = {}
    settings = validate_runtime_settings(game_settings)
    if global_gpu.strip() and 'gpu' not in settings:
        settings['gpu'] = _gpu(global_gpu.strip())
    for key, value in settings.items():
        env[RUNTIME_OPTIONS[key][0]] = '1' if value is True else str(value)
    return env


RELINKER_EXIT_CODES = {0: 'conversion succeeded', 1: 'invalid arguments', 2: 'conversion failed (see the log for the relinker error)'}


def relinker_exit_message(code):
    if code is None:
        return 'did not report an exit code'
    if code < 0:
        return f'was stopped by signal {-code}'
    return RELINKER_EXIT_CODES.get(code, f'exited with code {code}')


def prepare_runtime(output, libraries, resource_folder=None):
    output = Path(output).expanduser().resolve()
    if not output.is_file():
        raise ValueError('Converted output does not exist.')
    source_libs = Path(libraries).expanduser()
    prx = list(source_libs.glob('*.prx')) if source_libs.is_dir() else []
    if not prx:
        raise ValueError('No built AnyPS5 *.prx runtime libraries found at the configured path.')
    target = output.parent / 'libs'
    target.mkdir(parents=True, exist_ok=True)
    if source_libs.resolve() == target.resolve():
        raise ValueError('Runtime source and destination must be different.')
    for lib in prx:
        shutil.copy2(lib, target / lib.name)
    if resource_folder:
        source = Path(resource_folder).expanduser().resolve()
        if not source.is_dir():
            raise ValueError('Resource folder does not exist.')
        dst = output.parent / 'app0'
        if source == dst or source in dst.parents or dst in source.parents:
            raise ValueError('Resource folder overlaps destination.')
        for root, dirs, files in os.walk(source, followlinks=False):
            rel = Path(root).relative_to(source)
            (dst / rel).mkdir(parents=True, exist_ok=True)
            if any((Path(root) / d).is_symlink() for d in dirs):
                raise ValueError('Resource folder contains a symbolic link; copy refused.')
            for filename in files:
                src = Path(root) / filename
                if src.is_symlink():
                    raise ValueError('Resource folder contains a symbolic link; copy refused.')
                shutil.copy2(src, dst / rel / filename)
    output.chmod(output.stat().st_mode | 0o111)
    return len(prx)


def split_launch_args(text):
    """Turn the user's launch argument text into a structured list. No shell is ever used."""
    import shlex
    try:
        args = shlex.split(str(text or ''))
    except ValueError as exc:
        raise ValueError(f'Launch arguments are not valid: {exc}') from exc
    if len(args) > 64 or any(len(a) > 1024 or '\x00' in a for a in args):
        raise ValueError('Launch arguments are too long.')
    return args


def launch_command(output, gpu_preference='', launch_args='', game_settings=None):
    output = Path(output).expanduser().resolve()
    if not output.is_file() or not os.access(output, os.X_OK):
        raise ValueError('The converted executable is missing or not executable.')
    env = os.environ.copy()
    env.update(runtime_env(game_settings or {}, gpu_preference))
    return [str(output), *split_launch_args(launch_args)], output.parent, env


def detect_relinker():
    for candidate in (DEFAULT_RELINKER, LEGACY_ROOT / 'build/core/relinker/relinker'):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ''


def defaults():
    return {'relinker': detect_relinker(), 'libs':str(DEFAULT_LIBS if DEFAULT_LIBS.is_dir() else (LEGACY_ROOT / 'build/core/libs/libs' if (LEGACY_ROOT / 'build/core/libs/libs').is_dir() else DEFAULT_LIBS)), 'gpu':'', 'intel': False, 'output_dir':str(Path.home()/'Games'/'AnyPS5'),
            'theme': 'nexus', 'motion': 'full', 'game_colors': True,
            'pad_enabled': True, 'pad_deadzone': 35, 'pad_repeat_ms': 220, 'pad_confirm': 'south', 'pad_preferred': ''}
