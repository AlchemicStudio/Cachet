#!/usr/bin/env bash
# Build of both LINUX executables:
#   dist/signApp       (windowed: double-click -> GUI)
#   dist/signApp-cli   (console: batch signing/stamping)
#
# Usage:  ./build_linux.sh
#
# The windowed binary requires tkinter (_tkinter) in the Python used. On
# Ubuntu/Debian, if the venv does not have Tk:  sudo apt install python3-tk
# (or python3.14-tk depending on the version). The console binary does not need it.
#
# IMPORTANT: build on the OLDEST glibc you need to support — PyInstaller
# does not guarantee forward compatibility of glibc (a binary built on
# a recent glibc fails on an older target with "GLIBC_2.xx not found").
set -euo pipefail
cd "$(dirname "$0")"

PY=./venv/bin/python
if [ ! -x "$PY" ]; then
  echo ">> venv not found — creating a new venv…"
  python3 -m venv venv
fi

echo ">> Installing dependencies (runtime + build)…"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r requirements.txt -r requirements-build.txt

echo ">> Checking tkinter (required by the windowed binary)…"
"$PY" - <<'EOF' || echo "   WARNING: tkinter missing -> the GUI binary will fail. Install python3-tk."
import tkinter, _tkinter  # noqa: F401
print("   tkinter OK")
EOF

echo ">> PyInstaller…"
"$PY" -m PyInstaller --noconfirm --clean signApp.spec

echo
echo "=== Produced executables ==="
ls -la dist/
