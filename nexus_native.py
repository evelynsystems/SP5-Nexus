"""Bridge between SP5 Nexus (Python) and the compiled Rust backend.

Status values reported to the interface:
  * "Rust Backend Active"      - the compiled module imported, matched API_VERSION and passed self_test.
  * "Rust Backend Unavailable" - no compiled module was found (Nexus uses its Python implementations).
  * "Rust Backend Error"       - a module was found but failed to load, mismatched or failed self_test.

Source files alone never count as "Active": the module must import and answer.
Set SP5_DISABLE_RUST=1 to force the Python implementations.

Every function here returns Python objects. Rust errors arrive as ValueError,
FileNotFoundError, PermissionError, OSError, TimeoutError or RuntimeError.
"""
from __future__ import annotations
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED_API = 3
MODULE_NAME = 'sp5_nexus_native'
# Built modules are looked up in site-packages first (maturin/pip install), then here.
LOCAL_DIRS = (ROOT / 'native_build',)

ACTIVE = 'Rust Backend Active'
UNAVAILABLE = 'Rust Backend Unavailable'
ERROR = 'Rust Backend Error'

_lock = threading.Lock()
_module = None
_note = ''
_status = {'state': UNAVAILABLE, 'detail': 'Not loaded yet', 'version': '', 'binding': '',
           'path': '', 'load_ms': 0.0, 'functions': 0}


def _import_default():
    """site-packages first (PyO3 wheel installed by maturin), then native_build/ (C-API build).
    Returns (module, error_text)."""
    global _note
    try:
        return importlib.import_module(MODULE_NAME), ''
    except ModuleNotFoundError as exc:
        if exc.name != MODULE_NAME:
            _note = f'Installed module failed to import ({exc}); trying native_build/. '
    except Exception as exc:
        # A broken PyO3 install must not hide a working C-API build; the failure stays visible.
        _note = f'Installed module failed to import ({type(exc).__name__}: {exc}); trying native_build/. '
    for folder in LOCAL_DIRS:
        if not folder.is_dir():
            continue
        sys.path.insert(0, str(folder))
        try:
            return importlib.import_module(MODULE_NAME), ''
        except ModuleNotFoundError as inner:
            if inner.name != MODULE_NAME:
                return None, f'Import failed: {inner}'
        except Exception as inner:  # ImportError for wrong ABI, OSError for a bad ELF
            return None, f'{type(inner).__name__}: {inner}'
        finally:
            if sys.path and sys.path[0] == str(folder):
                sys.path.pop(0)
    return None, _note.strip()


def _import_capi():
    """Load the C-API build by file path so an installed PyO3 wheel cannot shadow it."""
    import importlib.machinery, importlib.util
    files = sorted(f for d in LOCAL_DIRS if d.is_dir() for f in d.glob(MODULE_NAME + '*.so'))
    if not files:
        return None, ''
    try:
        loader = importlib.machinery.ExtensionFileLoader(MODULE_NAME, str(files[0]))
        spec = importlib.util.spec_from_file_location(MODULE_NAME, str(files[0]), loader=loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module, ''
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'


def _import_pyo3():
    """Import from site-packages only, ignoring native_build/."""
    local = {d.resolve() for d in LOCAL_DIRS}
    saved = list(sys.path)
    sys.path[:] = [p for p in sys.path if Path(p or '.').resolve() not in local]
    try:
        return importlib.import_module(MODULE_NAME), ''
    except ModuleNotFoundError as exc:
        return None, '' if exc.name == MODULE_NAME else f'Import failed: {exc}'
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'
    finally:
        sys.path[:] = saved


def _load():
    started = time.perf_counter()
    if os.environ.get('SP5_DISABLE_RUST') == '1':
        return UNAVAILABLE, 'Disabled by SP5_DISABLE_RUST=1', None
    # SP5_NATIVE_BINDING=pyo3|capi selects one build explicitly (used by tests and build_native.sh).
    wanted = os.environ.get('SP5_NATIVE_BINDING', '').strip().lower()
    if wanted not in ('', 'pyo3', 'capi'):
        return ERROR, f'SP5_NATIVE_BINDING must be "pyo3" or "capi", not {wanted!r}', None
    module, error = {'': _import_default, 'pyo3': _import_pyo3, 'capi': _import_capi}[wanted]()
    if error:
        return ERROR, error, None
    if module is None:
        hint = {'': './build_native.sh', 'pyo3': './build_native.sh --pyo3', 'capi': './build_native.sh --capi'}[wanted]
        return UNAVAILABLE, f'Compiled {wanted + " " if wanted else ""}module not found. Run {hint} to build it.', None
    if wanted and getattr(module, 'BINDING', '') != wanted:
        return ERROR, f'Requested the {wanted} binding but loaded {getattr(module, "BINDING", "unknown")} from {getattr(module, "__file__", "?")}', None
    api = getattr(module, 'API_VERSION', None)
    if api != EXPECTED_API:
        return ERROR, f'Module API version {api} does not match the expected {EXPECTED_API}. Rebuild with ./build_native.sh.', None
    try:
        result = json.loads(module.self_test('{"probe":"sp5"}'))
        if not result.get('ok') or result.get('echo') != {'probe': 'sp5'}:
            return ERROR, 'Self test returned an unexpected result', None
    except Exception as exc:
        return ERROR, f'Self test failed: {type(exc).__name__}: {exc}', None
    _status['load_ms'] = round((time.perf_counter() - started) * 1000, 2)
    return ACTIVE, 'Compiled module loaded and passed its self test', module


def init(force=False):
    """Load the extension once. Safe to call from any thread."""
    global _module, _note
    with _lock:
        if _status['detail'] != 'Not loaded yet' and not force:
            return dict(_status)
        _note = ''
        state, detail, module = _load()
        if module is not None and _note:
            detail = _note + detail
        _module = module
        _status.update(state=state, detail=detail)
        if module is not None:
            _status.update(version=getattr(module, '__version__', ''), binding=getattr(module, 'BINDING', ''),
                           path=str(getattr(module, '__file__', '')),
                           functions=len([n for n in dir(module) if not n.startswith('_') and callable(getattr(module, n))]))
        else:
            _status.update(version='', binding='', path='', functions=0)
        return dict(_status)


def status():
    if _status['detail'] == 'Not loaded yet':
        init()
    return dict(_status)


def active():
    return status()['state'] == ACTIVE


def call(name, payload=None):
    """Call a Rust function with a JSON-able payload and return the decoded result."""
    if not active():
        raise RuntimeError(f'{status()["state"]}: {status()["detail"]}')
    func = getattr(_module, name, None)
    if func is None:
        raise RuntimeError(f'Rust backend has no function {name}')
    return json.loads(func(json.dumps(payload or {})))


def info():
    """Status plus the backend's own report when active."""
    s = status()
    if s['state'] == ACTIVE:
        try:
            s['report'] = call('backend_info')
        except Exception as exc:
            s['report_error'] = str(exc)
    return s
