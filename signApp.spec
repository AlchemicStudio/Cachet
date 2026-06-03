# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller — deux exécutables autonomes, depuis UN seul spec.

    pyinstaller signApp.spec        # Linux -> dist/signApp + dist/signApp-cli
                                    # Windows -> dist/signApp.exe + dist/signApp-cli.exe

Produit :
  * « signApp »      : binaire FENÊTRÉ (console=False). Double-clic -> ouvre la
                       GUI CustomTkinter (point d'entrée : gui_main.py). Embarque
                       Tk/CustomTkinter + tout le moteur.
  * « signApp-cli »  : binaire CONSOLE (console=True). Signature/tampon en lot
                       depuis un terminal (point d'entrée : sign_pdfs_beid.py).
                       Volontairement SANS Tk (headless), donc plus léger.

Dépendances RUNTIME volontairement NON embarquées (à installer sur la machine
cible) — voir BUILD.md :
  * Middleware eID belge -> libbeidpkcs11.so / beidpkcs11.dll, chargé par chemin
    via pkcs11.lib() (mode beid uniquement). Spécifique à la machine + au lecteur.
  * poppler (pdftoppm) -> repli OPTIONNEL pour l'aperçu de page. L'aperçu est
    désormais rendu par pypdfium2 (PDFium embarqué dans le binaire GUI via les
    hooks fournis), donc rien à installer côté utilisateur ; pdftoppm n'est
    utilisé que s'il est déjà présent (cf. core.render_page_image).
"""

import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# --------------------------------------------------------------------------- #
#  Helpers de collecte
# --------------------------------------------------------------------------- #
common_datas = []
common_binaries = []
common_hidden = []


def _add_all(pkg, *, datas, binaries, hidden):
    """collect_all(pkg) -> ajoute (datas, binaries, hiddenimports) aux listes."""
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hidden.extend(h)


def _meta(dist, datas):
    """copy_metadata tolérant : certaines libs lisent leur version/entry-points
    via importlib.metadata au runtime (pyHanko notamment)."""
    try:
        datas.extend(copy_metadata(dist))
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        print(f"[signApp.spec] copy_metadata({dist!r}) ignoré : {exc}")


# --- Moteur commun (CLI + GUI) : aucune dépendance Tk ici ------------------- #
# pyHanko + plugins : pas de hook fourni -> on collecte tout (sous-modules,
# données éventuelles) et on copie les métadonnées (découverte de plugins).
for _pkg in ("pyhanko", "pyhanko_beid", "pyhanko_certvalidator"):
    _add_all(_pkg, datas=common_datas, binaries=common_binaries, hidden=common_hidden)

# asn1crypto + oscrypto : pas de hook fourni. oscrypto EST sur le chemin
# d'import (tiré par pyhanko_certvalidator) et charge OpenSSL au runtime ->
# doit être présent dans le bundle.
for _pkg in ("asn1crypto", "oscrypto"):
    _add_all(_pkg, datas=common_datas, binaries=common_binaries, hidden=common_hidden)

# python-pkcs11 : extension native _pkcs11 -> binaire à embarquer explicitement.
_add_all("pkcs11", datas=common_datas, binaries=common_binaries, hidden=common_hidden)
common_binaries += collect_dynamic_libs("pkcs11")
common_hidden += ["pkcs11._pkcs11"]

# Métadonnées lues au runtime (versions / entry-points).
for _dist in (
    "pyHanko",
    "pyhanko-beid-plugin",
    "pyhanko-cli",
    "pyhanko-certvalidator",
    "asn1crypto",
    "oscrypto",
    "cryptography",
    "certvalidator",
):
    _meta(_dist, common_datas)

# lxml : hook fourni, mais on s'assure des sous-modules (xpath/objectify...).
common_hidden += collect_submodules("lxml")

# tzdata : SUR WINDOWS UNIQUEMENT. pyHanko horodate la signature via tzlocal,
# qui y construit un zoneinfo.ZoneInfo — or Windows n'a pas de base zoneinfo
# système, il faut donc embarquer le paquet « tzdata ». Sous Linux/macOS la base
# système (/usr/share/zoneinfo) suffit et tzdata est généralement absent : on ne
# collecte QUE s'il est installé (sinon no-op).
import importlib.util as _ilu

if _ilu.find_spec("tzdata") is not None:
    _add_all("tzdata", datas=common_datas, binaries=common_binaries, hidden=common_hidden)
    common_hidden += ["tzdata"]
    _meta("tzdata", common_datas)

# --------------------------------------------------------------------------- #
#  Extras GUI (uniquement pour le binaire fenêtré)
# --------------------------------------------------------------------------- #
gui_datas = list(common_datas)
gui_binaries = list(common_binaries)
gui_hidden = list(common_hidden)

# customtkinter : hook fourni, mais collect_all garantit thèmes + polices.
_add_all("customtkinter", datas=gui_datas, binaries=gui_binaries, hidden=gui_hidden)
gui_hidden += [
    "darkdetect",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "PIL.ImageTk",
    "PIL._tkinter_finder",
    "gui",  # module local importé paresseusement par gui_main
    # Aperçu de page : moteur PDFium embarqué (rendu sans dépendance externe).
    # Les hooks fournis (hook-pypdfium2*.py) embarquent la lib native.
    "pypdfium2",
]

# --------------------------------------------------------------------------- #
#  Le binaire CLI exclut tout Tk : il est headless par conception.
# --------------------------------------------------------------------------- #
CLI_EXCLUDES = [
    "tkinter",
    "_tkinter",
    "customtkinter",
    "darkdetect",
    "PIL.ImageTk",
    "gui",
    "gui_main",
    # L'aperçu de page est exclusivement GUI : pas de PDFium dans le binaire CLI.
    "pypdfium2",
    "pypdfium2_raw",
]

# Icône optionnelle : déposez signApp.ico (Windows) / signApp.icns (macOS) à
# côté de ce spec pour personnaliser. Absent -> icône par défaut.
import os as _os

_ICON = None
for _cand in ("signApp.ico", "signApp.icns"):
    if _os.path.exists(_cand):
        _ICON = _cand
        break

# --------------------------------------------------------------------------- #
#  Binaire CONSOLE : signApp-cli  (entrée = sign_pdfs_beid.py)
# --------------------------------------------------------------------------- #
a_cli = Analysis(
    ["sign_pdfs_beid.py"],
    pathex=["."],
    binaries=common_binaries,
    datas=common_datas,
    hiddenimports=common_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=CLI_EXCLUDES,
    noarchive=False,
)
pyz_cli = PYZ(a_cli.pure)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    a_cli.binaries,
    a_cli.datas,
    [],
    name="signApp-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX désactivé : il peut corrompre certaines .so/.dll crypto (libcrypto,
    # _rust de cryptography) et provoque des plantages difficiles à diagnostiquer.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)

# --------------------------------------------------------------------------- #
#  Binaire FENÊTRÉ : signApp  (entrée = gui_main.py)
# --------------------------------------------------------------------------- #
a_gui = Analysis(
    ["gui_main.py"],
    pathex=["."],
    binaries=gui_binaries,
    datas=gui_datas,
    hiddenimports=gui_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    a_gui.binaries,
    a_gui.datas,
    [],
    name="signApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # cf. binaire CLI ci-dessus : UPX peut corrompre les .so crypto
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)
