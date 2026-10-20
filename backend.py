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
ENGINE_ROOT = Path(__file__).resolve().parent / 'engine' / 'AnyPS5'
LEGACY_ROOT = Path.home() / 'Documents' / 'AnyPS5'
DEFAULT_RELINKER = ENGINE_ROOT / 'build/core/relinker/relinker'
DEFAULT_LIBS = ENGINE_ROOT / 'build/core/libs/libs'


DATA_LOCK = threading.RLock()

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

def update_json(path, default, modifier):
    with DATA_LOCK:
        data = load_json(path, default)
        result = modifier(data)
        save_json(path, data)
        return result

def log(message):
    APP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(APP_DIR, 0o700)
    entry = f'[{datetime.now().strftime("%Y/%m/%d %H:%M:%S")}] {message}'
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
        LOG_FILE.replace(LOG_FILE.with_suffix('.log.1'))
    with LOG_FILE.open('a', encoding='utf-8') as file:
        file.write(entry + '\n')
    os.chmod(LOG_FILE, 0o600)
    return entry


def run_check(args):
    if not shutil.which(args[0]):
        return False, f'{args[0]} is not installed'
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=12, check=False)
        result = (cp.stdout or cp.stderr).strip()
        return cp.returncode == 0, result[:3500] or f'exited with {cp.returncode}'
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def parse_vulkan_devices(output):
    """Extract hardware devices from vulkaninfo --summary without assuming a GPU brand."""
    devices = []
    current = None
    for raw in output.splitlines():
        match = re.search(r'^\s*deviceName\s*=\s*(.+?)\s*$', raw)
        if match:
            if current:
                devices.append(current)
            current = {'name': match.group(1).strip(), 'driver': '', 'type': ''}
            continue
        if current:
            driver = re.search(r'^\s*driverName\s*=\s*(.+?)\s*$', raw)
            dtype = re.search(r'^\s*deviceType\s*=\s*(.+?)\s*$', raw)
            if driver: current['driver'] = driver.group(1).strip()
            if dtype: current['type'] = dtype.group(1).strip()
    if current:
        devices.append(current)
    return [d for d in devices if not re.search(
        r'llvmpipe|lavapipe|software|softpipe|swrast|swiftshader|cpu',
        ' '.join((d['name'], d['driver'], d['type'])), re.I)]


def diagnose():
    """Detect AMD and NVIDIA hardware by Vulkan; NVIDIA CLI is optional."""
    nvidia_ok, nvidia_raw = run_check(['nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader'])
    vk_cmd_ok, vk = run_check(['vulkaninfo', '--summary'])
    nv_devices = []
    if nvidia_ok:
        for line in nvidia_raw.splitlines():
            parts = [part.strip() for part in line.split(',')]
            if len(parts) >= 3:
                nv_devices.append({'name': parts[0], 'driver': parts[1], 'memory': parts[2], 'vendor': 'NVIDIA'})
    vk_devices = parse_vulkan_devices(vk) if vk_cmd_ok else []
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


def validate_modules(parent):
    parent = Path(parent)
    folders = [name for name in ('sce_module', 'sce_modules', 'prx') if (parent / name).is_dir()]
    if not folders:
        raise ValueError('No sce_module, sce_modules, or prx directory exists beside the ELF. Select the correct module parent.')
    if 'sce_module' in folders and 'sce_modules' in folders:
        raise ValueError('Both sce_module and sce_modules exist; AnyPS5 requires only one of those.')
    return folders


def conversion_command(relinker, elf, output, *, intel=False, module_parent=None):
    binary = Path(relinker).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError('Set Settings → Relinker to a built, executable AnyPS5 relinker.')
    source = validate_elf(elf)
    output = Path(output).expanduser().resolve()
    if source == output:
        raise ValueError('Output cannot overwrite the source ELF.')
    parent = Path(module_parent).expanduser().resolve() if module_parent else source.parent
    validate_modules(parent)
    if output == binary.resolve():
        raise ValueError('Output cannot overwrite the relinker.')
    args = [str(binary)]
    if intel:
        args.append('--to-intel')
    if module_parent:
        args.extend(['--sce-module-path', str(parent)])
    args.extend([str(source), str(output)])
    return args


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
            for filename in files:
                src = Path(root) / filename
                if src.is_symlink():
                    raise ValueError('Resource folder contains a symbolic link; copy refused.')
                shutil.copy2(src, dst / rel / filename)
    output.chmod(output.stat().st_mode | 0o111)
    return len(prx)


def launch_command(output, gpu_preference=''):
    output = Path(output).expanduser().resolve()
    if not output.is_file() or not os.access(output, os.X_OK):
        raise ValueError('The converted executable is missing or not executable.')
    env = os.environ.copy()
    if gpu_preference.strip():
        env['ANYPS5_GPU'] = gpu_preference.strip()
    return [str(output)], output.parent, env


def detect_relinker():
    for candidate in (DEFAULT_RELINKER, LEGACY_ROOT / 'build/core/relinker/relinker'):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return ''


def defaults():
    return {'relinker': detect_relinker(), 'libs':str(DEFAULT_LIBS if DEFAULT_LIBS.is_dir() else (LEGACY_ROOT / 'build/core/libs/libs' if (LEGACY_ROOT / 'build/core/libs/libs').is_dir() else DEFAULT_LIBS)), 'gpu':'', 'intel': False, 'output_dir':str(Path.home()/'Games'/'AnyPS5')}
