# signApp

**Batch** signing of PDF files with the **Belgian electronic identity card
(eID)**, or stamping of a signature **image** — with input validation against a
template, both from the **command line** and from a **graphical interface**
(CustomTkinter).

> ⚖️ The eID mode uses the card's **non-repudiation** certificate, legally
> equivalent to a handwritten signature. The **national register number** is
> embedded in every signature produced — mind the distribution of the signed
> PDFs.

---

## Two signature modes

| Mode | Card required | Nature | Output |
|---|---|---|---|
| **`beid`** | yes (reader + card + PIN per document) | **cryptographic** eID signature (pyHanko via the PKCS#11 middleware) | visible **vignette**: cardholder photo + "Signed by:" / name / date |
| **`image`** | no | **image stamp** (this is *not* a cryptographic signature) | the supplied image, placed at a chosen position |

In both modes, the same vignette/image + page + position is applied to **all**
the documents in the batch — template validation guarantees the files are
geometrically identical.

## Template validation (`--template`)

If a template PDF is supplied, each input is accepted **only** if it has the
**same page count** AND **exactly identical per-page dimensions** (strict
equality, no tolerance). Rejected files are never signed; the rejection reason
is displayed (CLI summary / GUI table).

## Output

Files are written as `{name}_signe.pdf` in the output folder and are **never
overwritten**: on collision, ` - 1`, ` - 2`, … are appended.

---

## Requirements (runtime)

- **`beid` mode**: the **Belgian eID middleware** installed
  (<https://eid.belgium.be>), which provides the PKCS#11 library
  (`libbeidpkcs11.so` / `beidpkcs11.dll` / `…dylib`), a **reader + inserted eID
  card**, and the **PC/SC** service (`pcscd`) running. The PIN is requested for
  **each** document. **Levels ≥ b-t additionally need network access** (TSA,
  EU trusted list, OCSP/CRL — see *Signature levels* below); `b-b` is offline.
- **`image` mode**: nothing special — pure PDF stamping.
- **Graphical interface**: `customtkinter` + a Python with `tkinter` and a
  display. The page preview (step 6) is rendered by **pypdfium2** (the
  **bundled** PDFium engine) — no external dependency to install. (poppler /
  `pdftoppm` is now only an optional fallback if already present.)

## Installation (from source)

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# GUI on Ubuntu/Debian, if tkinter is missing:  sudo apt install python3-tk
```

---

## Usage — command line

```bash
# eID signing (vignette bottom-right of the last page), PAdES B-LTA by default:
./venv/bin/python sign_pdfs_beid.py --input ../pdfs --output ../signes --mode beid

# eID signing at a lighter level (basic signature, fully offline):
./venv/bin/python sign_pdfs_beid.py --input ../pdfs --output ../signes --pades-level b-b

# Image stamp (no card), validated against a template:
./venv/bin/python sign_pdfs_beid.py --mode image \
  --template ../pdfs/MODELE.pdf --input ../pdfs --output ../signes \
  --image-path signature.png --page 1 --x 360 --y 150

# Graphical interface:
./venv/bin/python sign_pdfs_beid.py --gui
```

### Options

| Flag | Meaning |
|---|---|
| `--gui` | launch the graphical interface; otherwise run in console mode. |
| `--input <paths…>` | files and/or folders to process (folders are globbed for `*.pdf`). |
| `--output <folder>` | output folder (`{name}_signe.pdf`, never overwritten). |
| `--template <pdf>` | template PDF; if supplied, inputs are validated against it. |
| `--mode beid\|image` | signature mode (default `beid`). |
| `--image-path <img>` | image to stamp (**required** in `--mode image`). |
| `--page <N>` | target page, **1-based**. Image: insertion page. beID: vignette page. |
| `--x <pt> --y <pt>` | lower-left corner, in points from the page's bottom-left. beID: **omit both ⇒ bottom-right of the last page**. |
| `--pades-level <lvl>` | PAdES baseline level: `b-b`, `b-t`, `b-lt`, `b-lta` (**default `b-lta`**). See *Signature levels* below. |
| `--timestamp-url <url>` | RFC 3161 TSA for levels ≥ b-t. Precedence: flag > `SIGNAPP_TSA_URL` env > `http://timestamp.digicert.com`. |
| `--trust-list-url <url>` | EU LOTL URL seeding the LTV trust anchors. Precedence: flag > `SIGNAPP_LOTL_URL` env > the official EU URL. |
| `--refresh-trust-list` | force re-download of the EU trusted list (bypass the 24 h cache). |
| `--digest <alg>` | signature digest: `sha256` (default), `sha384`, `sha512`. |
| `--no-verify` | skip the post-signing self-verification (levels ≥ b-t). |
| `--pades` | **deprecated no-op** (PAdES is now the default); use `--pades-level`. |
| `--legacy-cms` | **deprecated**: legacy non-PAdES `adbe.pkcs7.detached` signature (no timestamp, no LTV). Incompatible with levels above b-b. |
| `--lib <path>` | path to the eID PKCS#11 library (otherwise OS-default value). |
| `--field <name>` | base name of the signature field (beid mode). |

### Signature levels (PAdES baseline, ETSI EN 319 142-1)

| Level | Adds | Network needed |
|---|---|---|
| `b-b` | basic eID signature | none (offline) |
| `b-t` | + trusted RFC 3161 timestamp | TSA (+ EU trusted list unless `--no-verify`) |
| `b-lt` | + revocation info (OCSP/CRL) embedded in the document (LTV) | TSA, EU trusted list, OCSP/CRL endpoints |
| `b-lta` | + archival document-timestamp chain (**default**) | same as b-lt |

- **Long-term validation (LTV)**: from `b-lt` upward, everything needed to
  validate the signature later (CA certificates from the **EU trusted list**,
  OCSP/CRL responses) is embedded at signing time, so the PDF stays verifiable
  after certificates expire. `b-lta` adds a document timestamp so the evidence
  chain itself stays provable — for genuine archival, that chain must be
  **renewed periodically** (see BUILD.md).
- **⚠ Free vs qualified timestamps**: the default TSA
  (`http://timestamp.digicert.com`) is free and yields *technically valid*
  B-T/B-LTA signatures, but **not qualified timestamps** in the eIDAS sense.
  For genuine qualified long-term preservation, point `--timestamp-url` at a
  **qualified** TSA (see the EU trusted list for QTSPs offering QTST services).
- **Self-verification**: after writing each signed PDF (levels ≥ b-t), the file
  is re-opened and validated, and the *achieved* level is reported in the
  summary (e.g. `PAdES-B-LTA, LTV ok`). A mismatch marks the document failed —
  the level is **never silently downgraded**; `--no-verify` skips this check.
- **Offline behaviour**: `b-b` and `image` mode work fully offline. Levels
  ≥ b-t fail with an actionable error naming the unreachable endpoint (TSA,
  EU trusted list, OCSP/CRL).
- **🔒 Privacy**: the signer's **national register number (RRN)** is embedded
  in every eID signature (it is part of the certificate). The CLI warns at
  startup and in the summary — mind how signed PDFs are distributed.

> Backward compatibility: the legacy positional form `inputs… output_folder`
> is still accepted if `--input`/`--output` are absent.

## Usage — graphical interface

A vertical wizard walks through the flow: **1.** template → **2.** files →
**3.** output folder → **4.** validation (pass/fail table) → **5.** mode
(eID/image + PAdES level selector, default `b-lta`) → **6.** page + position (actual page preview, click to
place) → **7.** launch → **8.** per-document summary.

---

## Standalone executables (Linux & Windows)

The project compiles into **two standalone binaries** per OS (a windowed GUI
`signApp`, a console CLI `signApp-cli`) — no Python required on the target
machine. See **[BUILD.md](BUILD.md)** for all the routes (native Linux, native
Windows, Wine, and GitHub Actions CI).

```bash
./build_linux.sh        # Linux  -> dist/signApp , dist/signApp-cli
build_windows.bat       # Windows -> dist\signApp.exe , dist\signApp-cli.exe
```

The eID middleware remains a **runtime dependency** (beid mode) and is never
bundled; the page preview, for its part, works without installing anything
(PDFium bundled via pypdfium2).

## Tests

Headless `unittest` suite (no card, no tkinter):

```bash
./venv/bin/python -m unittest -v
```

## Project structure

| File | Role |
|---|---|
| `sign_pdfs_beid.py` | core + CLI entry point (business logic, importable without tkinter). |
| `trust.py` | EU trusted-list (LOTL) trust provider: anchors for LTV, with local cache. |
| `gui.py` | CustomTkinter interface (façade over the core). |
| `gui_main.py` | entry point of the windowed binary (opens the GUI). |
| `test_sign_pdfs_beid.py`, `test_trust.py` | `unittest` test suites. |
| `signApp.spec` | PyInstaller recipe (two binaries). |
| `build_*.sh` / `build_windows.bat` | build scripts. |
| `.github/workflows/build.yml` | CI: Windows + Linux binaries as artifacts. |
| `BUILD.md` | detailed packaging guide. |
