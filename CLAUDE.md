# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A batch PDF-signing app for Belgian electronic identity cards (eID), with two
signature modes, template-based input validation, and a CustomTkinter GUI that
wraps the same logic as the headless CLI.

- **`beid` mode** — cryptographic eID signature (pyHanko over the eID PKCS#11
  middleware), with a visible **vignette** (cardholder photo + "Signed by:" /
  name / date) bottom-right of the last page. Signatures are **PAdES,
  default level B-LTA** (timestamp + embedded revocation info + archival
  timestamp chain), selectable via `--pades-level`; levels ≥ b-t need network.
- **`azure` mode** — personal **AES (advanced, not qualified)** signature with
  the signed-in user's own certificate + non-exportable key in **Azure Key
  Vault**, after ONE interactive Microsoft Entra ID login per batch. Only the
  digest is sent to Azure. Reuses the PAdES level pipeline; LTV trust comes
  from the **internal CA chain** (`--azure-trust-anchors`), NEVER the EU LOTL.
- **`image` mode** — stamps a user-supplied image onto a chosen page at a chosen
  position. See the design note below: **image mode does NOT use the card** and
  is not a cryptographic signature; it is the alternative to `beid`.

No build system, no linter config. Tests use stdlib `unittest`.
Code comments and CLI text are in English; the GUI is **localized**
(EN/FR/NL/DE/ES/PT) through `i18n.py` — every user-visible GUI string goes
through `i18n.tr(key, **fmt)`, never a hard-coded literal.

## Modules

- `sign_pdfs_beid.py` — **core + CLI entry point**. All business logic lives
  here (eID signing, vignette, image insertion, validation, placement math,
  `RunConfig`/`process_batch`, arg parsing). Imports cleanly **without tkinter**.
- `gui.py` — CustomTkinter GUI: a **landing page + 8-step wizard** (see "GUI
  workflow"). Imported **only** when `--gui` is passed (lazy), so the
  CLI/core/tests never require a display. It is a thin façade over the core;
  all non-widget logic it needs is imported from the core, all its text from
  `i18n.py`.
- `i18n.py` — GUI localization: `LANGUAGES` (en/fr/nl/de/es/pt),
  `tr(key, **fmt)`, `set_language`, `system_language` and the UI `CATALOG`
  (key → per-language strings). Texts may carry a light **`**bold**`**
  markup; `split_markup(text)` (pure) turns it into `(segment, is_bold)`
  pairs that the GUI renders through a text tag. `DOC_SECTIONS` /
  `DOC_SOURCES` (title key → URL) drive the "Full documentation" popup.
  Import-safe **without tkinter**; `test_i18n.py` enforces that every key
  exists in every language, that placeholders match the English reference,
  that bold markers are balanced with the same count per language, and that
  the documentation is genuinely translated — add new GUI strings in ALL
  six languages.
- `i18n_docs.py` — the long-form documentation of the popup (`DOCS_CATALOG`:
  `docs.modes/levels/tiers/glossary/glance`, `docs.sources_*`, `docs.src.*`
  link titles) in six languages, merged into `i18n.CATALOG` at import so
  `tr()` and the test invariants cover it. Kept apart so `i18n.py` stays
  readable; bundled into the GUI binary through the import chain.
- `trust.py` — EU trusted-list (LOTL, ETSI TS 119 612) trust provider for LTV:
  LOTL → Belgian list → granted CA/QC-for-eSignatures certs as
  `ValidationContext` anchors; JSON cache under `platformdirs` (24 h TTL,
  `--refresh-trust-list`); raises actionable `TrustListError` offline.
  Import-safe without tkinter; network injectable (`fetcher=`) for tests.
  **beid-mode only** (azure uses the internal CA instead).
- `azure_signer.py` — azure mode: Entra credential factory + process-wide
  cache (`get_cached_credential` — one login per batch, shared with the GUI
  sign-in), token-claims user resolution (UPN/oid, decoded locally, never
  logged), per-user key/cert name template (`sig-{upn}`, sanitised; explicit
  override flagged), `AzureKeyVaultSigner` (pyHanko `Signer`: hashes locally,
  sends ONLY the digest to `CryptographyClient.sign`, maps key type +
  `--digest` to RS256/…/ES256/…, converts ECDSA r||s → DER). Azure SDK
  imports are lazy; clients injectable for tests.
- `test_sign_pdfs_beid.py`, `test_trust.py`, `test_azure.py`, `test_i18n.py`
  — headless `unittest` suites (no card, no network; the `Gui*` classes need
  a display and skip themselves otherwise).
- `pdfs/` (`../pdfs`), `signes/` (`../signes`) — sample inputs / output dir.
- `venv/` — Python virtualenv (see "tkinter in this environment").

## Commands

```bash
# eID signing (vignette), new flat flags — PAdES B-LTA by default:
./venv/bin/python sign_pdfs_beid.py --input ../pdfs --output ../signes --mode beid

# eID signing, offline basic level; legacy positional form (still supported)
./venv/bin/python sign_pdfs_beid.py ../pdfs ../signes --pades-level b-b

# azure mode (personal Key Vault cert; one Microsoft login per batch):
./venv/bin/python sign_pdfs_beid.py --mode azure \
  --azure-vault-url https://myorg-sign.vault.azure.net \
  --azure-trust-anchors ./internal-ca-chain.pem \
  --input ../pdfs --output ../signes

# image stamp (no card), with template validation:
./venv/bin/python sign_pdfs_beid.py --mode image \
  --template ../pdfs/MODELE.pdf --input ../pdfs --output ../signes \
  --image-path signature.png --page 1 --x 360 --y 150

# GUI:
./venv/bin/python sign_pdfs_beid.py --gui

# Tests (headless, no card):
./venv/bin/python -m unittest -v

# Syntax check everything:
./venv/bin/python -m py_compile sign_pdfs_beid.py gui.py gui_main.py trust.py azure_signer.py i18n.py i18n_docs.py test_sign_pdfs_beid.py test_trust.py test_azure.py test_i18n.py
```

### CLI flags

| flag | meaning |
|------|---------|
| `--gui` | launch the GUI; otherwise run headless. Running without `--gui` never opens a window. |
| `--input <path…>` | files and/or directories to process (dirs are globbed for `*.pdf`). |
| `--output <dir>` | output folder; files are written `{stem}_signe.pdf`, **never overwriting** (collisions get ` - 1`, ` - 2`, …). |
| `--template <pdf>` | model PDF; if given, inputs are validated against it (CLI **and** GUI). |
| `--mode beid\|image\|azure` | signature mode (default `beid`). |
| `--azure-vault-url` / `--azure-key-name` / `--azure-key-name-template` / `--azure-cert-name` / `--azure-auth` / `--azure-trust-anchors` / `--azure-graph` | azure mode config; each falls back to its `CACHET_AZURE_*` env var. Vault URL required; key derived per-user from the template (default `sig-{upn}`; explicit override flagged); auth `interactive`/`device-code` (CLI default)/`default` (CI only, breaks per-user model); trust anchors = internal CA PEM/DER file-or-dir, required when LTV/verification needs them. |
| `--image-path <img>` | image to stamp (**required** for `--mode image`). |
| `--page <N\|first\|last>` | target page. A number is **1-based**; `first`/`last` (→ `RunConfig.page_anchor`) are resolved **per document** (index 0 / -1) and, with `--template`, accept files whose page count differs from the template (see validation rules). Image: insertion page. beID: vignette page (with `--x/--y`; with `first`/`last` and no position the default bottom-right vignette moves to that page). |
| `--x <pt> --y <pt>` | lower-left position, points from the page's bottom-left. Image: image corner. beID: vignette corner — **omit both → default bottom-right of last page**. |
| `--pades-level {b-b,b-t,b-lt,b-lta}` | PAdES baseline level, **default `b-lta`**. b-t+ needs a TSA; b-lt+ embeds OCSP/CRL (LTV) using EU-trusted-list anchors. Never silently downgraded on network failure. |
| `--timestamp-url` / `--trust-list-url` | RFC 3161 TSA and EU LOTL overrides. Precedence: flag > env (`CACHET_TSA_URL` / `CACHET_LOTL_URL`) > default (DigiCert free TSA / official EU LOTL). Free TSA = technically valid but NOT qualified timestamps. |
| `--digest {sha256,sha384,sha512}` | digest, pinned as `md_algorithm` (default sha256). |
| `--no-verify` | skip post-signing self-verification (levels ≥ b-t); also skips the trusted-list fetch at b-t. |
| `--refresh-trust-list` | bypass the 24 h LOTL cache. |
| `--pades` | deprecated no-op alias (warns); PAdES is the default now. |
| `--legacy-cms` | deprecated; old `adbe.pkcs7.detached` path, only combinable with level b-b. |
| `--lib` / `--field` | eID options (as before): PKCS#11 lib path, field name. |

Backward compatibility: `--input`/`--output` take precedence; if absent, the
legacy positional form `inputs… output_dir` is used. `resolve_config()` does
this resolution and `validate_config()` rejects bad combos (e.g. `image` mode
without `--image-path`). Both are pure and unit-tested.

## Shared core: `process_batch`

Both the CLI (`main`) and the GUI call `process_batch(cfg: RunConfig, on_progress=…)`:
1. If `cfg.template` is set, read its per-page dimensions once.
2. For `beid` mode, open **one** PKCS#11 session, build `BEIDSigner`, read the
   identity once (no PIN), and build the network material once via
   `build_signing_material(cfg)` → `SigningMaterial` (an `HTTPTimeStamper`
   for levels ≥ b-t; trust anchors + a fetching `ValidationContext` for
   b-lt/b-lta — anchors are passed as `extra_trust_roots` because pyHanko
   validates the TSA chain against the same context). The trust source is
   **mode-dependent**: beid → EU LOTL (`trust.py`); azure → internal CA
   (`load_trust_anchor_certs`). A `TrustListError`/`ValueError` here fails
   the whole batch — the level is NEVER silently downgraded.
   For `azure` mode the setup-once path is: `get_cached_credential` →
   `acquire_user` (ONE login per batch) → `resolve_key_names` →
   `build_azure_signer` → `read_cert_identity` (vignette name from the cert
   subject or Graph displayName; `photo=None`).
3. Per input: validate against the template (if any) → reject+report on failure;
   else `sign_one()` (beid) or `insert_image_one()` (image). Returns a
   `DocResult` per file (`ok`, `detail`). `on_progress` fires per document so
   the GUI can stream rows into its summary table.
4. beid + levels ≥ b-t (unless `--no-verify`): `verify_signed_pdf()` re-opens
   the output, validates it (pyHanko validation API + EU anchors) and detects
   the **achieved** level structurally (timestamp → b-t, DSS revinfo → b-lt,
   doc-timestamp → b-lta); the result lands in `DocResult.detail`
   (e.g. `PAdES-B-LTA, LTV ok`); `SelfVerificationError` ⇒ document FAILED.

The level → `PdfSignatureMetadata` mapping is the pure
`signature_meta_kwargs(level, digest, legacy_cms=…, validation_context=…)`
(unit-tested for all four levels + legacy CMS).

The **same** image/vignette + page + (x, y) is applied to every document —
validation guarantees the files are geometrically identical, so one placement
fits all. With `cfg.page_anchor` (`"first"`/`"last"`, exclusive with
`cfg.page`) the page is resolved **per document** (`anchor_page_index`: 0 /
-1) and validation only pins the anchor page, so page counts may differ.
Output paths come from `unique_output_path()`: `{stem}_signe.pdf`,
then `{stem}_signe - 1.pdf`, ` - 2`, … on collision — **existing files are never
overwritten**.

## Template validation rules

`validate_against_template(template_dims, pdf)` rejects a file unless it has
**(a)** the same page count and **(b)** EXACTLY identical per-page dimensions —
exact float equality of `(width, height)` from each page's MediaBox, **no
tolerance**. Failures are returned as `ValidationResult(ok=False, reason=…)` and
surfaced (CLI summary / GUI table); they are never silently signed. Dimensions
come from the MediaBox (inherited through the page tree); rotation is not
considered.

With `page_anchor="first"|"last"` (CLI `--page first|last`, GUI validation-step
selector), a file whose **page count differs** from the template is accepted
**iff its anchor page exactly matches the template's anchor page** — that is
the page the signature lands on, so the chosen (x, y) is guaranteed to fit it.
Files with the template's page count keep the full strict check; a count
mismatch whose anchor page differs is rejected with both facts in the reason.
Accepted mismatches carry an informative `reason` ("N page(s), the template
has M — signed on the last page") even though `ok=True`.

## Image placement convention

`(x, y)` is the **lower-left** corner of the placed image, in PDF points from
the page's **bottom-left** corner (MediaBox origin is added internally, so it is
correct even for non-zero-origin MediaBoxes). Page numbers are **1-based** at the
CLI/GUI boundary and converted to pyHanko's 0-based (`-1` = last) internally.
The image is scaled to a fixed width `_IMG_TARGET_W_PT` (150 pt) preserving
aspect, with **no border** — `insert_image_one` sets `border_width=0` on the
`StaticStampStyle` (whose default is 3 pt black, which would frame the image).
The GUI and CLI share this convention exactly. An out-of-range `--page` is
reported as a clear per-document failure ("page N out of range…"), not a raw
pyHanko error.

**beID vignette placement.** In `beid` mode, supplying `--x/--y` (or clicking in
the GUI) places the vignette on `--page` at that lower-left point, sized to a
**3:1 landscape box of width = page_w/5** (`vignette_size_pt`). With no position,
the vignette keeps its default bottom-right corner box
(`_default_vignette_box(writer, page_index)` — the last page by default, the
anchor page when `--page first|last` is set).
`build_stamp_style(identity, box_w, box_h)` makes the photo band proportional to
the box width (`_PHOTO_BAND_FRAC`, 0.2 → 42 pt for the default 210 pt box,
unchanged), so the same layout fits both the default box and the smaller placed
box without overflowing pyHanko's layout margins.

Placement math is in pure, tkinter-free functions (`fit_frame`,
`frame_click_to_pdf_xy`, `pdf_rect_to_frame_rect`) so it is unit-tested and
reused by the GUI canvas.

## GUI workflow (`gui.py`)

`CachetApp` (a `ctk.CTk`) opens on a **landing page** (overview text, language
selector, Start bottom-right — no stepper there); Start builds the **wizard**:
a **top bar with the same language selector** (`_wizard_lang_menu`), a
stepper bar, a split body (form on the left inside a `CTkScrollableFrame`,
per-step contextual help `i18n` text on the right, rendered with
`_fill_textbox` so `**bold**` markup shows as bold), and a footer whose
Previous/Next labels **name the target step** (`nav.next`/`nav.previous`).
Cancel opens a confirm modal; confirming (and Finish on the last step) calls
`_reset_state()` and returns to the landing page. All texts come from
`i18n.tr`; the language defaults to `system_language()` and can be switched
at any step: `_on_wizard_language_change` sets the language, closes the
(language-bound) docs popup, then rebuilds the chrome and the current step
in place (`_build_wizard()` + `_goto_step(min(step, _first_incomplete()))`)
— state lives on the app, so nothing the user entered is lost. The selector
is disabled while a batch runs.

The 8 steps (`_STEP_KEYS` → `_build_step_<key>`): (1) template, (2) files,
(3) validation — auto-runs on first entry, pass/fail table, plus a localized
**first/last-page selector** (`anchor_row`, default "last") that appears only
when some files' page count differs from the template; it maps to
`RunConfig.page_anchor` for the whole batch and re-validates on change,
(4) output folder, (5) signature type (beid/azure/image radios + per-input
hints + **PAdES level selector**, default `b-lta`; azure panel appears only in
azure mode; the **"Full documentation" popup is localized**: `_show_docs_popup`
renders `i18n.DOC_SECTIONS` with bold markup, then `i18n.DOC_SOURCES` as
clickable links — `tag_bind` → `webbrowser.open`), (6) placement — when
`count_mismatch` is set, a **mirror of the step-3 first/last selector** sits
at the top (`place_anchor_row`, same shared `anchor_choice`, re-validates on
change and shows "N/M accepted" via `_update_place_anchor_widgets`), then the
page preview + click for **all** modes (image mode adds the image picker)
plus a manual **target-page field** (`page_text`) that follows the preview
when in range and only warns when beyond the template; while a page anchor
applies the field and Prev/Next are **disabled**, the preview is **locked**
onto the template's first/last page (label says "locked") and a position
clicked on another page is dropped (`_sync_anchor_page`) — `page` and
`page_anchor` stay mutually exclusive in the built `RunConfig`, (7) signing —
config summary (names the anchor when active), in beid mode a **green
"insert your eID card" box** (`card_box`, hidden by `_launch`, shown again by
`_show_card_box` when the batch fails to start), Start + progress bar,
(8) results report table + an **"Open output folder"** button
(`core.open_in_file_manager`: `os.startfile` / `open` / `xdg-open`; failures
are shown inline, never raised into the Tk loop).

Step state drives the chrome (`_refresh_chrome`): per-step predicates
`_step_complete` / `_step_error` color the stepper chips — current = accent,
passed & clean = light-green border (`_COL_DONE`), problems = light-red
(`_COL_ERROR`, e.g. rejected files on step 3, missing azure vault/anchors on
step 5), reachable-pending = gray, beyond the first incomplete step = disabled
(`_COL_LOCKED`) — a step is never shown green just because its *defaults* are
valid. Next is enabled only while the current step **and every step before
it** are complete (`i < _first_incomplete()` — the step-6 selector can
re-validate step 3 down to zero accepted files); ALL navigation (stepper,
Prev/Next, Cancel, language selector) locks while `_running`. Editing upstream state
invalidates downstream results (`validation_results`, `run_results`) so the
gating recomputes. Step content is **rebuilt on every entry** (state lives on
the app, widgets are disposable) — update helpers guard widget access with
`_alive(...)`.

Step 6's `tkinter.Canvas` shows the **actual rendered template page** as its
background (`core.render_page_image`: **pypdfium2** primary, `pdftoppm` fallback;
cached per page; falls back to a white frame only if both are unavailable).
Prev/Next change the preview page; a click sets the position AND syncs the
target-page field, drawing a to-scale placeholder — the image (image mode) or a
**3:1 box of width page_w/5** (beid/azure). The canvas **resizes with the
window**: `<Configure>` on the toplevel → `_draw_page` recomputes the canvas
size from the window (`_canvas_target_size`) and refits the page, preserving
proportions (cached full-res page image is just rescaled, so no re-render on
resize). Tables use `rowheight=30` + an explicit font so full text lines show.

Tkinter is **not thread-safe**, so the worker thread never touches widgets: it
pushes `("row"/"done"/"error", payload)` onto a `queue.Queue`, and the main
thread drains it via a periodic `self.after(100, self._poll_results)` (progress
bar + status on step 7; "done" auto-advances to the report). Calling
`self.after(...)` *from* the worker raises `main thread is not in main loop` —
do not reintroduce that. The worker catches `(Exception, SystemExit)`:
`open_eid_session()` raises **`SystemExit`** (no reader/card), which is *not* an
`Exception`, so a bare `except Exception` would let the worker die silently and
hang the GUI on "Processing…". `_poll_results` also no-ops if the
window was closed mid-batch. Guarded end-to-end tests (`GuiImageEndToEnd`,
`GuiWizardChrome`, …, skipped without a display) cover these paths.

**CTkEntry + StringVar pitfall**: `CTkEntry.destroy()` (CustomTkinter 5.2.2)
does NOT remove the trace it adds on its textvariable (radio buttons and
option menus do). Since step content is rebuilt constantly, entry-backed state
is kept in **plain strings** (`page_text`, `azure_vault`, `azure_key`) synced
via key bindings — do not "simplify" these back to shared `StringVar`s, or
every later `var.set()` fires callbacks on dead widgets (TclError spam).

## Three deliberate workarounds (beid mode) — do not "simplify" away

1. **`open_eid_session()` replaces `pyhanko_beid.open_beid_session()`** — the
   plugin hard-codes a `BELPIC` token label; the same opaque error also appears
   when no card is present. The custom opener picks `BELPIC` else the first
   token and gives distinct "no reader" / "no card" / "other label" messages.
2. **`IncrementalPdfFileWriter(inf, strict=False)`** — inputs use *hybrid*
   xref sections; strict mode refuses them (`hybrid cross-reference sections
   while hybrid xrefs are disabled`). `strict=False` is the intended escape
   hatch. Image insertion uses the same `strict=False` writer.
3. **Vignette via `signers.PdfSigner(stamp_style=…, new_field_spec=…)`** — field
   box defaults to a bottom-right corner box on the target page
   (`_default_vignette_box`, `on_page=-1` unless a page anchor moves it), or,
   when `sign_one(..., pos=(x, y))` is given, a 3:1 box of width page_w/5 on the
   chosen page; background image and text positioned independently.

## Where the signer's identity comes from (beid mode)

`read_card_identity()` reads both **without a PIN**: **name** from the
`Signature` certificate subject (`given_name` + `surname`); **photo** from the
PKCS#11 DATA object `PHOTO_FILE` (JPEG, via Pillow). The signing cert is the
**non-repudiation** cert (legally equivalent to a handwritten signature) and the
national register number is embedded in every signature — mind PDF distribution.

## Runtime requirements

- **beid mode**: eID middleware (`libbeidpkcs11.so`), reader + inserted card,
  `pcscd` running. Prompts for the **PIN once per document**, so a full eID run
  needs hardware + a human and cannot be exercised headlessly. Levels ≥ b-t
  (default b-lta) additionally need **network**: TSA, EU trusted list
  (cached 24 h) and OCSP/CRL endpoints; `--pades-level b-b` is offline.
- **azure mode**: no hardware; outbound network to `login.microsoftonline.com`,
  the vault URL, the TSA (≥ b-t) and the internal CA's CRL/OCSP (≥ b-lt);
  per-user Key Vault key/cert provisioned by an Azure admin (README). Tokens
  and key material are never logged; only the digest leaves the machine.
- **image mode**: nothing special — pure PDF stamping, fully testable headless.
- **GUI**: `customtkinter` (pip) **and** a Python with `tkinter` + a display.
  The step-6 page preview is rendered by **pypdfium2** (bundled PDFium, no
  external binary); `pdftoppm` (poppler-utils) is only an optional fallback if
  already present. Without either, the canvas falls back to a blank white frame.

### tkinter in this environment

The system Python (3.12 here) lacks the `_tkinter` C extension, so `tkinter`
(and thus `customtkinter`) cannot import out of the box. The clean fix is
`sudo apt install python3-tk`. In this checkout the venv has been
**provisioned** without root: `tkinter/` + `_tkinter*.so` extracted from the
`python3-tk` .deb into `venv/lib/python3.12/site-packages/`, plus
`libBLT.2.5.so.8.6` (from the `tk8.6-blt2.5` .deb — Ubuntu's `_tkinter` links
against it) preloaded by `_blt_preload.pth` (a `sitecustomize.py` would be
shadowed by Ubuntu's own). Recreating the venv requires redoing that (or the
apt install).

## Validating changes without hardware

- **image mode & validation**: fully end-to-end via the CLI or `process_batch`
  (no card). The `unittest` suite builds synthetic PDFs (`make_pdf`) to assert
  page-count / dimension validation, image insertion, placement math, and arg
  resolution.
- **PAdES levels & self-verification**: `SelfVerification` signs real PDFs
  *offline* with `SimpleSigner` + pyHanko's `DummyTimeStamper` (RSA-only) and
  a pre-loaded CRL, then asserts `verify_signed_pdf()` detects B-T/B-LT/B-LTA
  and fails on mismatch. Do NOT fake the eID hardware path into passing —
  real-card B-LTA stays the manual acceptance test in BUILD.md.
- **trust.py**: `test_trust.py` covers LOTL parsing, cert filtering, cache,
  TTL, refresh and offline errors with the network mocked (`fetcher=`); a
  live run against the real LOTL takes ~1 s if you need to sanity-check.
- **azure mode**: `test_azure.py` mocks ONLY the Azure transport — the fake
  `CryptographyClient` really signs the digest with a local key, so RSA/EC
  signatures flow through pyHanko + `verify_signed_pdf` end-to-end (incl.
  the r||s→DER conversion). Auth/claims/key-template/material/batch wiring
  are unit-tested with stub credentials; NO test performs a real login —
  the real Entra+Key Vault path is the manual acceptance test in BUILD.md.
  Do not fake it into passing.
- **GUI**: instantiate `gui.CachetApp(args)`, call `app._start_wizard()`,
  inject state directly (`template_path`/`template_dims`/`input_paths`/
  `output_dir`…), walk with `app._goto_step(i)`, call `app.update()`, and
  screenshot the window by id with ImageMagick
  (`import -window <hex winfo_id> shot.png`) on `DISPLAY=:0` — this catches real
  CustomTkinter API errors that `py_compile` cannot. If a GUI test run stalls
  at window creation with ~0 % CPU, it is Tk waiting on the ibus X input-method
  bridge (`XCreateIC`/`_XimRead`), not the app: run with
  `XMODIFIERS=@im=none` (and `PYTHONFAULTHANDLER=1 timeout -s ABRT …` to get a
  stack if it ever recurs). Never run several GUI suites concurrently on the
  same display. Step 3 auto-validates on
  entry; step 6 builds `app.canvas`; a finished batch auto-advances to step 8.
- **i18n**: `test_i18n.py` (headless) checks catalog completeness,
  placeholder parity, balanced / equal-count `**bold**` markup, and that the
  docs catalog (`i18n_docs.py`) is merged and genuinely translated in all six
  languages.
- **vignette appearance** (beid): render `build_stamp_style(identity)` via
  `pyhanko.stamp.TextStamp.apply()` onto a copy with an explicit
  `BoxConstraints(width=_STAMP_W, height=_STAMP_H)`, then rasterize with
  `pdftoppm`. `read_card_identity()` needs the card inserted but no PIN.

## Packaging (standalone executables)

PyInstaller builds **two onefile binaries** from one shared spec
(`cachet.spec`), full details in **`BUILD.md`**:

- **`cachet`** — windowed (`console=False`), entry `gui_main.py` (parses the
  core arg parser then calls `gui.launch_gui`); double-click → GUI.
- **`cachet-cli`** — console (`console=True`), entry `sign_pdfs_beid.py`;
  headless, **excludes** tkinter/customtkinter to stay lean.

`i18n.py` is a plain module imported by `gui.py`, so PyInstaller's import
analysis bundles it into the GUI binary by itself — no spec change needed;
the CLI neither imports nor needs it.

Key spec facts (don't regress): no built-in PyInstaller hooks exist for
`pyhanko`/`pyhanko_beid`/`pyhanko_certvalidator`/`asn1crypto`/`oscrypto`/`pkcs11`
→ they are `collect_all`'d; `pkcs11._pkcs11` native ext + `collect_dynamic_libs`;
`copy_metadata` for the pyhanko family (defensive). `oscrypto` **must not be
excluded** (hard import in certvalidator) but its OpenSSL backend is never on
this app's signing path. `tzdata` is collected **only on Windows** (pyHanko
timestamps need a zoneinfo there). `upx=False` (UPX can corrupt crypto libs).
The eID middleware (`libbeidpkcs11.so`/`beidpkcs11.dll`) is a **runtime dep,
never bundled**. The page preview uses **pypdfium2** (bundled in the GUI binary
via the contrib hooks; excluded from the CLI binary), so poppler is no longer
required — only an optional fallback. The Azure SDK
(`azure.core`/`azure.identity`/`azure.keyvault.*`/`msal`/`msal_extensions`)
has no provided hooks either → `collect_all`'d + metadata into the **common**
collection, and `azure_signer`/`jwt` are explicit hiddenimports — azure mode
must stay available in the **CLI** binary (do NOT add azure to CLI_EXCLUDES).

Build routes: `./build_linux.sh` (native), `build_windows.bat` (real Windows),
`./build_windows_wine.sh` (Linux→Windows via Wine, best-effort), and
`.github/workflows/build.yml` (CI matrix, windows+linux, artifacts — runs on
`develop` pushes and PRs). **Releases**: merging `develop` into `main` runs
`.github/workflows/release.yml`, which tags `v{__version__}` (read from
`sign_pdfs_beid.py` — bump it on develop, it is the single source of truth)
and publishes a GitHub Release with both packaged binaries; an existing tag
makes the workflow skip gracefully. See BUILD.md "Release process". Verify
headlessly: CLI `--help`, image-mode end-to-end, a PKCS#11 native-load canary
(`--lib` at a dummy `.so` → expect a PKCS#11 error, not `ImportError`), and the
GUI binary launched on `DISPLAY=:0` + screenshot. Real eID signing needs
hardware and a real-Windows acceptance test.
