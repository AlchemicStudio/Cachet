# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller — two standalone executables, from ONE single spec.

    pyinstaller signApp.spec        # Linux -> dist/signApp + dist/signApp-cli
                                    # Windows -> dist/signApp.exe + dist/signApp-cli.exe

Produces:
  * "signApp"        : WINDOWED binary (console=False). Double-click -> opens the
                       CustomTkinter GUI (entry point: gui_main.py). Bundles
                       Tk/CustomTkinter + the whole engine.
  * "signApp-cli"    : CONSOLE binary (console=True). Batch signing/stamping
                       from a terminal (entry point: sign_pdfs_beid.py).
                       Deliberately WITHOUT Tk (headless), hence lighter.

RUNTIME dependencies deliberately NOT bundled (to be installed on the target
machine) — see BUILD.md:
  * Belgian eID middleware -> libbeidpkcs11.so / beidpkcs11.dll, loaded by path
    via pkcs11.lib() (beid mode only). Specific to the machine + the reader.
  * poppler (pdftoppm) -> OPTIONAL fallback for the page preview. The preview is
    now rendered by pypdfium2 (PDFium bundled into the GUI binary via the
    provided hooks), so nothing to install on the user's side; pdftoppm is
    only used if it is already present (cf. core.render_page_image).
"""

import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

# --------------------------------------------------------------------------- #
#  Collection helpers
# --------------------------------------------------------------------------- #
common_datas = []
common_binaries = []
common_hidden = []


def _add_all(pkg, *, datas, binaries, hidden):
    """collect_all(pkg) -> adds (datas, binaries, hiddenimports) to the lists."""
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hidden.extend(h)


def _meta(dist, datas):
    """Tolerant copy_metadata: some libs read their version/entry-points
    via importlib.metadata at runtime (pyHanko notably)."""
    try:
        datas.extend(copy_metadata(dist))
    except Exception as exc:  # pragma: no cover - depends on the environment
        print(f"[signApp.spec] copy_metadata({dist!r}) ignored: {exc}")


# --- Common engine (CLI + GUI): no Tk dependency here ----------------------- #
# pyHanko + plugins: no provided hook -> collect everything (submodules,
# possible data) and copy the metadata (plugin discovery).
for _pkg in ("pyhanko", "pyhanko_beid", "pyhanko_certvalidator"):
    _add_all(_pkg, datas=common_datas, binaries=common_binaries, hidden=common_hidden)

# asn1crypto + oscrypto: no provided hook. oscrypto IS on the import path
# (pulled in by pyhanko_certvalidator) and loads OpenSSL at runtime ->
# must be present in the bundle.
for _pkg in ("asn1crypto", "oscrypto"):
    _add_all(_pkg, datas=common_datas, binaries=common_binaries, hidden=common_hidden)

# python-pkcs11: native _pkcs11 extension -> binary to bundle explicitly.
_add_all("pkcs11", datas=common_datas, binaries=common_binaries, hidden=common_hidden)
common_binaries += collect_dynamic_libs("pkcs11")
common_hidden += ["pkcs11._pkcs11"]

# Metadata read at runtime (versions / entry-points).
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

# lxml: hook provided, but we make sure of the submodules (xpath/objectify...).
common_hidden += collect_submodules("lxml")

# trust.py (EU trusted-list provider for LTV signing): local module imported
# lazily inside the core -> make it explicit. Its deps ride along: requests
# (also a pyHanko dep) and platformdirs (otherwise only pulled in by
# customtkinter, which the CLI binary excludes).
common_hidden += ["trust", "requests", "platformdirs"]

# azure mode (Entra ID + Key Vault) is CLI-usable -> COMMON collection; do
# NOT exclude azure from the CLI binary. No provided hooks for the azure
# SDK (hooks-contrib only ships unrelated 'azurerm'), so collect_all the
# dotted packages + metadata. azure_signer.py is imported lazily by the
# core, like trust.py.
for _pkg in (
    "azure.core",
    "azure.identity",
    "azure.keyvault.keys",
    "azure.keyvault.certificates",
    "msal",
    "msal_extensions",
):
    _add_all(_pkg, datas=common_datas, binaries=common_binaries, hidden=common_hidden)
for _dist in (
    "azure-core",
    "azure-identity",
    "azure-keyvault-keys",
    "azure-keyvault-certificates",
    "msal",
    "msal-extensions",
    "PyJWT",
):
    _meta(_dist, common_datas)
common_hidden += ["azure_signer", "jwt"]

# tzdata: ON WINDOWS ONLY. pyHanko timestamps the signature via tzlocal,
# which builds a zoneinfo.ZoneInfo there — but Windows has no system zoneinfo
# database, so the "tzdata" package must be bundled. On Linux/macOS the system
# database (/usr/share/zoneinfo) is enough and tzdata is generally absent: we
# collect it ONLY if it is installed (no-op otherwise).
import importlib.util as _ilu

if _ilu.find_spec("tzdata") is not None:
    _add_all("tzdata", datas=common_datas, binaries=common_binaries, hidden=common_hidden)
    common_hidden += ["tzdata"]
    _meta("tzdata", common_datas)

# --------------------------------------------------------------------------- #
#  GUI extras (only for the windowed binary)
# --------------------------------------------------------------------------- #
gui_datas = list(common_datas)
gui_binaries = list(common_binaries)
gui_hidden = list(common_hidden)

# customtkinter: hook provided, but collect_all guarantees themes + fonts.
_add_all("customtkinter", datas=gui_datas, binaries=gui_binaries, hidden=gui_hidden)
gui_hidden += [
    "darkdetect",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "PIL.ImageTk",
    "PIL._tkinter_finder",
    "gui",  # local module imported lazily by gui_main
    # Page preview: bundled PDFium engine (rendering without external dependency).
    # The provided hooks (hook-pypdfium2*.py) bundle the native lib.
    "pypdfium2",
]

# --------------------------------------------------------------------------- #
#  The CLI binary excludes all Tk: it is headless by design.
# --------------------------------------------------------------------------- #
CLI_EXCLUDES = [
    "tkinter",
    "_tkinter",
    "customtkinter",
    "darkdetect",
    "PIL.ImageTk",
    "gui",
    "gui_main",
    # The page preview is exclusively GUI: no PDFium in the CLI binary.
    "pypdfium2",
    "pypdfium2_raw",
]

# Optional icon: drop signApp.ico (Windows) / signApp.icns (macOS) next to
# this spec to customize. Absent -> default icon.
import os as _os

_ICON = None
for _cand in ("signApp.ico", "signApp.icns"):
    if _os.path.exists(_cand):
        _ICON = _cand
        break

# --------------------------------------------------------------------------- #
#  CONSOLE binary: signApp-cli  (entry = sign_pdfs_beid.py)
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
    # UPX disabled: it can corrupt some crypto .so/.dll (libcrypto,
    # cryptography's _rust) and causes crashes that are hard to diagnose.
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
#  WINDOWED binary: signApp  (entry = gui_main.py)
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
    upx=False,  # cf. CLI binary above: UPX can corrupt crypto .so files
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
