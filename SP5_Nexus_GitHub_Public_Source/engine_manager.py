"""Install and verify upstream AnyPS5 from source on Linux.

This installer never executes third party install scripts or requests sudo.
Third party source is obtained directly from the named upstream repository.
"""
from __future__ import annotations
import os
import signal
import shutil
import subprocess
from pathlib import Path

UPSTREAM = 'https://github.com/boykopovar/AnyPS5.git'
DEFAULT_SOURCE = Path(__file__).resolve().parent / 'engine' / 'AnyPS5'
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
    commands += [
        (['cmake','-S','.','-B','build','-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
          '-DCMAKE_C_COMPILER=gcc','-DCMAKE_CXX_COMPILER=g++'],source),
        (['cmake','--build','build','--parallel','4'],source),
        (['cmake','--build','build','--target','libs','--parallel','4'],source),
    ]
    return commands

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
        try:
            probe = subprocess.run([current['relinker'], '--help'], capture_output=True, text=True, timeout=12)
            if probe.returncode == 0 and 'Usage: relinker' in (probe.stdout + probe.stderr):
                progress('Existing working engine preserved and reused')
                return current
        except (OSError, subprocess.TimeoutExpired):
            pass
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
    probe = subprocess.run([result['relinker'], '--help'], capture_output=True, text=True, timeout=12)
    if probe.returncode != 0 or 'Usage: relinker' not in (probe.stdout + probe.stderr):
        raise RuntimeError('Relinker executable did not pass its help check')
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
