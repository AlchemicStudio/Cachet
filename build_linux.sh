#!/usr/bin/env bash
# Build des deux exécutables LINUX :
#   dist/signApp       (fenêtré : double-clic -> GUI)
#   dist/signApp-cli   (console : signature/tampon en lot)
#
# Usage :  ./build_linux.sh
#
# Le binaire fenêtré exige tkinter (_tkinter) dans le Python utilisé. Sur
# Ubuntu/Debian, si le venv n'a pas Tk :  sudo apt install python3-tk
# (ou python3.14-tk selon la version). Le binaire console n'en a pas besoin.
#
# IMPORTANT : compilez sur la PLUS ANCIENNE glibc à supporter — PyInstaller
# n'assure pas la compatibilité ascendante de la glibc (un binaire compilé sur
# une glibc récente échoue sur une cible plus ancienne avec « GLIBC_2.xx not found »).
set -euo pipefail
cd "$(dirname "$0")"

PY=./venv/bin/python
if [ ! -x "$PY" ]; then
  echo ">> venv introuvable — création d'un nouveau venv…"
  python3 -m venv venv
fi

echo ">> Installation des dépendances (runtime + build)…"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r requirements.txt -r requirements-build.txt

echo ">> Vérification de tkinter (nécessaire au binaire fenêtré)…"
"$PY" - <<'EOF' || echo "   AVERTISSEMENT : tkinter absent -> le binaire GUI échouera. Installez python3-tk."
import tkinter, _tkinter  # noqa: F401
print("   tkinter OK")
EOF

echo ">> PyInstaller…"
"$PY" -m PyInstaller --noconfirm --clean signApp.spec

echo
echo "=== Exécutables produits ==="
ls -la dist/
