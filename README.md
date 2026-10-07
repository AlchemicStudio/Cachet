# Cachet

Batch PDF signing for Belgian administrative workflows, usable from
both a **command line** and a **CustomTkinter GUI**, with three modes:

- **`beid`** — qualified (QES-grade) cryptographic signatures with the Belgian
  eID card via PKCS#11, stamping a visible vignette (cardholder photo + name +
  date) on each document. One PIN entry per document.
- **`azure`** — personal *advanced* (AES) signatures with the user's own
  certificate held in Azure Key Vault, after a single Microsoft Entra ID login
  per batch; only the document digest ever leaves the machine.
- **`image`** — **visual signatures only** (no cryptographic value): typed
  text in the font and colour of your choice, images, or a hand-drawn
  signature — one or several per document, each on a given page, on the
  first/last page or **on every page** (initials).

`beid` and `azure` can carry the same visual signatures: they are stamped
first, and the cryptographic signature then covers them.

Signatures are **PAdES up to B-LTA** by default: trusted RFC 3161 timestamp,
embedded revocation info (LTV), and an archival timestamp chain — with trust
anchors drawn from the EU Trusted List (eID) or the organisation's internal CA
(Azure). Every signed file is re-validated on the spot and the achieved level
is reported; levels are never silently downgraded.

Batches are validated against a template PDF (exact page count + dimensions)
before signing, outputs never overwrite existing files, and the whole thing
ships as two standalone PyInstaller binaries (windowed GUI + headless CLI) for
Linux and Windows.

The GUI is a **step-by-step wizard** (template → documents → validation →
output folder → signature type → placement → signing → report) with contextual
help on every step, available in **English, French, Dutch, German, Spanish and
Portuguese** (language selector on the welcome screen and at the top of every
wizard step; the system language is picked by default). It **remembers** your
visual signatures, their last positions and your settings between sessions
(see *Saved profile*).

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
| **`image`** | nothing | **visual signatures** — typed text, images, drawings (this is *not* a cryptographic signature) | the elements you defined, each at its own position |

A run applies **a list of elements**: the vignette of the cryptographic
signature (`beid`/`azure`) and/or any number of visual signatures, each with
its own target — page *N*, the first page, the last page or every page — and
its own position. The same list is applied identically to **all** the
documents in the batch — template validation guarantees the files are
geometrically identical. A `first`/`last` target is resolved **per document**,
which also lets the batch contain files whose page count differs from the
template (see *Template validation*). Only **one** cryptographic signature is
made per document, and the visual signatures are always stamped **before** it,
never after.

### Documents that are already signed

A visual signature rewrites page content, so it **invalidates any signature
the document already carries**. An input that already holds a signature or a
document timestamp is therefore handled as follows:

- **`beid`/`azure` with visual signatures** — the document is refused before
  anything is signed (no PIN prompt, nothing written) and reported as failed
  (`the document is already signed: a visual signature would invalidate the
  existing signature(s)…`). The rest of the batch continues.
- **`beid`/`azure` without visual signatures** — the document is
  countersigned and the earlier signature stays valid. A signature field name
  can be used only once per document: to countersign a file that Cachet
  already signed, give the new signature another base name with `--field`
  (the GUI always uses the default name, so it cannot countersign Cachet's own
  output).
- **`image`** — the document is stamped, as it always was, and its report
  line ends with `WARNING: the document was already signed; its existing
  signature(s) are no longer valid`.

The post-signing self-verification only checks the signature Cachet just
created; it says nothing about earlier ones.

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
- **`image`** adds visual signatures only — an appearance, with no
  cryptographic value.

## Template validation (`--template`)

If a template PDF is supplied, each input is accepted **only** if it has the
**same page count** AND **exactly identical per-page dimensions** (strict
equality, no tolerance). Rejected files are never signed; the rejection reason
is displayed (CLI summary / GUI table).

**Files with a different page count** (e.g. scanned annexes were appended) are
accepted only when **every element of the run targets the first or the last
page**: each visual signature (`--page first|last`, or `"page_anchor"` in a
`--signatures` file) and, in `beid`/`azure`, the vignette. Each of those pages
must still have **exactly** the template's corresponding page dimensions, so
the positions are guaranteed to fit. An element on a numbered page or on every
page rejects such a file, and the reason names it, e.g. `1 page(s), the
template has 2; signature #2 targets every page (only first/last page targets
accept a different page count)`. For the vignette, `--page first|last` must be
explicit: the default bottom-right vignette does **not** count as a last-page
target (a safety check is never relaxed silently). Files with the template's
page count keep the full strict check.

In the GUI, a selector appears after validation whenever such files are
detected: it puts **every** element on each document's own first or last page.

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
- **Text signatures** (every mode): no new dependency — the text is rendered
  by Pillow. Six fonts under the SIL Open Font License are **bundled**
  (`fonts/`, licence texts in `fonts/licenses/`): Great Vibes, Dancing Script,
  Caveat, Sacramento (handwriting), Lato (sans) and Libre Baskerville (serif).
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

# Text signature (no card): two lines, on the last page of each document:
./venv/bin/python sign_pdfs_beid.py --mode image \
  --text "Jane Doe\nRead and approved, {date}" --font great-vibes \
  --color "#1A2B8C" --page last --x 360 --y 120 \
  --input ../pdfs --output ../signes

# Several visual signatures (texts, images, initials on every page):
./venv/bin/python sign_pdfs_beid.py --mode image --signatures sigs.json \
  --input ../pdfs --output ../signes

# The same visual signatures, then the eID signature that covers them:
./venv/bin/python sign_pdfs_beid.py --mode beid --signatures sigs.json \
  --input ../pdfs --output ../signes

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
| `--mode beid\|azure\|image` | signature mode (default `beid`). `image` = visual signatures only. |
| `--signatures <file.json>` | JSON list of visual signatures (see *Visual signatures* below). Works in **every** mode; in beid/azure they are applied before the cryptographic signature. |
| `--text <text>` | text signature for `--mode image`, placed with `--page/--x/--y`. `\n` starts a new line; `{date}` and `{filename}` are replaced per document. |
| `--font <id\|path>` | font of `--text`: `great-vibes` (default), `dancing-script`, `caveat`, `sacramento`, `lato`, `libre-baskerville`, or the path of a `.ttf`/`.otf` file. |
| `--color <#RRGGBB>` | colour of `--text` (default `#1A2B8C`). |
| `--font-size <pt>` | font size of `--text` in points (default 24, at most 400). |
| `--image-path <img>` | image to stamp in `--mode image` — the legacy single-image form, 150 pt wide, placed with `--page/--x/--y`. Ignored in beid/azure. |
| `--page <N\|first\|last>` | target page: a **1-based** number, or `first`/`last` (resolved **per document**). image mode: page of the `--text` / `--image-path` signature (default 1). beid/azure: vignette page. With `--template`, see *Template validation* for files whose page count differs. |
| `--x <pt> --y <pt>` | lower-left corner, in points from the page's bottom-left. image mode: the `--text` / `--image-path` signature (default 0, 0). beid/azure: the vignette — **omit both ⇒ bottom-right of the last page**. |
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

Which flags define the visual signatures of a run, and what `--page/--x/--y`
place:

| Mode | Flags | Visual signatures of the run | `--page/--x/--y` belong to |
|---|---|---|---|
| image | `--image-path` | 1 image | that image |
| image | `--text` (`--font`, `--color`, `--font-size`) | 1 text | that text |
| image | `--signatures` | the file's entries | — (giving them is an error) |
| image | `--signatures` + `--image-path` or `--text` | that image/text **first**, then the file's entries | the image/text |
| image | `--text` + `--image-path` | error (use `--signatures` for several elements) | |
| image | none of the three | error | |
| beid/azure | `--signatures` | the file's entries, applied before the signature | the vignette |
| beid/azure | `--text` (or `--font`/`--color`/`--font-size`) | error (use `--signatures`) | |
| beid/azure | `--image-path` | none (ignored) | the vignette |
| any | `--font`/`--color`/`--font-size` without `--text` | error | |

### Visual signatures (`--text`, `--signatures`)

A visual signature is a **text** or an **image** stamped on one or several
pages. It is an appearance only: by itself it proves nothing. In `beid` and
`azure` modes the visual signatures are stamped first, into the same revision
that is then signed, so the cryptographic signature covers them — a signed
output is never stamped afterwards.

A `--signatures` file is a **UTF-8 JSON array** of objects, one per visual
signature (a byte-order mark is accepted, e.g. PowerShell's
`Out-File -Encoding utf8`):

```json
[
  {"kind": "text", "text": "Jane Doe\nRead and approved, {date}",
   "font": "great-vibes", "color": "#1A2B8C", "font_size": 24,
   "page_anchor": "last", "x": 360, "y": 120},
  {"kind": "text", "text": "JD", "font": "caveat", "font_size": 14,
   "all_pages": true, "x": 540, "y": 20},
  {"kind": "image", "image_path": "stamp.png", "width_pt": 120,
   "page": 1, "x": 60, "y": 700}
]
```

| Key | Type | Applies to | Default | Notes |
|---|---|---|---|---|
| `kind` | `"text"` \| `"image"` | all | required | |
| `text` | string | text | required | Line breaks are allowed. `{date}` → `dd/mm/YYYY` (local date of the run); `{filename}` → name of the source file without `.pdf`. Any other brace is left as it is. At most 2000 characters. |
| `font` | string | text | `"great-vibes"` | A bundled id (`great-vibes`, `dancing-script`, `caveat`, `sacramento`, `lato`, `libre-baskerville`) or the path of a `.ttf`/`.otf` file. A relative path resolves against the folder of the JSON file (the `--font` flag: against the current folder). |
| `color` | `"#RRGGBB"` | text | `"#1A2B8C"` | |
| `font_size` | number | text | `24` | points, greater than 0, at most 400 |
| `image_path` | string | image | required | A relative path resolves against the folder of the JSON file; `~` is expanded. |
| `width_pt` | number | image | `150` | points, greater than 0, at most 5000; the height follows the aspect ratio |
| `page` | integer ≥ 1 | all | — | give exactly **one** of `page`, `page_anchor`, `all_pages: true` |
| `page_anchor` | `"first"` \| `"last"` | all | — | resolved per document |
| `all_pages` | boolean | all | `false` | every page of each document (initials) |
| `x`, `y` | number | all | required | lower-left corner, points from the page's bottom-left |
| `enabled` | boolean | all | `true` | `false` → the entry is skipped |

Unknown keys are ignored, so an entry copied from the GUI's `profile.json`
(which adds `id`, `label`, `placed_on`) is accepted as it is — only the
`image_path` of an image entry has to be rewritten to a real path. A bad
entry stops the run before anything is processed, with its position in the
file (`sigs.json: signature #2: …`).

Good to know:

- **Text is drawn as a picture.** It is rendered at 300 dpi with a
  transparent background and cropped tightly to its ink; no font is embedded
  in the PDF. `(x, y)` is the lower-left corner of that ink box, and the text
  grows right and up from it. With `{date}` or `{filename}` the box is
  measured per document, so its size — and the baseline, by the depth of the
  descenders — can vary slightly from one document to the next.
- **Missing characters.** A character the chosen font does not contain is
  drawn as an empty box or, depending on the font (Sacramento, for
  instance), not drawn at all — without any error. A text none of whose
  characters can be drawn is refused as `text is empty`. Check the result, or
  point `font` at a `.ttf`/`.otf` file that covers your script.
- **Limits.** A text whose rendering would exceed 40 megapixels at 300 dpi is
  refused with a clear error (reduce the size or the length). Images are
  embedded at no more than 600 dpi of their width on the page (a large photo
  is downsampled), with their EXIF orientation applied; the alpha channel
  of a PNG (and palette transparency) is kept.
- **Blank pages** are stamped like any other.
- **The command line never reads the GUI's saved profile**: a headless run
  depends on its flags and files only.

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
- **Offline behaviour**: `b-b` and `image` mode work fully offline (visual
  signatures never need the network, in any mode). Levels
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

The app opens on a **welcome screen** (overview + language selector —
English, French, Dutch, German, Spanish, Portuguese); **Start** launches a
wizard. Both screens share a top bar with the **Cachet logo and name** on the
left and, on the right, the **language selector** (switching re-renders the
current step in place, nothing entered is lost) next to a **♥ Support
Cachet** link that opens the payment page in the browser. The wizard adds a
stepper (completed
steps green, problems red, unreached steps disabled), the current step's form
on the left, contextual help on the right, and Previous/Next buttons that
name the target step. **Cancel** asks for confirmation, then discards the
current session (template, files, validation, results) and returns to the
welcome screen — your saved signatures and settings are kept.

The 8 steps: **1.** template (with a single PDF to sign, pick that document
itself as the template) → **2.** documents → **3.** validation (pass/fail
table; if some files have a **different page count** than the template, a
selector appears to sign every file on its own **first or last page**) →
**4.** output folder → **5.** signature type (eID / Azure / **visual
signature (text / image)** + PAdES level selector, default `b-lta`; in Azure
mode a panel offers the vault settings and a **"Sign in with Microsoft"**
action; **"Full documentation"** opens the localized reference — modes, PAdES
levels, AES vs QES, glossary — ending with clickable links to the source
standards) → **6.** placement, a small **editor** (see below) → **7.**
signing (summary listing the vignette placement and every visual signature, a
green **"insert your eID card"** reminder in eID mode, progress) → **8.**
per-document report with an **"Open output folder"** button.

**Step 6 — the element editor.** On the left, the list of **elements** stamped
on every document: in eID/Azure modes the **signature vignette** comes first
(bottom-right of the last page unless you place it), followed by your visual
signatures, each with a tick box (untick one to keep it in your library
without using it). **Add text…** opens a dialog (text on one or several lines,
font, size, colour, with a live preview and the size the text will have on
the page), **Add image…** picks a picture, **Draw…** lets you sign by hand
with the mouse or a stylus (transparent background); **Edit…** and **Delete**
act on the selected signature. On the right, the **actual page preview** shows
every element at its real size: **select an element in the list, then click
on the page** — the click sets its lower-left corner. **On every page
(initials)** repeats the selected signature on all pages, a **target page**
field moves it to another page, and **Reset position** un-places it. The step
is complete when every ticked signature is placed (the visual-signature mode
needs at least one). When page counts differ, the first/last selector is also
shown at the top of this step: every element then goes on that page, the
preview is locked onto it and "On every page" is disabled.

### Saved profile

The GUI saves, on every change, and reloads at the next start:

- your **signature library** — texts (with font, colour and size), imported
  images and drawings, with their name and tick box;
- each signature's **last placement** (page, position), re-applied only when
  the page of the current template has exactly the size it was placed on —
  otherwise the signature simply reads "not placed" and nothing is lost;
- your **settings**: language, signature mode, PAdES level, output folder,
  Azure vault URL / sign-in method / trust-anchors file, and the position of
  the eID/Azure vignette.

Where it lives:

| OS | `profile.json` | Images (`signatures/`) |
|---|---|---|
| Linux | `~/.config/Cachet/` (or `$XDG_CONFIG_HOME/Cachet/`) | `~/.local/share/Cachet/signatures/` (or `$XDG_DATA_HOME/Cachet/…`) |
| Windows | `%LOCALAPPDATA%\Cachet\` | `%LOCALAPPDATA%\Cachet\signatures\` |
| macOS | `~/Library/Application Support/Cachet/` | `~/Library/Application Support/Cachet/signatures/` |

Set `CACHET_CONFIG_DIR` and/or `CACHET_DATA_DIR` to use other folders (the
first holds `profile.json`, the second `signatures/`).

- Everything is stored **unencrypted** (files readable by your user account
  only on Linux/macOS). **Never stored**: PIN codes, sign-in tokens or any
  other credential, the Azure key name, the signed-in account, the template
  and the documents.
- Imported images and drawings are **copied** into `signatures/`, so moving
  or deleting the original does not break a saved signature. A custom font
  file (`.ttf`/`.otf`) is only referenced by its path: if it disappears, the
  signature is shown as unusable until you edit or delete it.
- **Cancel** and **Finish** keep the profile; **Delete** removes a signature
  and its stored image.
- Precedence for the Azure vault URL and trust anchors: your saved choice,
  then the `CACHET_AZURE_*` environment variables, then the built-in default.
  A saved output folder or trust-anchors file that no longer exists is
  ignored.
- The **command line never reads this profile**.

Limits, stated plainly: an image that cannot be copied into the user folder
is not added (the reason is shown). A profile that cannot be read (damaged
file, or written by a newer version) is set aside as `profile.json.bak` and
the app starts with an empty library; if it can be neither read nor moved
(access denied, for instance) it is left untouched and nothing is saved over
it until it is readable again. After that, or when the profile was
edited by hand, image files that no signature references any more can remain
in `signatures/`: Cachet never sweeps that folder, because a backup or
another profile may still use them. **To erase everything, delete the folders
above** (a single folder on Windows and macOS; on Windows it also holds the
cached EU trusted list, which is simply downloaded again).

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
(PDFium bundled via pypdfium2), and the six fonts of the text signatures are
bundled in both binaries.

## Tests

Headless `unittest` suites (no card, no network, no display — the `Gui*`
classes skip themselves):

```bash
env -u DISPLAY ./venv/bin/python -m unittest -v
```

GUI suites (they drive the real wizard, the editor dialogs and a batch
through the worker thread — the end-to-end layer of this desktop app). Run
them **one at a time**, under a virtual display when available:

```bash
CACHET_REQUIRE_GUI=1 XMODIFIERS=@im=none xvfb-run -a ./venv/bin/python -m unittest -v -k Gui
```

With `CACHET_REQUIRE_GUI=1`, a GUI test that cannot run (no display, broken
Tk, `gui.py` that no longer imports) **fails** instead of being skipped — a
green `-k Gui` run with skips proves nothing. Without `xvfb-run`, use a real
display (`DISPLAY=:0`).

The tests never touch your real profile: each one works in temporary
folders. The same rule applies to **manual** runs of the GUI during
development — point it at scratch folders first, otherwise it loads and
auto-saves your own profile:

```bash
export CACHET_CONFIG_DIR=/tmp/cachet-dev/cfg CACHET_DATA_DIR=/tmp/cachet-dev/data
./venv/bin/python sign_pdfs_beid.py --gui
```

## Project structure

| File | Role |
|---|---|
| `sign_pdfs_beid.py` | core + CLI entry point (business logic, importable without tkinter). |
| `stamps.py` | visual signatures: model, bundled fonts, text/drawing rendering, the `--signatures` JSON, stamping onto a PDF (importable without tkinter). |
| `profile_store.py` | the GUI's saved profile: signature library, last placements, settings, and the image store (never read by the CLI). |
| `trust.py` | EU trusted-list (LOTL) trust provider: anchors for LTV in beid mode, with local cache. |
| `azure_signer.py` | azure mode: Entra ID login, per-user Key Vault key/cert resolution, pyHanko signer (digest-only signing). |
| `gui.py` | CustomTkinter interface: landing page + 8-step wizard with the step-6 element editor (façade over the core). |
| `i18n.py` | GUI localization catalog (EN/FR/NL/DE/ES/PT), light `**bold**` markup helper, documentation sections/sources. |
| `i18n_docs.py` | long-form documentation of the "Full documentation" popup, six languages (merged into the catalog). |
| `gui_main.py` | entry point of the windowed binary (opens the GUI). |
| `fonts/` | the six bundled OFL fonts for text signatures; `fonts/licenses/` holds their licence texts (shipped in both binaries). |
| `test_sign_pdfs_beid.py`, `test_stamps.py`, `test_profile_store.py`, `test_trust.py`, `test_azure.py`, `test_i18n.py` | `unittest` test suites. |
| `cachet.spec` | PyInstaller recipe (two binaries). |
| `build_*.sh` / `build_windows.bat` | build scripts. |
| `.github/workflows/build.yml`, `release.yml` | GitHub Actions: tests (headless + GUI suites under Xvfb), Windows + Linux binaries as artifacts, and the release on `main`. |
| `.gitlab-ci.yml`, `.forgejo/workflows/tests.yml` | GitLab CI and Forgejo Actions: the same tests (no build, no release). |
| `BUILD.md` | detailed packaging guide. |
