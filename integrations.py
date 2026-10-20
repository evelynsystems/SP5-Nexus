"""Optional Linux gaming integrations. Never modifies discs or installs packages."""
import json
import os
import shutil
import subprocess
from pathlib import Path

PROJECTS = {
    'controllers': {'name':'SDL GameControllerDB', 'url':'https://github.com/mdqinc/SDL_GameControllerDB', 'license':'Zlib', 'note':'Controller mappings for applications using SDL; Nexus detects input devices without claiming they work in games.'},
    'devices': {'name':'pyudev', 'url':'https://github.com/pyudev/pyudev', 'license':'LGPL', 'note':'Optional Linux device monitoring; read only disc and controller discovery is built in.'},
    'gamescope': {'name':'Gamescope', 'url':'https://github.com/ValveSoftware/gamescope', 'license':'BSD style', 'note':'Optional game compositor. Used only when installed and explicitly enabled.'},
    'mangohud': {'name':'MangoHud', 'url':'https://github.com/flightlessmango/MangoHud', 'license':'MIT', 'note':'Optional game performance overlay. Used only when installed and explicitly enabled.'},
    'artwork': {'name':'IGDB Python API', 'url':'https://github.com/twitchtv/igdb-api-python', 'license':'See upstream', 'note':'Game artwork service requiring the user’s own API credentials. No credentials are bundled or collected.'},
    'engine': {'name':'AnyPS5', 'url':'https://github.com/boykopovar/AnyPS5', 'license':'GPL 2.0', 'note':'Experimental executable relinker; commercial game support is not implied.'},
}

def _run(cmd, timeout=7):
    try:
        p=subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.stdout if p.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''

def controllers():
    # Kernel-reported input devices, not a promise of application-level support.
    root=Path('/sys/class/input')
    results=[]
    for device in sorted(root.glob('event*')) if root.exists() else []:
        namepath=device/'device'/'name'
        caps=device/'device'/'capabilities'/'ev'
        try:
            name=namepath.read_text(errors='replace').strip()
            events=int(caps.read_text().strip().replace(' ',''),16) if caps.exists() else 0
        except (OSError, ValueError):
            continue
        if name and ((events & (1<<3)) or any(x in name.lower() for x in ('controller','gamepad','joystick','dualshock','dualsense'))):
            results.append({'name':name[:160], 'device':'/dev/input/'+device.name})
    return results[:32]

def optical_discs():
    if not shutil.which('lsblk'):
        return []
    raw=_run(['lsblk','-J','-o','NAME,PATH,TYPE,SIZE,MODEL,FSTYPE,LABEL,MOUNTPOINTS'],8)
    if not raw:return []
    try: nodes=json.loads(raw).get('blockdevices',[])
    except (ValueError,TypeError):return []
    return [{'name':x.get('name'),'path':x.get('path'),'model':x.get('model'), 'size':x.get('size'), 'filesystem':x.get('fstype'), 'label':x.get('label'), 'mountpoints':x.get('mountpoints') or [], 'status':'Detected optical drive; protected PS5 discs cannot be converted directly'} for x in nodes if x.get('type')=='rom']

def status():
    return {'tools':{name:bool(shutil.which(name)) for name in ('gamescope','mangohud','vulkaninfo','lsblk','udisksctl')},
            'controllers':controllers(),'discs':optical_discs(),'projects':PROJECTS}

def wrap_launch(command, cwd, environment, *, gamescope=False, mangohud=False):
    """Wrapper applies to already converted games only; optional and explicit."""
    cmd=list(command)
    env=dict(environment)
    if mangohud:
        if not shutil.which('mangohud'):raise ValueError('MangoHud is not installed')
        cmd=['mangohud','--dlsym',*cmd]
    if gamescope:
        if not shutil.which('gamescope'):raise ValueError('Gamescope is not installed')
        cmd=['gamescope','--',*cmd]
    return cmd,cwd,env
