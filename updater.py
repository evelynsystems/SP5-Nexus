"""Opt-in update checks and staged AnyPS5 builds; never overwrite a working engine."""
from __future__ import annotations
import os
import re
import subprocess
import shutil
from pathlib import Path
import engine_manager as E

SHA = re.compile(r'^[a-f0-9]{40}$')

def run(args, timeout=20):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, stdin=subprocess.DEVNULL, timeout=timeout,
                       env={**os.environ, 'GIT_TERMINAL_PROMPT':'0'})
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip()[:250] or 'Command failed')
    return p.stdout.strip()

def source_from_relinker(path):
    p = Path(path).expanduser()
    if p.name != 'relinker' or tuple(p.parts[-4:]) != ('build','core','relinker','relinker'):
        return None
    src = p.parents[3]
    return src if (src / '.git').exists() else None

def check_engine(relinker=''):
    source = source_from_relinker(relinker) or E.DEFAULT_SOURCE
    local = E.git_identity(source) if (source / '.git').exists() else None
    origin = (local or {}).get('origin','')
    if local and origin.removesuffix('.git').rstrip('/') != E.UPSTREAM.removesuffix('.git').rstrip('/'):
        return {'status':'untrusted','message':'Engine repository origin does not match official AnyPS5; updating disabled.', 'source':str(source)}
    remote = run(['git','ls-remote',E.UPSTREAM,'HEAD'], timeout=30).split('\t')[0]
    if not SHA.fullmatch(remote):
        raise RuntimeError('GitHub did not return a valid commit identifier')
    current = (local or {}).get('commit','')
    return {'status':'current' if current == remote else 'available' if current else 'not_installed',
            'source':str(source), 'current':current[:12], 'latest':remote[:12],
            'commit':remote,
            'message': 'Engine source is current' if current == remote else
                       'A different official engine commit is available' if current else
                       'No official engine checkout detected'}

def system_tools():
    # Read only. These are managed by the distribution, never by git clone.
    tools = {'Gamescope':'gamescope','MangoHud':'mangohud','GameMode':'gamemoderun',
             'Vulkan utilities':'vulkaninfo','Git':'git'}
    return {name: bool(shutil.which(exe)) for name,exe in tools.items()}

def build_new_engine(progress, configured_relinker, stage=None, on_process=None, cancelled=None):
    info = check_engine(configured_relinker)
    if info['status'] == 'untrusted':
        raise RuntimeError(info['message'])
    if info['status'] == 'current':
        progress('Official source is already current. Existing engine preserved.')
        return None
    existing = source_from_relinker(configured_relinker) or E.DEFAULT_SOURCE
    location = existing.parent / 'nexus_updates'
    location.mkdir(parents=True, exist_ok=True)
    # Unique commit-qualified folder, not a replacement for the old engine.
    dest = location / ('AnyPS5_' + info['commit'][:12])
    if dest.exists() and not (dest / '.git').exists():
        raise RuntimeError('Update destination exists but is not a Git checkout; no files changed')
    if dest.exists():
        git = E.git_identity(dest)
        if not git or git['origin'].removesuffix('.git').rstrip('/') != E.UPSTREAM.removesuffix('.git').rstrip('/'):
            raise RuntimeError('Staged update has unexpected repository origin')
        if git['commit'] != info['commit']:
            raise RuntimeError('Staged folder contains a different commit; refusing to modify it')
    result = E.install(progress, source=dest, update=False, stage=stage,
                       on_process=on_process, cancelled=cancelled)
    # An update that races with upstream advancement must never silently install an unexpected revision.
    identity = E.git_identity(dest)
    if not identity or identity['commit'] != info['commit']:
        raise RuntimeError('Built revision differs from the checked revision; old engine preserved')
    return result
