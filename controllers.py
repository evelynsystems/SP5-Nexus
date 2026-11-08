"""Game controller detection, battery reporting and navigation input for SP5 Nexus.

Detection reads /proc/bus/input/devices (bus 0003 = USB, 0005 = Bluetooth) and battery
level from /sys/class/power_supply (the kernel hid-playstation driver exposes
ps-controller-battery-<MAC> for DualSense and DualShock 4). Input is read from the
controller's /dev/input/event* node with the standard library only: no extra packages, no
root, no writes to the device. Ubuntu grants the logged-in user access to game controllers.

The reader turns raw events into navigation actions (up, down, left, right, confirm, back,
options, menu, prev, next) and hands them to a callback. Rust is not used here: the work is
a few events per second, so Python is sufficient and keeps the Rust backend unchanged.
"""
from __future__ import annotations
import errno
import fcntl
import os
import re
import select
import struct
import threading
import time
from pathlib import Path

BUS = {'0003': 'USB', '0005': 'Bluetooth'}
KNOWN = {
    ('054c', '0ce6'): 'DualSense',
    ('054c', '0df2'): 'DualSense Edge',
    ('054c', '05c4'): 'DualShock 4',
    ('054c', '09cc'): 'DualShock 4',
    ('054c', '0ba0'): 'DualShock 4 USB adapter',
}
VENDORS = {'054c': 'Sony', '045e': 'Microsoft', '057e': 'Nintendo', '28de': 'Valve', '046d': 'Logitech', '2dc8': '8BitDo'}

EV_KEY, EV_ABS = 0x01, 0x03
BTN_GAMEPAD = 0x130
BUTTONS = {0x130: 'south', 0x131: 'east', 0x133: 'north', 0x134: 'west', 0x136: 'l1', 0x137: 'r1',
           0x138: 'l2', 0x139: 'r2', 0x13a: 'create', 0x13b: 'options', 0x13c: 'ps', 0x13d: 'l3', 0x13e: 'r3',
           0x220: 'up', 0x221: 'down', 0x222: 'left', 0x223: 'right'}
BUTTON_LABELS = {'south': 'Cross / A', 'east': 'Circle / B', 'north': 'Triangle / Y', 'west': 'Square / X',
                 'l1': 'L1 / LB', 'r1': 'R1 / RB', 'l2': 'L2 / LT', 'r2': 'R2 / RT', 'create': 'Create / Back',
                 'options': 'Options / Start', 'ps': 'PS / Guide', 'l3': 'L3', 'r3': 'R3',
                 'up': 'D-pad up', 'down': 'D-pad down', 'left': 'D-pad left', 'right': 'D-pad right'}
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11
EVENT = struct.Struct('llHHi')          # struct input_event on 64-bit Linux (24 bytes)
ABSINFO = struct.Struct('6i')           # value, minimum, maximum, fuzz, flat, resolution


def eviocgabs(code):
    return (2 << 30) | (ABSINFO.size << 16) | (ord('E') << 8) | (0x40 + code)


def parse_input_devices(text):
    """Parse /proc/bus/input/devices into device dicts."""
    devices = []
    for block in text.strip().split('\n\n'):
        d = {'handlers': [], 'key_bits': ''}
        for line in block.splitlines():
            tag, _, rest = line.partition(': ')
            if tag == 'I':
                for part in rest.split():
                    k, _, v = part.partition('=')
                    d[k.lower()] = v.lower()
            elif tag == 'N':
                d['name'] = rest.partition('=')[2].strip().strip('"')
            elif tag == 'U':
                d['uniq'] = rest.partition('=')[2].strip().lower()
            elif tag == 'H':
                d['handlers'] = rest.partition('=')[2].split()
            elif tag == 'B' and rest.startswith('KEY='):
                d['key_bits'] = rest[4:].strip()
        if d.get('name'):
            devices.append(d)
    return devices


def has_key(bitmap, code):
    words = bitmap.split()
    if not words:
        return False
    index = len(words) - 1 - code // 64
    if index < 0:
        return False
    try:
        return bool(int(words[index], 16) >> (code % 64) & 1)
    except ValueError:
        return False


def batteries(power_root='/sys/class/power_supply'):
    """Controller batteries exposed by the kernel, keyed by lower-case MAC address."""
    out = {}
    root = Path(power_root)
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        m = re.match(r'(ps-controller|sony_controller)_?-?battery[_-]?(.*)', entry.name)
        if not m:
            continue
        def read(name):
            try:
                return (entry / name).read_text().strip()
            except OSError:
                return ''
        capacity = read('capacity')
        out[m.group(2).lower().replace('_', ':')] = {
            'capacity': int(capacity) if capacity.isdigit() else None,
            'status': read('status') or 'Unknown',
        }
    return out


def detect(proc_path='/proc/bus/input/devices', power_root='/sys/class/power_supply'):
    """Connected gamepads (devices with gamepad buttons and an event node)."""
    try:
        text = Path(proc_path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    bats = batteries(power_root)
    pads = []
    devices = parse_input_devices(text)
    claimed = {d.get('uniq', '') for d in devices} & set(bats)
    for d in devices:
        event = next((h for h in d['handlers'] if h.startswith('event')), None)
        if not event or not (has_key(d['key_bits'], BTN_GAMEPAD) or any(h.startswith('js') for h in d['handlers'])):
            continue
        name_l = d['name'].lower()
        if 'motion sensors' in name_l or 'touchpad' in name_l:
            continue
        vendor, product = d.get('vendor', ''), d.get('product', '')
        model = KNOWN.get((vendor, product))
        battery = bats.get(d.get('uniq', ''))
        # Some kernels name the battery without a MAC that matches Uniq; use it only when it is
        # the single unclaimed Sony battery and this pad has no matching one of its own.
        unclaimed = [v for k, v in bats.items() if k not in claimed]
        if battery is None and vendor == '054c' and not d.get('uniq') and len(unclaimed) == 1:
            battery = unclaimed[0]
        pads.append({
            'name': d['name'], 'model': model or d['name'], 'vendor': VENDORS.get(vendor, vendor),
            'vendor_id': vendor, 'product_id': product, 'connection': BUS.get(d.get('bus', ''), 'Other'),
            'event': '/dev/input/' + event, 'id': f"{vendor}:{product}:{d.get('uniq') or event}",
            'dualsense': model in ('DualSense', 'DualSense Edge'),
            'battery': battery, 'readable': os.access('/dev/input/' + event, os.R_OK),
        })
    return pads


class Navigator:
    """Turns raw evdev events into navigation actions with dead zone and auto-repeat."""

    def __init__(self, deadzone=0.35, repeat_ms=220, confirm='south', absinfo=None):
        self.deadzone = max(0.0, min(0.6, deadzone))
        self.repeat = max(0.1, repeat_ms / 1000)
        self.confirm, self.back = ('south', 'east') if confirm == 'south' else ('east', 'south')
        self.absinfo = absinfo or {}
        self.axes = {ABS_X: 0.0, ABS_Y: 0.0, ABS_HAT0X: 0, ABS_HAT0Y: 0}
        self.held, self.next_at = None, 0.0
        self.last_button = ''

    def _norm(self, code, value):
        lo, hi = self.absinfo.get(code, (0, 255))
        if hi <= lo:
            return 0.0
        return max(-1.0, min(1.0, (value - lo) / (hi - lo) * 2 - 1))

    def direction(self):
        hx, hy = self.axes[ABS_HAT0X], self.axes[ABS_HAT0Y]
        if hx or hy:
            return 'left' if hx < 0 else 'right' if hx > 0 else 'up' if hy < 0 else 'down'
        x, y = self.axes[ABS_X], self.axes[ABS_Y]
        if max(abs(x), abs(y)) < self.deadzone:
            return None
        return ('left' if x < 0 else 'right') if abs(x) > abs(y) else ('up' if y < 0 else 'down')

    def feed(self, etype, code, value, now):
        """Returns a list of actions produced by one event."""
        actions = []
        if etype == EV_KEY and code in BUTTONS and value == 1:
            name = BUTTONS[code]
            self.last_button = name
            mapping = {self.confirm: 'confirm', self.back: 'back', 'options': 'options', 'ps': 'menu',
                       'l1': 'prev', 'r1': 'next', 'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
                       'north': 'details', 'create': 'menu'}
            if name in mapping:
                actions.append(mapping[name])
        elif etype == EV_ABS and code in self.axes:
            self.axes[code] = value if code in (ABS_HAT0X, ABS_HAT0Y) else self._norm(code, value)
            d = self.direction()
            if d != self.held:
                self.held = d
                if d:
                    actions.append(d)
                    self.next_at = now + self.repeat * 1.6
        return actions

    def tick(self, now):
        """Auto-repeat while a direction is held."""
        if self.held and now >= self.next_at:
            self.next_at = now + self.repeat
            return [self.held]
        return []


class ControllerService:
    """Background reader for the preferred (or first) connected gamepad. Thread-safe status."""

    def __init__(self, on_action, settings=lambda: {}, paused=lambda: False,
                 detect_fn=detect, open_fn=None):
        self.on_action, self.settings, self.paused = on_action, settings, paused
        self.detect_fn, self.open_fn = detect_fn, open_fn or (lambda path: os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.active, self.pads, self.events_seen, self.last_error = None, [], 0, ''
        self.last_button, self.notices = '', []
        self.thread = None

    def status(self):
        with self.lock:
            return {'running': bool(self.thread and self.thread.is_alive()), 'active': self.active,
                    'pads': list(self.pads), 'events_seen': self.events_seen, 'last_button': self.last_button,
                    'last_button_label': BUTTON_LABELS.get(self.last_button, ''), 'error': self.last_error}

    def take_notices(self):
        with self.lock:
            out, self.notices = self.notices, []
            return out

    def _notice(self, text):
        with self.lock:
            self.notices.append(text)
            del self.notices[:-20]

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name='sp5-controller', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _choose(self, pads):
        preferred = (self.settings() or {}).get('pad_preferred', '')
        usable = [p for p in pads if p['readable']]
        return next((p for p in usable if preferred and preferred in (p['id'], p['name'])), usable[0] if usable else None)

    def _absinfo(self, fd):
        info = {}
        for code in (ABS_X, ABS_Y):
            try:
                buf = fcntl.ioctl(fd, eviocgabs(code), bytes(ABSINFO.size))
                _, lo, hi, *_ = ABSINFO.unpack(buf)
                info[code] = (lo, hi)
            except OSError:
                pass
        return info

    def _run(self):
        fd, nav, last_scan = None, None, 0.0
        buf = b''
        while not self.stop_event.is_set():
            now = time.monotonic()
            if fd is None and now - last_scan >= 2:
                last_scan = now
                pads = self.detect_fn()
                pad = self._choose(pads)
                with self.lock:
                    self.pads = pads
                if pad:
                    try:
                        fd = self.open_fn(pad['event'])
                        s = self.settings() or {}
                        nav = Navigator(s.get('pad_deadzone', 35) / 100, s.get('pad_repeat_ms', 220),
                                        s.get('pad_confirm', 'south'), self._absinfo(fd))
                        with self.lock:
                            self.active, self.last_error = pad, ''
                        self._notice(f"Controller connected: {pad['model']} ({pad['connection']})")
                    except OSError as exc:
                        with self.lock:
                            self.last_error = f"Cannot read {pad['event']}: {exc.strerror}"
                        fd = None
            if fd is None:
                self.stop_event.wait(0.25)
                continue
            try:
                ready, _, _ = select.select([fd], [], [], 0.05)
                if ready:
                    chunk = os.read(fd, EVENT.size * 64)
                    if not chunk:
                        raise OSError(errno.ENODEV, 'device closed')
                    buf += chunk
                    while len(buf) >= EVENT.size:
                        _, _, etype, code, value = EVENT.unpack(buf[:EVENT.size])
                        buf = buf[EVENT.size:]
                        acts = nav.feed(etype, code, value, time.monotonic())
                        with self.lock:
                            self.events_seen += 1
                            self.last_button = nav.last_button
                        self._dispatch(acts)
                self._dispatch(nav.tick(time.monotonic()))
            except OSError as exc:
                if exc.errno in (errno.EAGAIN,):
                    continue
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd, buf = None, b''
                with self.lock:
                    name = self.active['model'] if self.active else 'Controller'
                    self.active = None
                self._notice(f'{name} disconnected')
                last_scan = 0.0
        if fd is not None:
            os.close(fd)

    def _dispatch(self, actions):
        if not actions or self.paused():
            return   # a running game owns the controller; Nexus ignores its input meanwhile
        enabled = (self.settings() or {}).get('pad_enabled', True)
        if not enabled:
            return
        for a in actions:
            try:
                self.on_action(a)
            except Exception:
                pass
