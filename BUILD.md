# Building the standalone executables (Linux & Windows)

`signApp` is packaged with **PyInstaller** into **two standalone binaries**,
from a single recipe file `signApp.spec`:

| Binary | Type | Role | Double-click |
|---|---|---|---|
| **`signApp`** (`.exe` on Windows) | windowed (`console=False`) | opens the CustomTkinter **graphical interface** | ➜ launches the GUI |
| **`signApp-cli`** (`.exe` on Windows) | console (`console=True`) | **batch signing/stamping** from a terminal | ➜ shows the help |

The console binary is deliberately **headless** (no Tk) and thus lighter; the
windowed binary bundles Tk + CustomTkinter + the whole engine.

> ℹ️ **PyInstaller does not cross-compile.** A Windows `.exe` must be produced
> *on* Windows (real machine, Windows CI, or Wine). The Linux binary compiles
> natively on Linux. The three Windows routes are described below.

---

## 1. Linux (native)

```bash
./build_linux.sh
# -> dist/signApp        (GUI)
# -> dist/signApp-cli    (CLI)
```

The windowed binary requires `tkinter`/`_tkinter` in the venv's Python. On
Ubuntu/Debian if needed: `sudo apt install python3-tk` (or `python3.14-tk`).

**glibc compatibility**: compile on the **oldest** distribution you want to
support. PyInstaller bundles Python and the Python libs but links dynamically
against the system glibc; a binary compiled on a recent glibc will fail on an
older target (`GLIBC_2.xx not found`).

---

## 2. Windows — on a real Windows machine (the most reliable)

Prerequisites: **64-bit Python 3.12 / 3.13 / 3.14** installed with the
*"tcl/tk and IDLE"* option checked, `py`/`python` in the `PATH`.

```bat
build_windows.bat
:: -> dist\signApp.exe        (GUI)
:: -> dist\signApp-cli.exe    (CLI)
```

This is the recommended route for a production deliverable: it produces a native
`.exe` testable immediately (including eID mode if a reader + card are present).

---

## 3. Windows — via GitHub Actions (CI, reproducible)

The `.github/workflows/build.yml` workflow compiles **Windows + Linux** on the
GitHub runners on every `push`/tag, runs a smoke test (image mode, no card) and
publishes the binaries as artifacts.

```bash
# once: push the repository to GitHub
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

Then: **Actions** tab → run → **Artifacts** → `signApp-windows-latest` /
`signApp-ubuntu-latest`. Can also be triggered manually (*workflow_dispatch*).

The CI's Python version is `3.13` (variable `PYTHON_VERSION` at the top of the
workflow; `3.14` works too).

---

## 4. Windows — via Wine, from Linux (best effort)

> ⚠️ Officially **unsupported** by PyInstaller. Reserve this for a stopgap
> `.exe`: the **GUI often does not display** correctly *under Wine* (Tcl/Tk +
> GDI bugs specific to Wine, absent on real Windows), and **eID mode is not
> testable** under Wine (no reader, no card, no `beidpkcs11.dll`).

```bash
sudo apt install wine        # single root step, run it yourself
./build_windows_wine.sh      # downloads Windows Python, installs the deps, builds
# -> dist/signApp.exe, dist/signApp-cli.exe
```

The script creates a 64-bit Wine prefix (`~/.wine-signapp`), installs Python 3.12
for Windows (all pinned `win_amd64` *wheels* exist as cp312), `pip install
--only-binary=:all:` (a missing wheel fails outright rather than attempting an
impossible compilation under Wine), then runs PyInstaller.

**The result must be validated on real Windows** before distribution.

---

## *Runtime* dependencies (NOT bundled — to be installed on the target machine)

These components are loaded dynamically and **cannot** be packaged:

- **Belgian eID middleware** — provides the PKCS#11 library loaded by path
  (`pkcs11.lib(...)`), **`beid` mode only**:
  - Windows: `C:\Windows\System32\beidpkcs11.dll`
  - Linux: `/usr/lib/…/libbeidpkcs11.so` (+ `pcscd` running)
  - macOS: `/usr/local/lib/libbeidpkcs11.dylib`
  - Install it from <https://eid.belgium.be>. Override the path via `--lib`.
  - Also requires a **reader + inserted eID card** + the PC/SC service.
- **Network endpoints** — PAdES levels ≥ `b-t` (the default is `b-lta`) reach
  out at signing time to the **RFC 3161 TSA** (`--timestamp-url`, default
  DigiCert free TSA), the **EU trusted list** (LOTL, cached locally 24 h) and
  the CAs' **OCSP/CRL** endpoints. Offline machines can only sign at
  `--pades-level b-b` (or stamp images); on failure the app names the
  unreachable endpoint and **never silently downgrades the level**.
The GUI's **page preview** (step 6) is rendered by **pypdfium2** (PDFium engine
**bundled** in the executable): nothing to install, on any OS.
`poppler` (`pdftoppm`) is now only an **optional fallback** used only if it is
already present on the machine (see `core.render_page_image`).

The **image mode** depends on none of these components: it is fully functional
and testable without hardware.

---

## Customization

- **Icon**: drop `signApp.ico` (Windows) or `signApp.icns` (macOS) next to
  `signApp.spec` — it will be picked up automatically. On Linux the icon is
  ignored by PyInstaller (provide a `.desktop` file with `Icon=` instead).
- **onefile → onedir**: by default each binary is *onefile* (a single file,
  slightly slower startup because it is decompressed into a temporary folder).
  For faster startup (an `exe + _internal/` folder), pass
  `exclude_binaries=True` in each `EXE(...)` and add a `COLLECT(...)` — see the
  PyInstaller docs.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| GUI: `Can't find a usable init.tcl` / empty window | Tcl/Tk data not collected | rebuild with a Python that has a complete `tkinter`; the `_tkinter` hook collects it into `_tcl_data`/`_tk_data` |
| GUI: `FileNotFoundError` on a `.json` theme | missing customtkinter assets | make sure `pyinstaller-hooks-contrib` is installed (it is); the spec also does `collect_all('customtkinter')` |
| `ModuleNotFoundError: pkcs11._pkcs11` | native extension not bundled | already handled (`collect_dynamic_libs('pkcs11')` + hiddenimport); check the build log |
| `ZoneInfoNotFoundError` at signing time **on Windows** | zoneinfo database missing | `tzdata` (installed via `requirements-build.txt` on Windows; collected by the spec) |
| OpenSSL error from `oscrypto` | version parsing on certain OpenSSL builds | not triggered by this app's image/eID modes (the Linux trust-list reads PEM files); otherwise `pip install` a fixed oscrypto or pyhanko-certvalidator ≥ 0.41 |
| `beid mode` "fails" on a clean machine | eID middleware/reader/card missing | **expected**: install the eID middleware (see above); this is not a packaging bug |
| Antivirus / SmartScreen blocks the `.exe` | unsigned onefile binaries are often flagged | `upx=False` (already the case); ideally **sign (Authenticode)** the `.exe` on Windows; switch to *onedir* if needed |
| `GLIBC_2.xx not found` (Linux) | compiled on too recent a glibc | recompile on the oldest target distribution |

---

## What CANNOT be tested without hardware

The **eID mode** (cryptographic signature via the card) requires a reader + an
eID card + the middleware + a PIN entry per document: it can only be validated
on a real, equipped machine. Packaging is verified up to the loading of the
PKCS#11 library; the actual signature must be the subject of an **acceptance
test on real hardware** before distribution:

1. Sign a PDF with the defaults (`--mode beid`, i.e. PAdES **B-LTA** with
   timestamping, embedded revocation info and the archival timestamp).
2. Check the app's own summary line reports the requested level
   (e.g. `PAdES-B-LTA, LTV ok`) — the post-signing self-verification fails
   the document on any mismatch.
3. Open the PDF in **Adobe Acrobat Reader**: the signature panel must show
   the signature as valid **and "LTV enabled"**.
4. Cross-check with the pyHanko CLI:
   `pyhanko sign validate --pretty-print --ltv-profile pades-lta <signed.pdf>`
   must report the expected level and a sound timestamp chain.

Reminder: with the default **free TSA** the timestamps are technically valid
but **not qualified**; for eIDAS-qualified preservation, run the acceptance
test against a **qualified TSA** (`--timestamp-url`).

### Ongoing maintenance for B-LTA archives (follow-up)

A B-LTA document's archival timestamp chain must be **renewed before the
last timestamp's certificate expires** (typically every few years). This
repository does not yet ship an `ltaupdate`-style maintenance command to
re-timestamp existing archives — planned follow-up; until then, renew with
the pyHanko CLI (`pyhanko sign ltaupdate --timestamp-url … <file>`).
