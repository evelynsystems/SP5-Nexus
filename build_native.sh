#!/usr/bin/env bash
# Build the SP5 Nexus Rust backend once and verify it with the interpreter that runs Nexus.
#
#   ./build_native.sh            PyO3 + Maturin; if that fails the failure is reported and the C-API build is made
#   ./build_native.sh --both     build and verify BOTH bindings (PyO3 into the venv, C-API into native_build/)
#   ./build_native.sh --pyo3     PyO3 only; exits non-zero if it fails (needs crates.io/PyPI the first time)
#   ./build_native.sh --capi     dependency-free C-API build only (cargo, works offline)
#
# Every run writes native_build/BUILD_STATUS.txt: which bindings built, which one Nexus loads,
# and the PyO3 error (full log in native_build/pyo3-build.log) when PyO3 failed.
# Nexus loads the PyO3 module first and keeps the C-API module as a fallback.
set -uo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$HERE"
MODE="${1:-auto}"
case "$MODE" in auto|--both|--pyo3|--capi) ;; *) echo "Unknown option $MODE" >&2; exit 2 ;; esac
PY="${SP5_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; else PY="$(command -v python3)"; fi
fi
[[ "$PY" = /* ]] || PY="$HERE/$PY"   # absolute, but keep the venv path (do not resolve symlinks)
mkdir -p native_build
STATUS=native_build/BUILD_STATUS.txt
PYO3_RESULT="not attempted"; CAPI_RESULT="not attempted"

echo "Python used by SP5 Nexus: $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])'))"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "Python 3.10 or newer is required." >&2; exit 1; }
command -v cargo >/dev/null || { echo "Rust (cargo) is not installed. Install it from https://rustup.rs or: sudo apt install cargo rustc" >&2; exit 1; }
echo "Rust: $(rustc --version)"

verify() { # $1 = binding to require (pyo3|capi|"" for whatever Nexus loads by default)
  (cd / && SP5_DISABLE_RUST= SP5_NATIVE_BINDING="$1" "$PY" - "$HERE" <<'PYCHECK'
import sys
sys.path.insert(0, sys.argv[1])
import nexus_native as N
s = N.init(force=True)
print(f"  {s['state']}: binding={s['binding'] or '-'} version={s['version'] or '-'} functions={s['functions']}")
print(f"  {s['detail']}")
if s['path']: print(f"  module: {s['path']}")
sys.exit(0 if s['state'] == N.ACTIVE else 1)
PYCHECK
  )
}

build_pyo3() {
  local log=native_build/pyo3-build.log
  : > "$log"
  {
    if ! "$PY" -m maturin --version; then
      echo "Installing maturin into the Nexus environment..."
      "$PY" -m pip install 'maturin>=1.5,<2.0' || return 1
    fi
    (cd native/python-pyo3 && "$PY" -m maturin build --release --out "$HERE/native_build/wheels" -i "$PY") || return 1
    local wheel; wheel="$(ls -t native_build/wheels/sp5_nexus_native-*.whl | head -1)"
    "$PY" -m pip install --force-reinstall --no-deps "$wheel" || return 1
  } >>"$log" 2>&1
}

build_capi() {
  (cd native/python-capi && { cargo build --release --locked --offline 2>/dev/null || cargo build --release; }) || return 1
  cp native/python-capi/target/release/libsp5_nexus_native.so native_build/sp5_nexus_native.abi3.so.tmp &&
  mv native_build/sp5_nexus_native.abi3.so.tmp native_build/sp5_nexus_native.abi3.so
}

echo "== Rust core tests"
(cd native/core && cargo test --release --quiet 2>&1 | grep "test result" | head -1) || true

if [[ "$MODE" != "--capi" ]]; then
  echo "== PyO3 binding (maturin)"
  if build_pyo3 && verify pyo3; then
    PYO3_RESULT="OK (installed into $("$PY" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])'))"
  else
    PYO3_RESULT="FAILED - see native_build/pyo3-build.log"
    echo "  PyO3 build FAILED. Last lines of native_build/pyo3-build.log:" >&2
    tail -n 15 native_build/pyo3-build.log | sed 's/^/    /' >&2
  fi
fi
if [[ "$MODE" == "--capi" || "$MODE" == "--both" || ( "$MODE" == "auto" && "$PYO3_RESULT" != OK* ) ]]; then
  echo "== C-API binding (cargo)"
  if build_capi && verify capi; then CAPI_RESULT="OK (native_build/sp5_nexus_native.abi3.so)"; else CAPI_RESULT="FAILED"; fi
fi

echo "== Binding Nexus will load by default"
verify "" ; DEFAULT_OK=$?
ACTIVE_BINDING="$(cd / && SP5_NATIVE_BINDING= "$PY" -c "import sys;sys.path.insert(0,'$HERE');import nexus_native as N;print(N.init()['binding'] or 'none (Python implementations)')")"
{
  echo "SP5 Nexus native build status - $(date -Iseconds)"
  echo "Python:  $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])'))"
  echo "Rust:    $(rustc --version)"
  echo "PyO3:    $PYO3_RESULT"
  echo "C-API:   $CAPI_RESULT"
  echo "Loaded by default: $ACTIVE_BINDING"
} > "$STATUS"
echo; cat "$STATUS"

case "$MODE" in
  --pyo3) [[ "$PYO3_RESULT" == OK* ]] ;;
  --capi) [[ "$CAPI_RESULT" == OK* ]] ;;
  --both) [[ "$PYO3_RESULT" == OK* && "$CAPI_RESULT" == OK* ]] ;;
  auto)   [[ $DEFAULT_OK -eq 0 ]] ;;
esac
