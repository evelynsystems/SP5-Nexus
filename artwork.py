"""Game artwork and metadata from the user's own game folder, plus a local artwork cache.

Authorized sources only: images and metadata that ship inside the game the user dumped
(sce_sys/icon0.png, sce_sys/pic1.png or pic0.png, sce_sys/param.json), and images the user
picks. Nothing is downloaded. Artwork the user picked is never replaced automatically.
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path

IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def sce_sys_dirs(elf_path):
    """sce_sys/ next to the executable or one level up (app0/ layouts)."""
    elf = Path(elf_path)
    out = []
    for base in (elf.parent, elf.parent.parent):
        d = base / 'sce_sys'
        if d.is_dir() and d not in out:
            out.append(d)
    return out


def _is_image(path):
    p = Path(path)
    if p.suffix.lower() not in IMAGE_TYPES or not p.is_file() or p.is_symlink():
        return False
    try:
        size = p.stat().st_size
        with p.open('rb') as f:
            head = f.read(12)
    except OSError:
        return False
    if not 0 < size <= MAX_IMAGE_BYTES:
        return False
    if p.suffix.lower() == '.png':
        return head.startswith(PNG_MAGIC)
    if p.suffix.lower() in ('.jpg', '.jpeg'):
        return head[:3] == b'\xff\xd8\xff'
    return head[:4] == b'RIFF' and head[8:12] == b'WEBP'


def read_param_json(path):
    """Title, title id and content version from a PS5 sce_sys/param.json, when present."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    loc = data.get('localizedParameters')
    if isinstance(loc, dict):
        lang = loc.get('defaultLanguage') if isinstance(loc.get('defaultLanguage'), str) else 'en-US'
        for key in (lang, 'en-US', 'en-GB'):
            entry = loc.get(key)
            if isinstance(entry, dict) and isinstance(entry.get('titleName'), str) and entry['titleName'].strip():
                out['title'] = entry['titleName'].strip()[:200]
                break
    if isinstance(data.get('titleId'), str):
        out['title_id'] = data['titleId'][:32]
    if isinstance(data.get('contentVersion'), str):
        out['version'] = data['contentVersion'][:32]
    return out


def discover(elf_path):
    """Artwork and metadata found inside the game folder."""
    found = {}
    for d in sce_sys_dirs(elf_path):
        if 'cover' not in found and _is_image(d / 'icon0.png'):
            found['cover'] = str(d / 'icon0.png')
        for name in ('pic1.png', 'pic0.png'):
            if 'background' not in found and _is_image(d / name):
                found['background'] = str(d / name)
        if 'meta' not in found and (d / 'param.json').is_file():
            meta = read_param_json(d / 'param.json')
            if meta:
                found['meta'] = meta
    return found


def cache_image(source, cache_dir, game_id, kind):
    """Copy an image into the per-game cache so artwork survives moved or unmounted game folders."""
    src = Path(source).expanduser()
    if not _is_image(src):
        raise ValueError('Artwork must be a PNG, JPEG or WebP image under 20 MB')
    if kind not in ('cover', 'background'):
        raise ValueError('Unknown artwork kind')
    safe_id = ''.join(c for c in str(game_id) if c.isalnum())[:64]
    if not safe_id:
        raise ValueError('Invalid game id')
    target_dir = Path(cache_dir) / safe_id
    target_dir.mkdir(parents=True, exist_ok=True)
    for old in target_dir.glob(kind + '.*'):
        old.unlink()
    target = target_dir / (kind + src.suffix.lower())
    tmp = target.with_suffix(target.suffix + '.tmp')
    shutil.copyfile(src, tmp)
    tmp.replace(target)
    return str(target)


def apply_discovered(game, cache_dir):
    """Fill cover/background/title metadata from the game folder without touching anything
    the user set by hand. Returns the list of changed fields."""
    found = discover(game.get('elf', ''))
    changes = {}
    source = dict(game.get('art_source') or {})
    for kind, field in (('cover', 'art'), ('background', 'background')):
        if kind in found and source.get(kind) != 'user':
            try:
                path = cache_image(found[kind], cache_dir, game['id'], kind)
            except (OSError, ValueError):
                continue
            if game.get(field) != path:
                changes[field] = path
            source[kind] = 'game'
    if source != (game.get('art_source') or {}):
        changes['art_source'] = source
    meta = found.get('meta') or {}
    if meta.get('title_id') and not game.get('title_id'):
        changes['title_id'] = meta['title_id']
    if meta.get('title') and source.get('title') != 'user' and game.get('name') in ('', 'eboot', Path(game.get('elf', '')).parent.name):
        changes['name'] = meta['title']
    return changes
