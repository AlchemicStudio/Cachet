# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A batch PDF-signing app for Belgian electronic identity cards (eID), with
three modes, template-based input validation, and a CustomTkinter GUI that
wraps the same logic as the headless CLI.

- **`beid`** — cryptographic eID signature (pyHanko over the eID PKCS#11
  middleware) with a visible **vignette** (cardholder photo + "Signed by:" /
  name / date), bottom-right of the last page by default. **PAdES, default
  level B-LTA** (`--pades-level`); levels ≥ b-t need network.
- **`azure`** — personal **AES (advanced, not qualified)** signature with the
  signed-in user's certificate + non-exportable key in **Azure Key Vault**,
  after ONE Microsoft Entra ID login per batch. LTV trust comes from the
  **internal CA chain** (`--azure-trust-anchors`), NEVER the EU LOTL.
- **`image`** — **visual signatures only**: typed texts, images or hand-drawn
  signatures stamped on chosen pages. **No card**, not a cryptographic
  signature. (Name kept for backward compatibility.)

`beid`/`azure` can carry the same N **visual stamps** (`RunConfig.stamps`)
plus exactly ONE cryptographic signature per document. The GUI **persists**
the signature library, placements and wizard settings in a user profile
(`profile_store.py`); the CLI never reads it.

Vocabulary trap: "visual signature" (user wording) = `Stamp` (`stamps.py`).
The older `_STAMP_*` constants and `build_stamp_style` in the core mean the
**vignette** of the cryptographic signature and were NOT renamed.

No build system, no linter config; tests use stdlib `unittest`. Comments and
CLI text are in English; the GUI is **localized** (EN/FR/NL/DE/ES/PT): every
GUI string goes through `i18n.tr(key, **fmt)`, in ALL six languages.

## Modules

- `sign_pdfs_beid.py` — **core + CLI entry point** (signing, vignette,
  validation, placement math, `RunConfig`/`process_batch`, arg parsing).
- `stamps.py` — visual signatures: the frozen `Stamp` dataclass, bundled
  fonts, text / freehand rendering, image normalisation, `stamp_content` (THE
  single source of pixels + size for the PDF and the GUI preview), validation,
  the `--signatures` JSON, `apply_stamps(writer, …)`. Every failure is a
  `StampError` (a `ValueError`).
- `profile_store.py` — the GUI's user profile (see "Persistence").
- `gui.py` — CustomTkinter **landing page + 8-step wizard**, imported
  **only** with `--gui` (lazy), so the CLI/core/tests never need a display.
- `i18n.py` — `tr`, `set_language`, `system_language`, the UI `CATALOG`;
  texts may carry a light `**bold**` markup (`split_markup`). `i18n_docs.py`
  holds the long documentation-popup texts, merged into the catalog at
  import. `test_i18n.py` enforces: every key in all six languages,
  placeholders matching English, balanced bold markers with equal counts.
- `trust.py` — EU trusted-list (LOTL, ETSI TS 119 612) anchors for LTV: LOTL →
  Belgian list → CA/QC-for-eSignatures certs; JSON cache (24 h TTL,
  `--refresh-trust-list`); actionable `TrustListError` offline; network
  injectable (`fetcher=`). **beid only**.
- `azure_signer.py` — Entra credential + process-wide cache
  (`get_cached_credential`: one login per batch, shared with the GUI sign-in),
  user from token claims (decoded locally, never logged), per-user key/cert
  name template (`sig-{upn}`; explicit override flagged),
  `AzureKeyVaultSigner` (hashes locally, sends ONLY the digest, ECDSA r||s →
  DER). Azure SDK imports are lazy; clients injectable for tests.
- `test_*.py` — headless suites (no card, no network); the `Gui*` classes
  skip themselves without a display.

Import graph (no cycles — keep it that way):

```
stamps         -> stdlib, PIL, pyhanko          (nothing of this app)
profile_store  -> stdlib, stamps, platformdirs  (lazy)
sign_pdfs_beid -> stamps                        (NEVER profile_store)
gui            -> sign_pdfs_beid, stamps, profile_store, i18n
```

- Only `gui.py` / `gui_main.py` need tkinter. `stamps` and
  `profile_store` never import the core: it is the CLI entry script
  (`__main__`), a module importing it would load it twice.
- The core never imports `profile_store`: headless runs stay deterministic.
- Name shadowing: `stamps` is also a `RunConfig` field and a `sign_one`
  keyword, so the core imports **names** (`from stamps import Stamp, …`);
  `gui.py` and `profile_store.py` use `import stamps as stamplib`.

## Commands

```bash
# eID signing (PAdES B-LTA by default); legacy positionals still work:
./venv/bin/python sign_pdfs_beid.py --input ../pdfs --output ../signes --mode beid
./venv/bin/python sign_pdfs_beid.py ../pdfs ../signes --pades-level b-b   # offline

# azure mode (one Microsoft login per batch):
./venv/bin/python sign_pdfs_beid.py --mode azure --azure-vault-url https://x.vault.azure.net \
  --azure-trust-anchors ./internal-ca-chain.pem --input ../pdfs --output ../signes

# visual signatures, no card: one text (or --image-path img.png) / a JSON list
./venv/bin/python sign_pdfs_beid.py --mode image --template ../pdfs/MODELE.pdf \
  --text "Jane Doe\nApproved, {date}" --font great-vibes --color "#1A2B8C" \
  --page last --x 360 --y 120 --input ../pdfs --output ../signes
# --signatures also works with --mode beid|azure (stamped BEFORE the signature):
./venv/bin/python sign_pdfs_beid.py --mode image --signatures sigs.json \
  --input ../pdfs --output ../signes

# GUI — it loads and AUTO-SAVES the user profile: for development export
# CACHET_CONFIG_DIR / CACHET_DATA_DIR to scratch folders first.
./venv/bin/python sign_pdfs_beid.py --gui

# Tests, headless (the Gui* classes skip themselves):
env -u DISPLAY ./venv/bin/python -m unittest -v
# GUI suites — ONE run at a time; skips are failures with CACHET_REQUIRE_GUI=1
# (no xvfb-run on this workstation: use DISPLAY=:0 instead of `xvfb-run -a`):
CACHET_REQUIRE_GUI=1 XMODIFIERS=@im=none PYTHONFAULTHANDLER=1 timeout -s ABRT 900 \
  xvfb-run -a ./venv/bin/python -m unittest -v -k Gui

# Syntax check everything (all modules and tests sit at the repo root):
./venv/bin/python -m py_compile *.py
```

### CLI flags (the non-obvious parts; `--help` has the full list)

- Without `--gui` no window is ever opened.
- `--input` takes files and/or directories (globbed for `*.pdf`); `--output`
  writes `{stem}_signe.pdf` and **never overwrites** (` - 1`, ` - 2`, …).
  If both are absent the legacy positional form `inputs… output_dir` is used.
- `--page <N|first|last>`: a number is **1-based**; `first`/`last`
  (→ `RunConfig.page_anchor`) are resolved **per document**. `--x/--y`:
  lower-left corner in points. In beid/azure they place the vignette —
  **omit both → default bottom-right box** (on the last page, or on the
  anchor page with `--page first|last`).
- `--signatures <file.json>`: JSON **list** of visual signatures, in EVERY
  mode. UTF-8 (BOM accepted); relative `image_path` / font paths resolve
  against the JSON file's directory; unknown keys ignored; `"enabled": false`
  entries skipped.
- `--text` (+ `--font`, `--color`, `--font-size`, which need it): ONE text
  signature, **image mode only**; the two characters `\n` become a newline.
  `--image-path`: ONE image (legacy, 150 pt wide), mutually exclusive with
  `--text`, silently ignored in beid/azure (as before).
- `--pades-level {b-b,b-t,b-lt,b-lta}`, default `b-lta`: b-t+ needs a TSA,
  b-lt+ embeds OCSP/CRL. Never silently downgraded on network failure.
  `--no-verify` skips the post-signing self-verification (and the
  trusted-list fetch at b-t).
- `--timestamp-url` / `--trust-list-url`: flag > env (`CACHET_TSA_URL` /
  `CACHET_LOTL_URL`) > default (DigiCert free TSA — valid but NOT qualified
  timestamps — / official EU LOTL).
- `--azure-*`: each falls back to its `CACHET_AZURE_*` env var. `--azure-auth`
  `default` is for CI only (it breaks the per-user model); trust anchors
  (internal CA PEM/DER file or directory) are required when LTV/verification
  needs them.
- Deprecated: `--pades` (no-op, warns); `--legacy-cms` (old
  `adbe.pkcs7.detached`, level b-b and beid only).

`resolve_config()` / `validate_config()` are pure (they read files, never
write).

**Who owns `--page/--x/--y`**: in image mode the single *convenience* stamp
(`--text`, or the legacy `--image-path`); in beid/azure the **vignette**. The
entries of `--signatures` carry their own placement, so in image mode
`--page/--x/--y` with `--signatures` alone is an error. `--signatures` is
additive with at most one convenience stamp, which comes FIRST. The legacy
image is NOT put in `cfg.stamps`: `image_path/page/x/y/page_anchor` stay
populated as before and `effective_stamps(cfg)` turns them into the first
stamp. `--font-size` is tested with `is not None`: an explicit 0 is rejected,
not replaced by the default.

`main()` reconfigures stdout/stderr with `errors="backslashreplace"`: texts
and file names are echoed, and redirected output on Windows is the ANSI code
page — a character outside it must not kill the run before any document.

## Shared core: `process_batch`

Both the CLI and the GUI call `process_batch(cfg, on_progress=…, today=…)`:
1. Resolved ONCE per batch: the template's page dimensions;
   `effective_stamps(cfg)`; ONE run date for `{date}` (`today`, injectable);
   ONE cache of stamp content; `anchor_requirements(cfg)`.
2. `beid`: **one** PKCS#11 session, `BEIDSigner`, identity read once (no PIN),
   network material built once by `build_signing_material(cfg)` (an
   `HTTPTimeStamper` for levels ≥ b-t; trust anchors + a fetching
   `ValidationContext` for b-lt/b-lta — anchors go in `extra_trust_roots`
   because pyHanko validates the TSA chain against the same context). Trust
   source is **mode-dependent**: beid → EU LOTL; azure → internal CA
   (`load_trust_anchor_certs`). A `TrustListError`/`ValueError` here fails the
   whole batch — the level is NEVER silently downgraded.
   `azure`: `get_cached_credential` → `acquire_user` (ONE login) →
   `resolve_key_names` → `build_azure_signer` → `read_cert_identity`
   (`photo=None`).
3. Per input: validate against the template (if any) → reject+report; else
   `sign_one(…, stamps=, stamp_date=, stamp_cache=)` (beid/azure) or
   `apply_stamps_one()` (image). One `DocResult` (`ok`, `detail`) per file;
   `on_progress` fires per document. Any `StampError` (bad page, unreadable
   image, font, oversized text, already-signed input) fails THAT document
   (`failed — …`, no output file) and the batch continues.
4. beid/azure, levels ≥ b-t (unless `--no-verify`): `verify_signed_pdf()`
   re-opens the output, validates it and detects the **achieved** level
   structurally (timestamp → b-t, DSS revinfo → b-lt, doc-timestamp → b-lta);
   `SelfVerificationError` ⇒ document FAILED.

The level → `PdfSignatureMetadata` mapping is the pure
`signature_meta_kwargs(…)`.

**Visual signatures in the core.** The legacy `RunConfig` fields keep their
meaning — in beid/azure `page/x/y/page_anchor` place the VIGNETTE, in image
mode (with `image_path`) they describe one legacy image stamp. Exactly two
writers of stamps, no third one:
- `sign_one(…, stamps=…)` — beid/azure: `apply_stamps(writer, …)` then
  `sign_pdf(writer)` on the SAME writer (workaround 4).
- `apply_stamps_one(src, dst, stamp_list, …) -> bool` — image mode: ONE
  incremental update, `dst` written only when everything succeeded; an empty
  list is an error, never a silent no-op; returns True when the input was
  already signed.

The **same** elements (vignette and/or stamps, each with its own page target
and (x, y)) are applied to every document — validation guarantees the files
are geometrically identical, so one placement fits all. A page anchor
(`cfg.page_anchor` for the vignette, `Stamp.page_anchor` for a stamp;
exclusive with a page number) is resolved **per document**
(`anchor_page_index`: 0 / -1), so page counts may differ.

## Template validation rules

`validate_against_template(template_dims, pdf)` rejects a file unless it has
**(a)** the same page count and **(b)** EXACTLY identical per-page dimensions —
exact float equality of each page's MediaBox `(width, height)` (inherited
through the page tree), **no tolerance**, rotation ignored. Failures come back
as `ValidationResult(ok=False, reason=…)` and are surfaced, never signed.

A file whose **page count differs** is accepted **iff EVERY placed element
targets a page anchor** (`first`/`last`) **and each of those anchor pages
exactly matches the template's anchor page** — the pages the elements land
on, so the chosen positions fit. `anchor_requirements(cfg)` → `(anchors,
blocker)`: the anchors in use, ordered `("first", "last")`, and None or a
phrase naming the FIRST element that is not anchored (it ends up in the
rejection reason). Deliberate: in beid/azure the default bottom-right
vignette (no `--page first|last`) is NOT auto-anchored (`the vignette has no
first/last page target`) — a safety check is never relaxed silently.

The GUI's step-3 `_validate` passes the single batch anchor as
`page_anchor=`; `process_batch` passes `anchors=` + `blocker=`. The GUI must
therefore hand the batch the SAME anchor for the vignette and every stamp, or
files shown as accepted at step 3 are rejected at run time. Accepted
mismatches carry an informative `reason` even though `ok=True`.

## Stamp placement convention

`(x, y)` is the **lower-left** corner of the placed element, in PDF points
(floats, never rounded) from the page's **bottom-left** corner — the MediaBox
origin is added per page inside `apply_stamps` (non-zero origins work). Page
numbers are **1-based** at the CLI/GUI/JSON boundary, 0-based (`-1` = last)
inside. A placed stamp has exactly ONE page target: `page`, `page_anchor`
(per document) or `all_pages`. An out-of-range page is a clear per-document
failure, not a raw pyHanko error.

- **Image stamps**: scaled to `width_pt`. `load_image` normalises once (EXIF
  orientation; any mode other than RGB/RGBA/L/LA → RGBA); the embedded
  bitmap is capped at `IMAGE_MAX_DPI`.
- **Text stamps are rasters** (no font embedded, no shaping library): Pillow
  at `TEXT_DPI` into a **tightly cropped** RGBA image, then the image
  pipeline. `ImageFont.Layout.BASIC` is pinned so sizes do not depend on
  raqm. Because of the crop, `(x, y)` is the corner of the INK box, and with
  `{filename}` / `{date}` the box (and baseline) can vary per document. A
  character the font lacks is drawn as a box or (Sacramento) not at all —
  accepted limitation, in README.
- **Placeholders**: `resolve_text` replaces `{date}` (dd/mm/YYYY, run date)
  then `{filename}` (source stem) with `str.replace` — never `str.format`.
- **`apply_stamps` paints the image XObject directly** (`w 0 0 h x y cm /Name
  Do`), NOT through a pyHanko `StaticStampStyle`: pyHanko's `BoxConstraints`
  truncates the box to whole points, so the PDF differed from the preview and
  anything under 1 pt high (a `______` line) raised `ZeroDivisionError`. ONE
  image per stamp, shared by its pages; a FRESH resource name per page (pages
  may share one `/Resources`; pyHanko refuses a duplicate name); numbers via
  `_pdf_number` (`%g` can emit an exponent, invalid in PDF). No border.
- **Every stamped page is `mark_update`d**: when `/Contents` is an indirect
  array pyHanko updates the array only, and the `/Resources` it adds to the
  page would never be written (invisible stamp, reported as success). Such
  an array is first COPIED onto the page: pyHanko appends in place, and a
  page sharing it would get the stamp too. A page without `/Contents` first
  gets an empty stream (pyHanko raises `KeyError`).
- **No half-stamped writer**: pages and content are resolved for ALL stamps
  before the first write.
- **Memory is bounded**: `text_layout` MEASURES (1×1 probe) and refuses a
  text above `MAX_TEXT_PIXELS` BEFORE any allocation; `validate_stamp` runs
  the same probe, so no entry point (CLI, JSON, dialog, hand-edited profile)
  can trigger a huge raster. Freehand drawings: same budget.
- **Caching**: `stamp_content(stamp, cache=…)` keys on `content_key`; a text
  with `{filename}` is never cached (unbounded growth over a batch).

JSON schema (`--signatures` and profile entries, `Stamp.to_dict/from_dict`):
`kind`; text → `text`, `font`, `color`, `font_size`; image → `image_path`,
`width_pt`; then `page` | `page_anchor` | `all_pages: true`, `x`, `y`;
optional `enabled`. `from_dict` only type-checks (a `bool` is never a
number); `validate_stamp(stamp, placed=True)` checks content then placement,
`placed=False` content only. A relative font PATH is joined to the JSON
folder, but a bare word that is no file there stays as typed, so a mistyped
bundled id is reported as an id.

**Vignette placement (beid/azure).** With `--x/--y` (or a click in the GUI)
it goes on `--page` at that point, sized to a **3:1 box of width page_w/5**
(`vignette_size_pt`). Without a position it keeps its default bottom-right
box (`_default_vignette_box`, from the pure `default_vignette_rect` that the
GUI also draws). `build_stamp_style(identity, box_w, box_h)` makes the photo
band proportional to the box width (`_PHOTO_BAND_FRAC`), so one layout fits
both boxes without overflowing pyHanko's layout margins. The canvas ↔ PDF
math is pure and tkinter-free (`fit_frame`, `frame_click_to_pdf_xy`,
`pdf_rect_to_frame_rect`).

## GUI workflow (`gui.py`)

`CachetApp` (a `ctk.CTk`) opens on a **landing page**; Start builds the
**wizard**: top bar, stepper, a split body (form in a `CTkScrollableFrame`,
per-step help), and a footer whose Previous/Next labels **name the target
step**. Cancel opens
`_confirm_modal` (shared with Delete — its button row must stay the window's
LAST child, a test finds the buttons that way); confirming (and Finish) calls
`_reset_state()`, which resets the SESSION only and RELOADS the profile — the
library and the settings are never wiped.

Language: `launch_gui` applies the saved language, else `system_language()`;
`CachetApp.__init__` never changes it (tests build apps directly). A switch
closes the docs popup and any editor dialog (language-bound), then rebuilds
the chrome and the current step in place.

The 8 steps (`_STEP_KEYS` → `_build_step_<key>`): template, files,
validation, output folder, signature type, placement (the element editor),
signing, report. Step 3 auto-validates on first entry; its **first/last-page
selector** (`anchor_row`, default "last") appears only when some files' page
count differs from the template: ONE anchor for the whole batch,
re-validating on change. Step 7's "insert your eID card" box (beid) is hidden
by `_launch` and shown again by `_show_card_box` when the batch fails to
start. The step-3 and step-8 tables (`_make_table` / `_fill_table`) keep
their first columns fixed; the last one (detail) takes the remaining width,
follows the window and is never narrower than its longest text — a
horizontal scrollbar shows up only while that does not fit.

**Step 6 — the element editor.** Elements = the vignette (beid/azure, id
`VIGNETTE_ID`) + the library `profile.signatures` (ids are 12 hex digits, so
never `"vignette"`). Top to bottom: a mirror of the step-3 selector when
`count_mismatch`; the navigation row at FULL width; then the
`elements_panel` (stacked add buttons, element list, actions) beside the
preview column (canvas, red `place_warn_lbl`).
- Selection happens in the LIST only; a canvas click always PLACES the
  selected element (no hit-testing).
- Every library/placement mutation ends with `_commit_elements()` (save →
  invalidate run → refresh); the helpers are `_alive`-guarded, so element
  operations also work while step 6 is not built.
- **Placement is never destroyed** by a template or anchor change: an element
  keeps its position plus `placed_on`, the `(w, h)` of the page it was placed
  on; whether it is *in force* is a pure predicate (`_sig_placed`,
  `_vignette_placed` → `profile_store`: exact page-size equality; under an
  anchor the element must sit on the anchor's template page). Not in force =
  "not placed", and it comes back when the template/anchor does. Code that
  re-targets an element (e.g. unticking "On every page") must ask
  `_sig_placed`, never "has coordinates", or a placement saved for another
  page size is applied without a click.
- `_place_error()` (first problem or None): page field invalid or beyond the
  template (BLOCKING), an enabled signature that cannot be rendered, image
  mode without any enabled signature, an enabled signature not placed.
  beid/azure with no enabled signature is complete without a click (default
  vignette); disabled signatures never block.
- The page field is parsed with `str.isdecimal()`: `isdigit()` accepts "²"
  (one key on AZERTY), `int()` rejects it, and the exception would hit every
  navigation callback. `_sync_page_to_selection()` is the ONLY place deriving
  `page_text` from an element; `_pick_template` empties the field.
- **Anchor lock** (page counts differ): the batch anchor forces EVERY element
  onto each document's first/last page; the preview is locked on that
  template page and no position is dropped. `_launch` rewrites every enabled
  stamp to `page_anchor=anchor, page=None, all_pages=False` and passes
  `page_anchor` for the vignette in beid/azure (None in image mode) — `page`
  and `page_anchor` stay exclusive. `_launch` never passes `image_path`:
  `cfg.stamps` = the enabled signatures; the legacy `page/x/y/page_anchor`
  carry the VIGNETTE only.
- Layout contract at the default 1180 px window in every language (asserted
  by a GUI test in `pt` and `nl`): nav row above the editor row, panel no
  wider than `_ELEMENTS_PANEL_W` + 12 px, canvas inside the visible column.

**Editor dialogs** (`_open_dialog`): ONE `self._dialog` at a time, a
non-blocking `CTkToplevel` with a delayed guarded `grab_set`, never
`wait_window`; closed on step change, reset and language switch; widgets are
`dlg_*` attributes, plain values live in `_dialog_state`. The footer (error
label + Cancel/Save) is packed FIRST from the bottom so it never leaves the
fixed-size window. Text dialog: live preview through `stamps.stamp_content`;
the "enter some text" error stays hidden until the user typed or tried to
save (`_dialog_state["edited"]`); editing KEEPS the placement. Draw dialog:
the strokes are the model, the canvas only their echo; points are clamped to
the canvas. An image that cannot be read or copied into the store is NOT
added — no fallback to the original file. A picker opened from a dialog gets
`parent=self._dialog`, and its handler re-checks `_alive(self._dialog)`.

Chrome (`_refresh_chrome`): `_step_complete` / `_step_error` colour the
stepper chips — a step is never green just because its *defaults* are valid.
Next is enabled only while the current step **and every step before it** are
complete (the step-6 selector can re-validate step 3 down to zero accepted
files); ALL navigation locks while `_running`. Editing upstream state
invalidates downstream results. Step content is **rebuilt on every entry**
(state lives on the app, widgets are disposable): update helpers guard widget
access with `_alive(...)`.

The step-6 canvas shows the **rendered template page**
(`core.render_page_image`: **pypdfium2**, `pdftoppm` fallback, white frame if
neither) and every element of that page to scale, the selected one LAST.
`_content(sig)` caches a thumbnail of `stamps.stamp_content` per
`content_key`; it runs inside `_refresh_chrome` on every navigation, so it
**never raises** (`except Exception`; the message is cached instead) and
never renders the same failing content twice. Every `ImageTk.PhotoImage` of a
draw is kept in the list `_canvas_imgs` (Tk only keeps a name). On resize
cached images are rescaled, never re-rendered.

**Persistence hooks.** `CachetApp(args, store=None)`; `_save_profile()` never
raises (an `OSError` is shown as `place.save_failed`), runs on the main
thread only, and is called on every change. The vault URL is saved only on a
REAL edit of the entry (a focus-out must not turn the env/default value into
a saved choice). **`_loading` guard**: `_reset_state` sets the tk
variables from the loaded profile, which fires their traces — the save
handlers return at once while `_loading`, or a reset would write defaults
back over the saved settings. Loading precedence: saved choice >
`CACHET_AZURE_*` env > built-in default. A saved output folder / anchors file
is probed with `os.path.isdir/exists`, NEVER `Path.is_dir()/exists()`: pathlib
raises on EACCES / ENAMETOOLONG / an unreachable share, and the app could no
longer start.

Tkinter is **not thread-safe**: the worker thread never touches widgets, the
profile or the GUI caches. It pushes `("row"/"done"/"error", payload)` onto a
`queue.Queue`, drained on the main thread by a periodic
`self.after(100, self._poll_results)` ("done" auto-advances to the report).
Calling `self.after(...)` *from* the worker raises `main thread is not in main
loop` — do not reintroduce that. The worker catches `(Exception, SystemExit)`:
`open_eid_session()` raises **`SystemExit`** (no reader/card), which is *not*
an `Exception`, so a bare `except Exception` would let the worker die silently
and hang the GUI on "Processing…". `_poll_results` no-ops if the window was
closed mid-batch.

**CTkEntry + StringVar pitfall**: `CTkEntry.destroy()` (CustomTkinter 5.2.2)
does NOT remove the trace it adds on its textvariable (radio buttons and
option menus do). Since step content is rebuilt constantly, entry-backed state
is kept in **plain strings** (`page_text`, `azure_vault`, `azure_key`) synced
via key bindings — do not "simplify" these back to shared `StringVar`s, or
every later `var.set()` fires callbacks on dead widgets (TclError spam). Rule
for new code: **no variable on any `CTkEntry`**, dialogs included (seed with
`.insert`, read with `.get()`); the step-6 check boxes and the dialog option
menu are used WITHOUT a variable.

**Other CustomTkinter 5.2.2 pitfalls** (each covered by a GUI test):
- Never `CTkLabel.configure(image=None)` (nor `""`): the old picture stays on
  screen and, once freed, the next `configure` raises `TclError: image
  "pyimageN" doesn't exist`. "No preview" = the 1×1 transparent image of
  `_blank_ctk_image()`; every preview image is pinned on its label.
- An empty `CTkFrame` keeps a 200×200 default size and does not shrink when
  its last child is destroyed: `elements_list` always keeps one child.
- `CTkButton` has no `justify` and its `width` is a minimum: list rows are
  two stacked `CTkLabel`s, and the panel's buttons are stacked.
- `CTkTextbox` wraps visually by default: the text dialog uses `wrap="none"`
  (the stamp breaks lines only at real newlines).
- `CTkCheckBox.select()/.deselect()` never call `command`, and there is no
  `invoke()` (tests use `toggle()`): handlers flip the MODEL and the list
  refresh re-syncs the widget, never the other way round.
- A `CTkScrollableFrame` inside the scrollable content column double-scrolls:
  `elements_list` is a plain frame.

## Deliberate workarounds and invariants — do not "simplify" away

1. **`open_eid_session()` replaces `pyhanko_beid.open_beid_session()`** — the
   plugin hard-codes a `BELPIC` token label; the same opaque error also appears
   when no card is present. The custom opener picks `BELPIC` else the first
   token and gives distinct "no reader" / "no card" / "other label" messages.
2. **`IncrementalPdfFileWriter(inf, strict=False)`** — inputs use *hybrid*
   xref sections; strict mode refuses them (`hybrid cross-reference sections
   while hybrid xrefs are disabled`). Same writer for the visual-only path.
3. **Vignette via `signers.PdfSigner(stamp_style=…, new_field_spec=…)`** — the
   field box is the default bottom-right box (`on_page=-1` unless a page
   anchor moves it) or, with `sign_one(..., pos=(x, y))`, the placed 3:1 box;
   background image and text are positioned independently.
4. **Visual stamps go into the SAME writer BEFORE `sign_pdf`, never after** —
   a stamp added to a signed output is an incremental update that validators
   flag as a modification (`verify_signed_pdf` raises). For the same reason,
   in beid/azure, stamps are never applied to an INPUT that is already
   signed: `sign_one` checks `existing_signature_count(writer)` (FILLED
   signature fields + document timestamps — an empty field prepared for
   signing does not count) right after opening the writer and raises
   `StampError` before any PIN prompt; self-verification only validates the
   LAST signature and would not notice the broken earlier one. Without stamps
   the guard is not evaluated (plain countersignature; use another `--field`,
   pyHanko refuses a filled one). Image mode keeps its historical outcome (it
   stamps, invalidating the old signature) but appends a WARNING to the
   detail; there a malformed `/AcroForm` counts as "not signed", while
   `sign_one` fails closed. Do not add a code path that stamps `dst`.

## Where the signer's identity comes from (beid mode)

`read_card_identity()` reads both **without a PIN**: **name** from the
`Signature` certificate subject (`given_name` + `surname`); **photo** from the
PKCS#11 DATA object `PHOTO_FILE` (JPEG, via Pillow). The signing cert is the
**non-repudiation** cert (legally equivalent to a handwritten signature) and the
national register number is embedded in every signature — mind PDF distribution.

## Runtime requirements

- **beid**: eID middleware (`libbeidpkcs11.so`), reader + inserted card,
  `pcscd` running. The **PIN is requested once per document**, so a full eID
  run needs hardware + a human and cannot be exercised headlessly. Levels
  ≥ b-t need **network** (TSA, EU trusted list, OCSP/CRL); `b-b` is offline.
- **azure**: no hardware; network to `login.microsoftonline.com`, the vault,
  the TSA (≥ b-t) and the internal CA's CRL/OCSP (≥ b-lt).
- **visual signatures**: no network; text needs Pillow's FreeType renderer
  (`PIL._imagingft`) and the bundled `fonts/`.

### tkinter in this environment

The system Python (3.12 here) lacks `_tkinter` (clean fix: `sudo apt install
python3-tk`). The venv was **provisioned** without root: `tkinter/` +
`_tkinter*.so` extracted from the `python3-tk` .deb into
`venv/lib/python3.12/site-packages/`, plus `libBLT.2.5.so.8.6` (from the
`tk8.6-blt2.5` .deb — Ubuntu's `_tkinter` links against it) preloaded by
`_blt_preload.pth` (a `sitecustomize.py` would be shadowed by Ubuntu's own).
Recreating the venv requires redoing that.
Packaging side effect: PyInstaller cannot see that `.pth` preload, so a GUI
binary built from THIS venv lacks libBLT — launch it with
`LD_LIBRARY_PATH=venv/lib/python3.12/site-packages` to check it here.

## Validating changes without hardware

- **Never touch the real user profile.** The GUI loads and AUTO-SAVES one.
  Non-GUI tests pass explicit temp dirs to `ProfileStore(config_dir=…,
  data_dir=…)`; GUI tests get `CACHET_CONFIG_DIR` / `CACHET_DATA_DIR` from
  `_GuiTestBase.setUp`, with a `setUpModule` sandbox underneath. The same
  holds for anything that is NOT a unittest (screenshot script, manual
  `--gui`, ad-hoc `gui.CachetApp(args)`): export both variables to scratch
  directories first, or pass `store=`.
- **Visual signatures & validation**: fully end-to-end via the CLI or
  `process_batch`. Fixtures: `make_pdf` (optional MediaBox `origin`),
  `write_pdf_objects` for unusual page shapes, `make_png`. Do not assert
  pixel-exact text sizes (hinting differs between FreeType builds): use
  ratios, bounding boxes, colours, or the paint matrix.
- **PAdES levels & self-verification**: `SelfVerification` signs real PDFs
  *offline* with `SimpleSigner` + pyHanko's `DummyTimeStamper` (RSA-only) and
  a pre-loaded CRL, then asserts `verify_signed_pdf()` detects B-T/B-LT/B-LTA
  and fails on mismatch. It is also the recipe for **stamps + signature**
  (real `core.sign_one(…, stamps=[…])`, then coverage `ENTIRE_FILE` and
  modification level `NONE`) and holds the negative control (a stamp after
  the signature fails verification).
- **azure**: `test_azure.py` mocks ONLY the Azure transport — the fake
  `CryptographyClient` really signs the digest with a local key, so RSA/EC
  signatures flow through pyHanko + `verify_signed_pdf` end-to-end.
- **Do NOT fake the hardware / login paths into passing**: real-card B-LTA
  and the real Entra + Key Vault run stay the manual acceptance tests of
  BUILD.md.
- **GUI**: (profile env vars set) `gui.CachetApp(args)`,
  `app._start_wizard()`, inject state directly (`template_path` /
  `template_dims` / `input_paths` / `output_dir`…; signatures through
  `app._add_signature(stamp, label)`), walk with `app._goto_step(i)`,
  `app.update()`, and screenshot the window by id with ImageMagick
  (`import -window <hex winfo_id> shot.png`) on `DISPLAY=:0` — this catches
  CustomTkinter API errors that `py_compile` cannot. A finished batch
  auto-advances to step 8. Dialogs are driven through their handlers (fill
  the `dlg_*` widgets, `_on_text_dialog_edit()`, `_apply_*_dialog()`), with
  `gui.filedialog.*` and `gui.colorchooser.askcolor` patched.
- **A GUI run stalling at window creation with ~0 % CPU** is Tk waiting on
  the ibus X input-method bridge, not the app: run with
  `XMODIFIERS=@im=none`. Never run several GUI suites concurrently on one
  display.
- **`CACHET_REQUIRE_GUI=1`**: the display guard of the GUI tests turns ANY
  failure of `tkinter.Tk()` or `import gui` (no display, broken Tk, an
  ImportError in `gui.py`) into a skip — a green `-k Gui` run with skips
  proves nothing. With this variable the guard FAILS instead; every run
  meant to exercise the GUI sets it and must report zero skips.
- **GUI tests and the cyclic GC**: `_GuiTestBase.setUp` calls `gc.disable()`.
  The tests pump `update()` instead of a mainloop; when the collector runs in
  the batch WORKER thread, each garbage `CTkFont.__del__` is a Tk call from a
  non-main thread that waits one second for a mainloop that never comes, and
  the batch outlives the test's wait loop. New worker-thread GUI tests must
  inherit `_GuiTestBase`.
- **Vignette appearance** (beid): render `build_stamp_style(identity)` via
  `pyhanko.stamp.TextStamp.apply()` with an explicit
  `BoxConstraints(width=_STAMP_W, height=_STAMP_H)`, then rasterize with
  `pdftoppm`. `read_card_identity()` needs the card inserted but no PIN.

## Packaging (standalone executables)

PyInstaller builds **two onefile binaries** from one spec (`cachet.spec`),
details in **`BUILD.md`**:

- **`cachet`** — windowed (`console=False`), entry `gui_main.py`.
- **`cachet-cli`** — console, entry `sign_pdfs_beid.py`; headless,
  **excludes** tkinter/customtkinter (and pypdfium2) to stay lean.

**Fonts are data of BOTH binaries** (as `stamps.py` is code of both):
`common_datas` carries `fonts/*.ttf` and `fonts/licenses/*` (the OFL requires
the licence texts to travel with the fonts) — never move them to
`gui_datas`, the CLI stamps text too.
`stamps.asset_path` resolves them next to the module or under
`sys._MEIPASS`. `common_hidden` must keep `PIL._imagingft` (Pillow's FreeType
renderer); `gui_hidden` adds `profile_store` and `tkinter.colorchooser`.

Key spec facts (don't regress): no built-in PyInstaller hooks exist for
`pyhanko`/`pyhanko_beid`/`pyhanko_certvalidator`/`asn1crypto`/`oscrypto`/`pkcs11`
→ they are `collect_all`'d; `pkcs11._pkcs11` native ext + `collect_dynamic_libs`;
`copy_metadata` for the pyhanko family (defensive). `oscrypto` **must not be
excluded** (hard import in certvalidator). `tzdata` is collected **only on
Windows** (pyHanko timestamps need a zoneinfo there). `upx=False` (UPX can
corrupt crypto libs). The eID middleware is a **runtime dep, never bundled**.
The Azure SDK (`azure.*`, `msal`, `msal_extensions`) has no hooks either →
`collect_all`'d + metadata into the **common** collection, with
`azure_signer`/`jwt` as explicit hiddenimports — azure mode must stay
available in the **CLI** binary (do NOT add azure to CLI_EXCLUDES).

Build routes: `./build_linux.sh`, `build_windows.bat`,
`./build_windows_wine.sh` (best-effort), and `.github/workflows/build.yml`
(on `develop` pushes and PRs). **Releases**: merging
`develop` into `main` runs `release.yml`, which tags `v{__version__}` (read
from `sign_pdfs_beid.py` — the single source of truth, bump it on develop)
and publishes both binaries; an existing tag makes it skip. Verify a build
headlessly: CLI `--help`; `--text` / `--signatures` end-to-end (fonts and
`PIL._imagingft` in the frozen CLI); a PKCS#11 native-load canary (`--lib` at
a dummy `.so` → a PKCS#11 error, not `ImportError`); the GUI binary on
`DISPLAY=:0` + screenshot (profile env vars set).

**CI** — four pipeline files run the tests in the same three Linux steps: the
headless suite; a **GUI canary** (`import tkinter, customtkinter, gui` + a
`Tk()` under Xvfb — a broken Tk or `gui.py` must fail, not skip); the `Gui*`
suites under Xvfb with `CACHET_REQUIRE_GUI=1` and `XMODIFIERS=@im=none` (the
end-to-end layer: a Tk desktop app has no browser to drive). Every test job
has a timeout — a stalled Tk call must not hold a runner or the release
concurrency group — and the GUI step runs under `timeout -s ABRT 900` with
`PYTHONFAULTHANDLER=1`, so a stall dumps its stacks.
GitHub Actions (`build.yml`, `release.yml`) run them on Linux only (on the
Windows runner Tk could open real windows and hang), then build on Windows +
Linux and smoke-test the frozen CLI. GitLab CI (`.gitlab-ci.yml`) and Forgejo
Actions (`.forgejo/workflows/tests.yml`) run the tests only, in
`python:3.13-bookworm` + `xvfb xauth fonts-dejavu-core`; on Forgejo the
runner label and `actions/checkout@v4` depend on the instance.

## Persistence (user profile) — `profile_store.py`

GUI only; tkinter-free; the core/CLI never imports it.

- **Where**: `profile.json` in the config dir, the image store `signatures/`
  in the data dir — each: explicit argument > `CACHET_CONFIG_DIR` /
  `CACHET_DATA_DIR` > `platformdirs` (ONE folder for both on Windows/macOS).
  `ProfileStore()` resolves both ONCE at construction and creates nothing.
- **What**: `settings` — only keys with a value are written (`None` = "never
  set by the user", so env/defaults keep applying); `signatures` — per entry
  `Stamp.to_dict(base_dir=signatures_dir)` + `id`, `label`, `enabled`,
  `placed_on`. A stored image is written as its bare file name, any other as
  an absolute path. "Unplaced" = no `x`/`y` (it may still remember
  `all_pages: true`).
- **NEVER stored**: PIN, tokens, any credential, the azure key name, the
  signed-in UPN, the template, the input files. Everything is UNENCRYPTED
  (files created 0600 on POSIX); the GUI help and README say so.
- **Atomic writes** (temp file in the same directory + `os.replace`); `save`
  raises only `OSError`.
- **Tolerant `load()` — never raises**: missing file → defaults; a UTF-8 BOM
  is accepted; not UTF-8 / invalid JSON / deep nesting / wrong top level /
  `version` missing or ≠ 1 → moved aside as `profile.json.bak` → defaults. A
  malformed entry is skipped, the others kept; an entry whose image or font
  FILE is missing is KEPT (shown as unusable, deletable) — recognised by the
  `StampError` prefixes in `_MISSING_FILE_PREFIXES`, keep them in sync with
  `stamps.py`; an incomplete placement is cleared; bad / duplicate ids are
  regenerated.
- **A degraded load must not lead to a destructive save**: when the file
  could not be read (`OSError`) or not be moved aside, it stays in place and
  `save()` REFUSES (`OSError`) until a later `load()` succeeds — the GUI
  saves on every click and would replace a good profile with an empty one.
- **Image store**: imported images and drawings are COPIED to
  `signatures/<32 hex of sha256><ext>` (content-addressed: same bytes → one
  file; a moved original does not break a signature). User FONTS given by
  path are not copied.
- **Deleting**: `remove_signature` is the ONLY code that unlinks anything,
  and only a file for which `is_store_file(path)` holds (not a symlink,
  RESOLVED parent = the resolved `signatures/` dir, content-addressed name)
  and that no remaining signature references. Never use a lexical
  `is_relative_to` / `relative_to` test for "inside the store"
  (`dir/../../x` would pass). Deliberately **no sweep of unreferenced
  files**: the data dir may serve another profile and a `.bak` still
  references its images.
