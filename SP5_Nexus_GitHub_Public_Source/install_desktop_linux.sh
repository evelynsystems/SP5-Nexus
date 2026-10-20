#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/256x256/apps"
if [[ -f "$HERE/nexus.png" ]]; then
  cp "$HERE/nexus.png" "$HOME/.local/share/icons/hicolor/256x256/apps/sp5-nexus.png"
fi
python3 - "$HERE" "$HOME/.local/share/applications/sp5-nexus.desktop" <<'PYDESK'
import pathlib,sys
root,path=map(pathlib.Path,sys.argv[1:])
path.write_text('[Desktop Entry]\nType=Application\nName=SP5 Nexus\nComment=Powered by AnyPS5\nExec="'+str(root/'run.sh')+'"\nPath='+str(root)+'\nIcon=sp5-nexus\nTerminal=false\nCategories=Game;Utility;\nStartupNotify=true\n')
print('Installed launcher:',path)
PYDESK
