#!/usr/bin/env bash
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
exec .venv/bin/python desktop.py
