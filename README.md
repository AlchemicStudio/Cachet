# Cachet

Batch PDF signing for Belgian administrative workflows, usable from
both a **command line** and a **CustomTkinter GUI**, with three modes:

- **`beid`** — qualified (QES-grade) cryptographic signatures with the Belgian
  eID card via PKCS#11, stamping a visible vignette (cardholder photo + name +
  date) on each document. One PIN entry per document.
- **`azure`** — personal *advanced* (AES) signatures with the user's own
  certificate held in Azure Key Vault, after a single Microsoft Entra ID login
  per batch; only the document digest ever leaves the machine.
- **`image`** — a simple image stamp (no cryptographic value).

Signatures are **PAdES up to B-LTA** by default: trusted RFC 3161 timestamp,
embedded revocation info (LTV), and an archival timestamp chain — with trust
anchors drawn from the EU Trusted List (eID) or the organisation's internal CA
(Azure). Every signed file is re-validated on the spot and the achieved level
is reported; levels are never silently downgraded.

Batches are validated against a template PDF (exact page count + dimensions)
before signing, outputs never overwrite existing files, and the whole thing
ships as two standalone PyInstaller binaries (windowed GUI + headless CLI) for
Linux and Windows.

> ⚖️ The eID mode uses the card's **non-repudiation** certificate, legally
> equivalent to a handwritten signature. The **national register number** is
> embedded in every signature produced — mind the distribution of the signed
> PDFs.

---

## The three modes

| Mode | Requires | Nature | Output |
|---|---|---|---|
| **`beid`** | reader + eID card + PIN per document | **cryptographic** eID signature (pyHanko via the PKCS#11 middleware) — *qualified*-grade (QES) | visible **vignette**: cardholder photo + "Signed by:" / name / date |
| **`azure`** | Microsoft (Entra ID) login, one per batch + a personal key/cert in Azure Key Vault | **cryptographic** personal signature — **advanced (AES), not qualified** | visible **vignette**: "Signed by:" / name / date (no photo) |
| **`image`** | nothing | **image stamp** (this is *not* a cryptographic signature) | the supplied image, placed at a chosen position |

In all modes, the same vignette/image + page + position is applied to **all**
the documents in the batch — template validation guarantees the files are
geometrically identical. With `--page first|last` the target page is resolved
**per document** instead, which also lets the batch contain files whose page
count differs from the template (see *Template validation*).

### eID vs Azure — which one?

- **`beid` (eID)** carries the strongest legal weight (qualified certificate,
  legally equivalent to a handwritten signature) but needs the physical card,
  a reader, and **one PIN entry per document**.
- **`azure`** signs with the user's **personal certificate held in Azure Key
  Vault** after **one interactive Microsoft login per batch** — far better
  ergonomics for large batches, but the result is an **advanced** electronic
  signature (AES): fine for internal documents; for documents relied upon by
  external third parties, a publicly recognised (qualified) issuer would be
  needed.
- **`image`** is a visual stamp only — no cryptographic value.

## Template validation (`--template`)

If a template PDF is supplied, each input is accepted **only** if it has the
**same page count** AND **exactly identical per-page dimensions** (strict
equality, no tolerance). Rejected files are never signed; the rejection reason
is displayed (CLI summary / GUI table).

**Files with a different page count** (e.g. scanned annexes were appended) can
still be processed by targeting a per-document page: pass `--page first` or
`--page last` (in the GUI, a selector appears after validation whenever such
files are detected). Every file is then signed on its own first/last page at
the chosen position; that page must still have **exactly** the template's
corresponding page dimensions, so the position is guaranteed to fit. Files with
the template's page count keep the full strict check.

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
- **`azure` mode**: no hardware — outbound network to
  `login.microsoftonline.com`, the Key Vault URL, the TSA (≥ b-t) and the
  internal CA's CRL/OCSP (≥ b-lt); per-user keys provisioned in Key Vault
  (see the azure section above).
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

# Azure mode (personal Key Vault certificate, one Microsoft login per batch):
./venv/bin/python sign_pdfs_beid.py --mode azure \
  --azure-vault-url https://myorg-sign.vault.azure.net \
  --azure-trust-anchors ./internal-ca-chain.pem \
  --input ../pdfs --output ../signes

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
| `--page <N\|first\|last>` | target page: a **1-based** number, or `first`/`last` (resolved **per document**; with `--template` this also accepts files whose page count differs — their first/last page must still match the template's). Image: insertion page. beID: vignette page. |
| `--x <pt> --y <pt>` | lower-left corner, in points from the page's bottom-left. beID: **omit both ⇒ bottom-right of the last page**. |
| `--pades-level <lvl>` | PAdES baseline level: `b-b`, `b-t`, `b-lt`, `b-lta` (**default `b-lta`**). See *Signature levels* below. |
| `--timestamp-url <url>` | RFC 3161 TSA for levels ≥ b-t. Precedence: flag > `CACHET_TSA_URL` env > `http://timestamp.digicert.com`. |
| `--trust-list-url <url>` | EU LOTL URL seeding the LTV trust anchors. Precedence: flag > `CACHET_LOTL_URL` env > the official EU URL. |
| `--refresh-trust-list` | force re-download of the EU trusted list (bypass the 24 h cache). |
| `--digest <alg>` | signature digest: `sha256` (default), `sha384`, `sha512`. |
| `--no-verify` | skip the post-signing self-verification (levels ≥ b-t). |
| `--pades` | **deprecated no-op** (PAdES is now the default); use `--pades-level`. |
| `--legacy-cms` | **deprecated**: legacy non-PAdES `adbe.pkcs7.detached` signature (no timestamp, no LTV). Incompatible with levels above b-b. |
| `--lib <path>` | path to the eID PKCS#11 library (otherwise OS-default value). |
| `--field <name>` | base name of the signature field (beid/azure modes). |
| `--azure-vault-url <url>` | Key Vault URL (**required** in azure mode; env `CACHET_AZURE_VAULT_URL`). |
| `--azure-key-name <name>` | explicit key override — bypasses the per-user derivation and is **flagged** in the output (env `CACHET_AZURE_KEY_NAME`). |
| `--azure-key-name-template <tpl>` | per-user key derivation, default `sig-{upn}`; placeholders `{upn}`, `{upn_local}`, `{oid}`, sanitised to the Key Vault charset (env `CACHET_AZURE_KEY_NAME_TEMPLATE`). |
| `--azure-cert-name <name>` | certificate name if it differs from the key name (env `CACHET_AZURE_CERT_NAME`). |
| `--azure-auth <m>` | `interactive` (browser; GUI default), `device-code` (CLI default), `default` (`DefaultAzureCredential` — testing/CI only, **breaks the per-user model**). Env `CACHET_AZURE_AUTH`. |
| `--azure-trust-anchors <path>` | PEM/DER file or directory with the **internal CA chain** used as LTV trust anchors in azure mode (required for b-lt/b-lta and for self-verification at b-t). Env `CACHET_AZURE_TRUST_ANCHORS`. |
| `--azure-graph` | opt-in: use the Microsoft Graph `/me` displayName for the vignette. |

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

### Azure mode (`--mode azure`) — personal AES from Key Vault

- **What it is**: each user signs **in their own name** with their personal
  certificate + non-exportable key held in **Azure Key Vault**, after an
  interactive **Microsoft Entra ID** login (one per batch — not per document).
  Only the document **digest** is sent to Azure; the document never leaves
  the machine. The result is an **advanced electronic signature (AES)** —
  appropriate for internal documents; it is *not* a qualified signature (QES).
- **Prerequisite (Azure admin)**: provision, per user, a Key Vault key +
  certificate issued by the organisation's internal CA (ADCS / Entra-issued),
  named after the key template (default `sig-{upn}`, e.g.
  `sig-jane-doe-example-org`), and grant each user `get` on certificates and
  `sign` on their own key (access policy / RBAC). Provisioning itself is out
  of scope for this tool.
- **Per-user rule**: the key name is derived from the *signed-in* user's UPN,
  so a user can only sign with their own key; an explicit `--azure-key-name`
  bypass is visibly flagged, and Key Vault access policy remains the hard
  authorization gate.
- **Trust/LTV**: azure mode builds its validation context from the **internal
  CA chain** (`--azure-trust-anchors`) — the EU trusted list is *not* used
  here (it is eID-specific). For b-lt/b-lta the internal CA must publish
  reachable **CRL/OCSP** endpoints, otherwise signing fails with a clear
  error (never a silent downgrade).
- **Network**: `login.microsoftonline.com`, the vault URL, the TSA (≥ b-t)
  and the internal CA's CRL/OCSP endpoints (≥ b-lt) must be reachable.

> Backward compatibility: the legacy positional form `inputs… output_folder`
> is still accepted if `--input`/`--output` are absent.

## Usage — graphical interface

A vertical wizard walks through the flow: **1.** template → **2.** files →
**3.** output folder → **4.** validation (pass/fail table; if some files have a
**different page count** than the template, a selector appears to sign every
file on its own **first or last page**) → **5.** mode
(eID/image/**Azure** + PAdES level selector, default `b-lta`; in Azure mode a
panel offers the vault settings and a **"Sign in with Microsoft"** action) →
**6.** page + position (actual page preview, click to
place; locked onto the first/last page while the step-4 selector applies) →
**7.** launch → **8.** per-document summary.

---

## Standalone executables (Linux & Windows)

The project compiles into **two standalone binaries** per OS (a windowed GUI
`cachet`, a console CLI `cachet-cli`) — no Python required on the target
machine. Official builds are published as **GitHub Releases**: merging
`develop` into `main` automatically tags `v{version}` and attaches the
Linux/Windows packages (see *Release process* in BUILD.md). See **[BUILD.md](BUILD.md)** for all the routes (native Linux, native
Windows, Wine, and GitHub Actions CI).

```bash
./build_linux.sh        # Linux  -> dist/cachet , dist/cachet-cli
build_windows.bat       # Windows -> dist\cachet.exe , dist\cachet-cli.exe
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
| `trust.py` | EU trusted-list (LOTL) trust provider: anchors for LTV in beid mode, with local cache. |
| `azure_signer.py` | azure mode: Entra ID login, per-user Key Vault key/cert resolution, pyHanko signer (digest-only signing). |
| `gui.py` | CustomTkinter interface (façade over the core). |
| `gui_main.py` | entry point of the windowed binary (opens the GUI). |
| `test_sign_pdfs_beid.py`, `test_trust.py`, `test_azure.py` | `unittest` test suites. |
| `cachet.spec` | PyInstaller recipe (two binaries). |
| `build_*.sh` / `build_windows.bat` | build scripts. |
| `.github/workflows/build.yml` | CI: Windows + Linux binaries as artifacts. |
| `BUILD.md` | detailed packaging guide. |
