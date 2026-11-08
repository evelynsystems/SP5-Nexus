"""Install and verify upstream AnyPS5 from source on Linux.

This installer never executes third party install scripts or requests sudo.
Third party source is obtained directly from the named upstream repository.
"""
from __future__ import annotations
import os
import re
import signal
import shutil
import subprocess
from pathlib import Path

UPSTREAM = 'https://github.com/boykopovar/AnyPS5.git'
from backend import ENGINE_ROOT as DEFAULT_SOURCE  # writable location; see backend.engine_root()
LEGACY_SOURCE = Path.home() / 'Documents' / 'AnyPS5'
PACKAGES = ('git', 'cmake', 'ninja-build', 'build-essential', 'binutils',
            'libx11-dev', 'libxext-dev', 'libpipewire-0.3-dev',
            'libpulse-dev', 'libasound2-dev', 'libudev-dev')

def paths(source=DEFAULT_SOURCE):
    source = Path(source).expanduser().resolve()
    return source / 'build/core/relinker/relinker', source / 'build/core/libs/libs'

def status(source=DEFAULT_SOURCE):
    binary, libs = paths(source)
    return {
        'source': str(Path(source).expanduser()),
        'source_present': (Path(source).expanduser() / 'CMakeLists.txt').is_file(),
        'checkout': (Path(source).expanduser() / '.git').exists(),
        'relinker': str(binary),
        'relinker_ready': binary.is_file() and os.access(binary, os.X_OK) and binary.stat().st_size > 0,
        'libs': str(libs),
        'libs_count': len(list(libs.glob('*.prx'))) if libs.is_dir() else 0,
        'missing_commands': [c for c in ('git','cmake','ninja','gcc','g++') if not shutil.which(c)],
    }

def plan(source=DEFAULT_SOURCE, update=False):
    source = Path(source).expanduser().resolve()
    if source.exists() and not (source / '.git').exists():
        raise ValueError(f'Existing folder is not a Git checkout: {source}. No files changed.')
    if (source / '.git').exists():
        info = git_identity(source)
        if not info or info['origin'].removesuffix('.git').rstrip('/') != UPSTREAM.removesuffix('.git').rstrip('/'):
            raise ValueError('Engine checkout has an unexpected Git origin; refusing to update or build.')
    if not source.exists():
        commands = [(['git','clone','--recursive','--progress',UPSTREAM,str(source)], source.parent)]
    else:
        commands = []
        if update:
            commands.append((['git','fetch','origin'],source))
            commands.append((['git','pull','--ff-only'],source))
        commands.append((['git','submodule','update','--init','--recursive','--progress'],source))
    jobs = str(max(1, min(os.cpu_count() or 4, 16)))
    commands += [
        (['cmake','-S','.','-B','build','-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
          '-DCMAKE_C_COMPILER=gcc','-DCMAKE_CXX_COMPILER=g++'],source),
        (['cmake','--build','build','--parallel',jobs],source),
        (['cmake','--build','build','--target','libs','--parallel',jobs],source),
    ]
    return commands

def probe_relinker(path):
    """Upstream docs/user/USAGE.md: 'There is no --help flag; invoking relinker without
    arguments prints the usage syntax and exits with an error.' The previous check ran
    `relinker --help` and required exit code 0, which a correct build can never pass.
    Returns (ok, message)."""
    binary = Path(path).expanduser()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return False, 'Relinker is missing or not executable'
    version = relinker_version(binary)   # current builds: --version (exit 0)
    if version:
        return True, f'Relinker {version} responded to --version'
    try:
        probe = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL, errors='replace')
    except subprocess.TimeoutExpired:
        return False, 'Relinker did not exit within 10 seconds'
    except OSError as exc:
        return False, f'Relinker could not run: {exc}'
    text = (probe.stdout + '\n' + probe.stderr).lower()
    if probe.returncode < 0:
        return False, 'Relinker crashed while printing its usage'
    if 'relinker' in text and ('usage' in text or '<input' in text):
        return True, 'Relinker responded with its usage text'
    return False, 'Relinker ran but did not print the expected usage text'


def install(progress, source=DEFAULT_SOURCE, update=False, stage=None, on_process=None, cancelled=None):
    if os.name != 'posix':
        raise RuntimeError('This automatic builder targets Linux.')
    missing = status(source)['missing_commands']
    if missing:
        raise RuntimeError('Missing build tools: ' + ', '.join(missing) +
                           '. Install prerequisites shown in the Engine Manager.')
    if update:
        raise RuntimeError('Safe engine update staging is not implemented yet. Existing engine preserved. Use a separate source checkout for testing updates.')
    current = status(source)
    if current['relinker_ready'] and current['libs_count']:
        ok, message = probe_relinker(current['relinker'])
        if ok:
            progress('Existing working engine preserved and reused')
            return current
        progress('Existing relinker check: ' + message)
    commands = plan(source, False)
    Path(source).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    stages = ['Downloading AnyPS5 source' if args[:2] == ['git','clone'] else
              'Updating source' if args[:2] in (['git','fetch'], ['git','pull']) else
              'Downloading engine dependencies' if args[:2] == ['git','submodule'] else
              'Configuring CMake' if args[:2] == ['cmake','-S'] else
              'Compiling runtime libraries' if 'libs' in args else 'Compiling engine'
              for args, _ in commands]
    for index, (args, cwd) in enumerate(commands):
        if stage: stage(stages[index], index + 1, len(commands))
        progress('Running: ' + ' '.join(args))
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        with subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,text=True,start_new_session=True,
                              errors='replace',bufsize=1) as process:
            if on_process: on_process(process, True)
            try:
                for line in process.stdout:
                    if cancelled and cancelled():
                        os.killpg(process.pid, signal.SIGTERM)
                        raise RuntimeError('Engine installation cancelled')
                    if line.strip(): progress(line.rstrip()[-1000:])
                result = process.wait(timeout=10)
            finally:
                if on_process: on_process(process, False)
        if result:
            raise RuntimeError('Build command failed with exit code ' + str(result) +
                               ': ' + ' '.join(args))
    if stage: stage('Verifying compiled engine', len(commands), len(commands))
    result = status(source)
    if not result['relinker_ready'] or not result['libs_count']:
        raise RuntimeError('Compilation ended, but relinker or PRX libraries are missing.')
    ok, message = probe_relinker(result['relinker'])
    if not ok:
        raise RuntimeError('Relinker executable did not pass its usage check: ' + message)
    return result


def git_identity(source=DEFAULT_SOURCE):
    """Return checkout origin and commit; never alter an unknown checkout."""
    src = Path(source).expanduser().resolve()
    if not (src / '.git').exists():
        return None
    try:
        origin = subprocess.run(['git', '-C', str(src), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=8)
        commit = subprocess.run(['git', '-C', str(src), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Unable to validate engine Git origin: {exc}') from exc
    return {'origin': origin.stdout.strip() if origin.returncode == 0 else '',
            'commit': commit.stdout.strip() if commit.returncode == 0 else ''}


_VERSION_CACHE = {}


def relinker_version(path):
    """`relinker --version` prints the build revision from `git describe` and exits 0
    (upstream docs/user/USAGE.md, re-read 2026-11-07). Older builds without --version
    return ''. Cached per file modification time."""
    binary = Path(path).expanduser()
    try:
        st = binary.stat()
    except OSError:
        return ''
    key = (str(binary), st.st_mtime_ns, st.st_size)
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    version = ''
    if os.access(binary, os.X_OK):
        try:
            cp = subprocess.run([str(binary), '--version'], capture_output=True, text=True, timeout=10,
                                stdin=subprocess.DEVNULL, errors='replace')
            first = (cp.stdout.strip().splitlines() or [''])[0].strip()
            # A git-describe revision: one token of [A-Za-z0-9._+-] that contains a digit.
            if cp.returncode == 0 and len(first) < 120 and re.fullmatch(r'[A-Za-z0-9._+-]*\d[A-Za-z0-9._+-]*', first):
                version = first
        except (OSError, subprocess.TimeoutExpired):
            pass
    _VERSION_CACHE[key] = version
    return version


def list_engines(configured_relinker='', configured_libs=''):
    """Engines installed on this PC: the default checkout, the legacy location, the checkout
    the configured relinker belongs to, and staged updates in nexus_updates/AnyPS5_<commit>."""
    candidates = [Path(DEFAULT_SOURCE), LEGACY_SOURCE]
    rel = Path(configured_relinker).expanduser() if configured_relinker else None
    if rel and rel.name == 'relinker' and len(rel.resolve().parents) > 3:
        candidates.append(rel.resolve().parents[3])
    for base in list(candidates):
        updates = Path(base).expanduser().parent / 'nexus_updates'
        if updates.is_dir():
            candidates.extend(sorted(updates.glob('AnyPS5_*')))
    seen, engines = set(), []
    for source in candidates:
        source = Path(source).expanduser()
        try:
            key = source.resolve()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        binary, libs = paths(source)
        if not binary.is_file():
            continue
        ready = os.access(binary, os.X_OK)
        engines.append({'name': source.name, 'source': str(source), 'relinker': str(binary), 'libs': str(libs),
                        'ready': ready, 'libs_count': len(list(libs.glob('*.prx'))) if libs.is_dir() else 0,
                        'version': relinker_version(binary) if ready else '',
                        'configured': bool(rel) and binary.resolve() == rel.resolve()})
    # A relinker configured outside a standard AnyPS5 checkout is still an installed engine.
    if rel and rel.is_file() and not any(e['configured'] for e in engines):
        ready = os.access(rel, os.X_OK)
        libs = Path(configured_libs).expanduser() if configured_libs else None
        engines.append({'name': 'Configured relinker', 'source': str(rel.parent), 'relinker': str(rel),
                        'libs': str(libs or ''), 'ready': ready,
                        'libs_count': len(list(libs.glob('*.prx'))) if libs and libs.is_dir() else 0,
                        'version': relinker_version(rel) if ready else '', 'configured': True})
    return engines
