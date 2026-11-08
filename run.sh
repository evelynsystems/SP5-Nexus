#!/usr/bin/env bash
# SP5 Nexus launcher: locate the app, pick the Python interpreter, check Qt and the Rust
# backend, then start the desktop window. The Rust module is built once with
# ./build_native.sh; it is never compiled during a normal launch.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [[ ! -d .venv ]]; then
  if ! python3 -m venv .venv; then
    echo 'Python virtual environment support is missing. Install python3-venv.' >&2
    exit 1
  fi
fi
if ! .venv/bin/python -c 'from PySide6.QtWebEngineWidgets import QWebEngineView' >/dev/null 2>&1; then
  echo 'Installing Qt desktop dependencies into the local virtual environment...'
  .venv/bin/python -m pip install -r requirements.txt
fi
for f in desktop.py server.py web/index.html web/app.js web/style.css; do
  [[ -f "$f" ]] || { echo "SP5 Nexus is incomplete: $f is missing." >&2; exit 1; }
done
# Report the native backend state; Nexus falls back to its Python implementations if needed.
.venv/bin/python -c 'import nexus_native as N; s=N.init(); print("SP5 Nexus:", s["state"], s["version"], "-", s["detail"])' || true
exec .venv/bin/python desktop.py "$@"
