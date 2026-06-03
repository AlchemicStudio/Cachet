#!/usr/bin/env bash
# ===================================================================
#  Cross-build WINDOWS (.exe) DEPUIS LINUX via Wine — « best effort ».
#
#  ⚠️  PyInstaller ne supporte PAS officiellement la cross-compilation.
#      On exécute le PyInstaller *Windows* dans un Python *Windows*
#      installé sous Wine ; la sortie est un vrai .exe PE.
#
#  Limites connues (cf. BUILD.md) :
#    - La GUI (signApp.exe) ne s'affiche souvent PAS correctement SOUS Wine
#      (bugs Tcl/Tk + GDI propres à Wine) — c'est un artefact de Wine, pas du
#      binaire : TESTEZ signApp.exe sur un VRAI Windows.
#    - Le mode eID n'est PAS testable sous Wine (ni lecteur, ni carte, ni DLL
#      beidpkcs11.dll). Seul le mode image est exerçable.
#    - Résultat à valider impérativement sur un vrai Windows avant diffusion.
#
#  Prérequis : wine (64 bits).  Ubuntu :  sudo apt install wine
#  Usage :  ./build_windows_wine.sh
# ===================================================================
set -euo pipefail
cd "$(dirname "$0")"

export WINEARCH=win64
export WINEPREFIX="${WINEPREFIX:-$HOME/.wine-signapp}"
export WINEDEBUG="${WINEDEBUG:--all}"

# Python Windows utilisé sous Wine. 3.12 est le plus fiable sous Wine ; toutes
# les roues (wheels) win_amd64 épinglées existent pour cp312
# (cryptography cp311-abi3, lxml/pillow/python-pkcs11/cffi cp312).
PYVER="${PYVER:-3.12.8}"
PYDIR='C:\Python312'
WINPY_UNIX="$WINEPREFIX/drive_c/Python312/python.exe"
PYEXE="python-${PYVER}-amd64.exe"
URL="https://www.python.org/ftp/python/${PYVER}/${PYEXE}"

if ! command -v wine >/dev/null 2>&1; then
  echo "ERREUR : wine n'est pas installé."
  echo "         Installez-le (une seule étape sudo) :  sudo apt install wine"
  echo "         puis relancez ce script."
  exit 1
fi

echo ">> Prefix Wine : $WINEPREFIX (WINEARCH=$WINEARCH)"
WINEDLLOVERRIDES="mscoree=d;mshtml=d" wineboot --init >/dev/null 2>&1 || true
# « wineserver » n'est pas toujours un binaire exposé dans le PATH (repack
# Ubuntu) ; l'attente est facultative (l'installeur silencieux est synchrone).
wait_wine() { wineserver -w 2>/dev/null || true; }
wait_wine

if [ ! -f "$WINPY_UNIX" ]; then
  if [ ! -f "$PYEXE" ]; then
    echo ">> Téléchargement de Python $PYVER (Windows amd64)…"
    if command -v wget >/dev/null 2>&1; then wget -q "$URL"
    else curl -fsSL -o "$PYEXE" "$URL"; fi
  fi
  echo ">> Installation silencieuse de Python sous Wine (décline Mono/Gecko)…"
  wine "$PYEXE" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 \
       Include_tcltk=1 Include_test=0 TargetDir="$PYDIR" || {
         echo "ERREUR : l'installeur Python a échoué sous Wine."
         echo "         Essayez : winetricks vcrun2019 corefonts ; puis relancez."
         exit 1; }
  wait_wine
fi

[ -f "$WINPY_UNIX" ] || { echo "ERREUR : Python introuvable dans le prefix après installation."; exit 1; }

echo ">> pip (wheels UNIQUEMENT : un wheel manquant échoue franchement)…"
wine "$WINPY_UNIX" -m pip install --upgrade pip
wine "$WINPY_UNIX" -m pip install --only-binary=:all: \
     -r requirements.txt -r requirements-build.txt tzdata

echo ">> PyInstaller sous Wine…"
wine "$WINPY_UNIX" -m PyInstaller --noconfirm --clean signApp.spec

echo
echo "=== Résultat (dist/) ==="
ls -la dist/ || true
echo
echo "⚠️  Validez signApp.exe / signApp-cli.exe sur un VRAI Windows :"
echo "    - GUI : double-clic sur signApp.exe ;"
echo "    - eID : middleware eID belge + lecteur + carte requis ;"
echo "    - signez un PDF de test et vérifiez la signature (Adobe Reader / pyHanko)."
