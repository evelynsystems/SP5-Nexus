"""Game library scanning, indexing, searching, sorting and duplicate detection.

This is the verified Python reference implementation. The Rust backend
(native/src/library.rs) implements the same rules; tests/test_parity.py runs
both on identical data and requires identical results before Nexus uses Rust.
"""
from __future__ import annotations
import os
from pathlib import Path

PS5_MACHINES = (0, 21, 183)          # Same rule as backend.validate_elf
SELF_MAGIC = b'\x4f\x15\x3d\x1d'     # Encrypted SELF container (not accepted by AnyPS5)
MODULE_DIRS = ('sce_module', 'sce_modules', 'prx')   # upstream USAGE.md, re-read 2026-11-07
FINGERPRINT_BYTES = 65536
SORTS = ('name', 'recent', 'added', 'playtime')
FILTERS = ('all', 'converted', 'not_converted')


def fnv1a64(data: bytes) -> int:
    h = 0xcbf29ce484222325
    for byte in data:
        h ^= byte
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def fingerprint(path) -> str:
    """Cheap duplicate fingerprint: file size plus FNV-1a 64 of the first 64 KiB."""
    p = Path(path)
    size = p.stat().st_size
    with p.open('rb') as f:
        head = f.read(FINGERPRINT_BYTES)
    return f'{size}:{fnv1a64(head):016x}'


def classify_header(header: bytes):
    """Return (kind, machine) for a candidate executable header, or (None, None)."""
    if len(header) >= 4 and header[:4] == SELF_MAGIC:
        return 'self', None
    if len(header) < 20 or header[:4] != b'\x7fELF' or header[4] != 2 or header[5] != 1:
        return None, None
    machine = int.from_bytes(header[18:20], 'little')
    return ('elf', machine) if machine in PS5_MACHINES else (None, None)


def module_state(directory: Path) -> str:
    """Module folders present, joined with '+', e.g. 'sce_module+prx'; '' when none."""
    return '+'.join(d for d in MODULE_DIRS if (directory / d).is_dir())


def _candidate(path: Path, size: int, kind: str, machine):
    modules = module_state(path.parent)
    if kind == 'self':
        convertible, reason = False, 'Encrypted SELF container. AnyPS5 accepts only plain ELF input.'
    elif 'sce_module+sce_modules' in modules:
        convertible, reason = False, 'Both sce_module/ and sce_modules/ exist; AnyPS5 accepts only one of them.'
    elif modules:
        convertible, reason = True, 'Plain ELF with ' + modules.replace('+', '/ and ') + '/ beside it.'
    else:
        convertible, reason = False, 'No sce_module/, sce_modules/ or prx/ folder beside the ELF.'
    name = path.parent.name if path.name.lower() == 'eboot.bin' else path.stem
    return {'path': str(path), 'name': name or path.name, 'size': size, 'kind': kind,
            'machine': -1 if machine is None else machine, 'modules': modules,
            'convertible': convertible, 'reason': reason}


def scan_library(roots, max_depth=8, max_files=200000):
    """Walk the given folders without following symbolic links and report PS5 executables."""
    candidates, errors = [], []
    files_scanned = dirs_scanned = 0
    truncated = False
    for root in roots:
        base = Path(str(root)).expanduser()
        if not base.is_dir():
            errors.append(f'Not a folder: {base}')
            continue
        stack = [(base, 0)]
        while stack:
            directory, depth = stack.pop()
            dirs_scanned += 1
            try:
                entries = sorted(os.scandir(directory), key=lambda e: e.name)
            except OSError as exc:
                errors.append(f'{directory}: {exc.strerror or exc}')
                continue
            subdirs = []
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in MODULE_DIRS and depth < max_depth:
                            subdirs.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if files_scanned >= max_files:
                        truncated = True
                        break
                    files_scanned += 1
                    size = entry.stat(follow_symlinks=False).st_size
                    if size < 64:
                        continue
                    with open(entry.path, 'rb') as f:
                        header = f.read(20)
                    kind, machine = classify_header(header)
                    if kind:
                        candidates.append(_candidate(Path(entry.path), size, kind, machine))
                except OSError as exc:
                    errors.append(f'{entry.path}: {exc.strerror or exc}')
            if truncated:
                break
            for sub in reversed(subdirs):
                stack.append((sub, depth + 1))
        if truncated:
            break
    candidates.sort(key=lambda c: c['path'])
    return {'candidates': candidates, 'files_scanned': files_scanned, 'dirs_scanned': dirs_scanned,
            'errors': errors[:50], 'truncated': truncated}


def query_library(games, query='', sort='name', filter='all'):
    """Return game ids in display order. games need id, name, added, last_played,
    playtime_seconds and output_exists."""
    q = str(query or '').lower()
    rows = [g for g in games if q in str(g.get('name', '')).lower()]
    if filter == 'converted':
        rows = [g for g in rows if g.get('output_exists')]
    elif filter == 'not_converted':
        rows = [g for g in rows if not g.get('output_exists')]
    name_key = lambda g: (str(g.get('name', '')).lower(), str(g.get('id', '')))
    rows.sort(key=name_key)
    if sort == 'recent':
        rows.sort(key=lambda g: str(g.get('last_played') or ''), reverse=True)
    elif sort == 'added':
        rows.sort(key=lambda g: str(g.get('added') or ''), reverse=True)
    elif sort == 'playtime':
        rows.sort(key=lambda g: int(g.get('playtime_seconds') or 0), reverse=True)
    return [str(g['id']) for g in rows]


def find_duplicates(games):
    """Group library ids that point at the same executable path."""
    groups = {}
    for g in games:
        key = os.path.normpath(str(g.get('elf', '')))
        if key and key != '.':
            groups.setdefault(key, []).append(str(g['id']))
    return [ids for _, ids in sorted(groups.items()) if len(ids) > 1]
