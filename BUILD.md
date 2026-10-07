# Building the standalone executables (Linux & Windows)

`Cachet` is packaged with **PyInstaller** into **two standalone binaries**,
from a single recipe file `cachet.spec`:

| Binary | Type | Role | Double-click |
|---|---|---|---|
| **`cachet`** (`.exe` on Windows) | windowed (`console=False`) | opens the CustomTkinter **graphical interface** | ➜ launches the GUI |
| **`cachet-cli`** (`.exe` on Windows) | console (`console=True`) | **batch signing/stamping** from a terminal | ➜ shows the help |

The console binary is deliberately **headless** (no Tk) and thus lighter; the
windowed binary bundles Tk + CustomTkinter + the whole engine.

> ℹ️ **PyInstaller does not cross-compile.** A Windows `.exe` must be produced
> *on* Windows (real machine, Windows CI, or Wine). The Linux binary compiles
> natively on Linux. The three Windows routes are described below.

---

## 1. Linux (native)

```bash
./build_linux.sh
# -> dist/cachet        (GUI)
# -> dist/cachet-cli    (CLI)
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
:: -> dist\cachet.exe        (GUI)
:: -> dist\cachet-cli.exe    (CLI)
```

This is the recommended route for a production deliverable: it produces a native
`.exe` testable immediately (including eID mode if a reader + card are present).

---

## 3. Windows — via GitHub Actions (CI, reproducible)

The `.github/workflows/build.yml` workflow compiles **Windows + Linux** on the
GitHub runners (pushes to `develop`, pull requests, manual runs) and publishes
the binaries as artifacts. Before building, the Linux job runs the tests in
three steps:

1. the **headless suite** (`env -u DISPLAY python -m unittest -v`; the `Gui*`
   classes skip themselves);
2. a **GUI canary** under Xvfb — `import tkinter, customtkinter, gui` and a
   `Tk()` window — so a broken Tk or a `gui.py` that no longer imports fails
   the job instead of making every GUI test skip;
3. the **`Gui*` suites** under Xvfb with `CACHET_REQUIRE_GUI=1`, which turns
   any remaining skip into a failure (they are the end-to-end layer of this
   desktop app).

After the build, a **smoke test** runs the frozen CLI without a card, on both
OSes: the legacy single image (`--image-path`), a text signature (`--text`)
and a `--signatures` file. The last two prove that the bundled fonts and
Pillow's FreeType renderer are in the CLI binary.

```bash
# once: push the repository to GitHub (build.yml runs on `develop`; a push to
# `main` runs the release workflow instead — see "Release process" below)
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin develop
```

Then: **Actions** tab → run → **Artifacts** → `cachet-windows-latest` /
`cachet-ubuntu-latest`. Can also be triggered manually (*workflow_dispatch*).

**GitLab CI** (`.gitlab-ci.yml`) and **Forgejo Actions**
(`.forgejo/workflows/tests.yml`) run the same three test steps in a
`python:3.13-bookworm` container. They run the tests only: builds and releases
stay on GitHub. On Forgejo, adapt `runs-on: docker` to your instance's runner
label.

## Release process (develop → main)

Branch model: day-to-day work lands on **`develop`** (CI builds + tests every
push, `.github/workflows/build.yml`); **merging `develop` into `main`
publishes a release** (`.github/workflows/release.yml`):

1. On `develop`, bump `__version__` in `sign_pdfs_beid.py` (single source of
   truth — the CLI `--version` and the GUI title read it).
2. Open a PR `develop` → `main` and merge it.
3. The release workflow then: reads the version → checks the tag `v{version}`
   does not already exist → runs the tests (Linux: headless suite, GUI canary,
   `Gui*` suites under Xvfb with `CACHET_REQUIRE_GUI=1`) → builds **both**
   executables on Windows AND Linux → smoke-tests the frozen CLI (legacy
   image, `--text`, `--signatures`) → packages
   `cachet-{v}-linux-x86_64.tar.gz` + `cachet-{v}-windows-x86_64.zip` →
   creates the tag and a **GitHub Release** with auto-generated notes and the
   two archives attached.
4. Merging without bumping `__version__` is safe: the workflow detects the
   existing tag and skips publishing (notice in the run log).

A failed build/test on either OS blocks the release (`fail-fast`). The
manual eID acceptance test (above) is NOT gated by CI — run it before
merging when the signing path changed.

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
# -> dist/cachet.exe, dist/cachet-cli.exe
```

The script creates a 64-bit Wine prefix (`~/.wine-cachet`), installs Python 3.12
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
  DigiCert free TSA), the trust source — **EU trusted list** (LOTL, cached
  locally 24 h) in beid mode, the **internal CA chain file** in azure mode —
  and the CAs' **OCSP/CRL** endpoints. `azure` mode additionally needs
  **`login.microsoftonline.com`** (Entra ID) and the **Key Vault URL**.
  Offline machines can only sign at `--pades-level b-b` in beid mode (or
  stamp visual signatures); on failure the app names the unreachable endpoint and
  **never silently downgrades the level**.
- **Azure per-user provisioning** (azure mode only) — each user needs a Key
  Vault key + certificate (internal CA) named after the key template
  (default `sig-{upn}`) and `sign`/`get` permissions; see README. This is an
  Azure-admin prerequisite, not something the app creates.
The GUI's **page preview** (step 6) is rendered by **pypdfium2** (PDFium engine
**bundled** in the executable): nothing to install, on any OS.
`poppler` (`pdftoppm`) is now only an **optional fallback** used only if it is
already present on the machine (see `core.render_page_image`).

**Bundled fonts** — the six OFL fonts of the text signatures (`fonts/*.ttf`)
and their licence texts (`fonts/licenses/`, which the OFL requires to travel
with the fonts) are bundled in **both** binaries (`common_datas` in
`cachet.spec`): the CLI stamps text too. `stamps.asset_path` finds them next
to the module in a checkout and under `sys._MEIPASS` in a frozen binary. A
font the user designates by path (`.ttf`/`.otf`) is read from that path at
run time and is never bundled.

**User profile** (GUI only — the CLI never reads it) — the GUI keeps the
signature library, the last placements and the wizard settings in
`profile.json` under the user config dir, and a copy of the imported/drawn
images in `signatures/` under the user data dir (Linux: `~/.config/Cachet/`
and `~/.local/share/Cachet/`; Windows: `%LOCALAPPDATA%\Cachet\` for both;
macOS: `~/Library/Application Support/Cachet/` for both). Override with
`CACHET_CONFIG_DIR` / `CACHET_DATA_DIR`. The files are **unencrypted** and
never contain a PIN, a token or any other credential. Nothing has to be
installed or created beforehand: the folders appear at the first save.

The **visual signatures** (image mode, and the stamps added in beid/azure
mode) depend on none of the components listed above: they are fully
functional and testable without hardware or network.

---

## Customization

- **Icon**: drop `cachet.ico` (Windows) or `cachet.icns` (macOS) next to
  `cachet.spec` — it will be picked up automatically. On Linux the icon is
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
| Text signature: `cannot load font '…': cannot open resource` / `bundled font file missing: …` | `fonts/` not in the bundle | check that `common_datas` in `cachet.spec` still carries `("fonts/*.ttf", "fonts")` (it must be in the **common** list, not only the GUI's) and that `fonts/` is present in the checkout |
| Text signature: `The _imagingft C module is not installed` | Pillow's FreeType renderer not bundled | `PIL._imagingft` must stay in `common_hidden` (`cachet.spec`); check the build log |
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
5. **Visual signatures + eID signature.** Sign a multi-page PDF with the card
   AND two visual signatures, one of them on every page (`--mode beid
   --signatures sigs.json`, or the GUI's step 6), at B-LTA. In Adobe Acrobat
   Reader the signature must be valid, "LTV enabled", and must **not** report
   that the document was modified after signing: the visual signatures are
   stamped before the signature, in the same revision.
6. **Already-signed input.** Take the output of step 5 and
   (a) countersign it with the card, with another field base name
   (`--field Countersign`) and **no** visual signature — both signatures must
   be valid in Acrobat; (b) try again **with** a visual signature — Cachet
   must refuse the document (`the document is already signed…`) **before**
   asking for the PIN, and write nothing.

Reminder: with the default **free TSA** the timestamps are technically valid
but **not qualified**; for eIDAS-qualified preservation, run the acceptance
test against a **qualified TSA** (`--timestamp-url`).

### Manual acceptance test — azure mode (real Entra ID + Key Vault)

The Azure SDK is fully mocked in the unit tests; the real login/signing
path must be validated manually against a provisioned tenant:

1. Sign a PDF as a real user:
   `--mode azure --azure-vault-url https://… --azure-trust-anchors chain.pem`
   (defaults: device-code login, PAdES B-LTA). Exactly **one** login prompt
   must appear for the whole batch.
2. The summary must report `signed (Azure, <your-upn>) — PAdES-B-LTA, LTV ok`.
3. In **Adobe Acrobat Reader**: signature valid, **"LTV enabled"**, and the
   signer identity is the USER's certificate (their name, internal CA chain).
4. Cross-check with
   `pyhanko sign validate --pretty-print --ltv-profile pades-lta <signed.pdf>`
   (point its trust at the internal CA chain).
5. Negative check: pass `--azure-key-name <someone-else's key>` — the run
   must print the override warning, and Key Vault must deny the `sign`
   operation unless your account was explicitly granted it.
6. Visual signatures: repeat step 1 with `--signatures sigs.json` (two visual
   signatures, one on every page). Acrobat must show the signature as valid
   and must **not** report a modification after signing. Then run it again on
   that signed output: Cachet must refuse the document
   (`the document is already signed…`) and write nothing.

Reminder: azure mode produces an **advanced** signature (AES) with the
internal CA — appropriate for internal documents, not a qualified (QES)
signature.

### Ongoing maintenance for B-LTA archives (follow-up)

A B-LTA document's archival timestamp chain must be **renewed before the
last timestamp's certificate expires** (typically every few years). This
repository does not yet ship an `ltaupdate`-style maintenance command to
re-timestamp existing archives — planned follow-up; until then, renew with
the pyHanko CLI (`pyhanko sign ltaupdate --timestamp-url … <file>`).
