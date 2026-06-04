#!/usr/bin/env bash
# ===================================================================
#  Cross-build WINDOWS (.exe) FROM LINUX via Wine — "best effort".
#
#  ⚠️  PyInstaller does NOT officially support cross-compilation.
#      We run the *Windows* PyInstaller in a *Windows* Python
#      installed under Wine; the output is a real PE .exe.
#
#  Known limitations (cf. BUILD.md):
#    - The GUI (cachet.exe) often does NOT display correctly UNDER Wine
#      (Tcl/Tk + GDI bugs specific to Wine) — this is a Wine artifact, not the
#      binary: TEST cachet.exe on a REAL Windows.
#    - eID mode is NOT testable under Wine (no reader, no card, no
#      beidpkcs11.dll DLL). Only image mode can be exercised.
#    - The result must be validated on a real Windows before distribution.
#
#  Prerequisites: wine (64-bit).  Ubuntu:  sudo apt install wine
#  Usage:  ./build_windows_wine.sh
# ===================================================================
set -euo pipefail
cd "$(dirname "$0")"

export WINEARCH=win64
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine-cachet}"
export WINEDEBUG="${WINEDEBUG:--all}"

# Windows Python used under Wine. 3.12 is the most reliable under Wine; all
# the pinned win_amd64 wheels exist for cp312
# (cryptography cp311-abi3, lxml/pillow/python-pkcs11/cffi cp312).
PYVER="${PYVER:-3.12.8}"
PYDIR='C:\Python312'
WINPY_UNIX="$WINEPREFIX/drive_c/Python312/python.exe"
PYEXE="python-${PYVER}-amd64.exe"
URL="https://www.python.org/ftp/python/${PYVER}/${PYEXE}"

if ! command -v wine >/dev/null 2>&1; then
  echo "ERROR: wine is not installed."
  echo "         Install it (a single sudo step):  sudo apt install wine"
  echo "         then re-run this script."
  exit 1
fi

echo ">> Wine prefix: $WINEPREFIX (WINEARCH=$WINEARCH)"
WINEDLLOVERRIDES="mscoree=d;mshtml=d" wineboot --init >/dev/null 2>&1 || true
# "wineserver" is not always a binary exposed in the PATH (Ubuntu
# repack); waiting is optional (the silent installer is synchronous).
wait_wine() { wineserver -w 2>/dev/null || true; }
wait_wine

if [ ! -f "$WINPY_UNIX" ]; then
  if [ ! -f "$PYEXE" ]; then
    echo ">> Downloading Python $PYVER (Windows amd64)…"
    if command -v wget >/dev/null 2>&1; then wget -q "$URL"
    else curl -fsSL -o "$PYEXE" "$URL"; fi
  fi
  echo ">> Silent install of Python under Wine (declines Mono/Gecko)…"
  wine "$PYEXE" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 \
       Include_tcltk=1 Include_test=0 TargetDir="$PYDIR" || {
         echo "ERROR: the Python installer failed under Wine."
         echo "         Try: winetricks vcrun2019 corefonts ; then re-run."
         exit 1; }
  wait_wine
fi

[ -f "$WINPY_UNIX" ] || { echo "ERROR: Python not found in the prefix after installation."; exit 1; }

echo ">> pip (wheels ONLY: a missing wheel fails outright)…"
wine "$WINPY_UNIX" -m pip install --upgrade pip
wine "$WINPY_UNIX" -m pip install --only-binary=:all: \
     -r requirements.txt -r requirements-build.txt tzdata

echo ">> PyInstaller under Wine…"
wine "$WINPY_UNIX" -m PyInstaller --noconfirm --clean cachet.spec

echo
echo "=== Result (dist/) ==="
ls -la dist/ || true
echo
echo "⚠️  Validate cachet.exe / cachet-cli.exe on a REAL Windows:"
echo "    - GUI: double-click cachet.exe;"
echo "    - eID: Belgian eID middleware + reader + card required;"
echo "    - sign a test PDF and verify the signature (Adobe Reader / pyHanko)."
