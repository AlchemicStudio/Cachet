#!/usr/bin/env python3
"""CustomTkinter graphical interface for Cachet — an 8-step wizard.

This module is imported ONLY when running `sign_pdfs_beid.py --gui`: it
depends on tkinter/customtkinter, which are absent in CLI/headless mode. All
the business logic (validation, image insertion, eID/azure signing, placement
math) lives in `sign_pdfs_beid.py` and is tested without tkinter; the GUI is
merely a façade.

Structure:

* **Landing page** — app overview, language selector (EN/FR/NL/DE/ES/PT via
  `i18n.py`), and a Start button. No stepper here.
* **Wizard** — a top bar with the same language selector (switching
  rebuilds the chrome and the current step in place; all state lives on the
  app, so nothing is lost), a stepper bar (current step highlighted;
  completed steps get a light-green border, steps with problems a light-red
  one, not-yet-reachable steps are disabled), a split body (form on the
  left, contextual help on the right — help and documentation texts render
  the catalog's light ``**bold**`` markup), and a navigation footer whose
  Previous/Next labels name the target step. Cancel asks for confirmation,
  then resets everything and returns to the landing page.

The 8 steps: template → documents → validation → output folder → signature
type (+ localized "Full documentation" popup ending with source links) →
placement (a multi-element editor: the list of elements — the crypto
vignette in beid/azure plus the user's library of visual signatures (text,
image, drawing — added and edited through three small non-blocking
dialogs), each with an enabled checkbox — beside the page preview,
which shows every element to scale; select an element, click to place it;
an explicit target-page field and — when page counts differ — the
first/last-page selector mirrored from validation) → signing (summary,
green "insert your eID card" reminder, progress) → results report (+ "Open
output folder").

The signature library, each element's last placement and the wizard
settings live in the user profile (`profile_store.py`): loaded at start,
saved on every change, reloaded — never wiped — by Cancel/Finish.

Threading rule (do not regress): tkinter is NOT thread-safe. The batch runs
on a worker thread that never touches widgets — it only pushes onto a
`queue.Queue` drained by the main thread via a periodic `after()`. The worker
catches `SystemExit` too: `open_eid_session()` raises it (no reader/card) and
it is *not* an `Exception`."""

from __future__ import annotations

import dataclasses
import datetime
import os
import queue
import threading
import webbrowser
from pathlib import Path
from tkinter import colorchooser, filedialog, font as tkfont, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

import i18n
import profile_store
import sign_pdfs_beid as core
import stamps as stamplib
from i18n import tr

_FRAME_MAX_W = 360   # minimum size of the page preview frame (px)
_FRAME_MAX_H = 460

_HELP_PANEL_W = 380  # right column (contextual help) width, px
_HINT_WRAP = 520     # px; CTk labels do not auto-wrap

_LOGO_SIZE = 30      # top-bar logo height, px (width follows the aspect)
_SUPPORT_URL = "https://donate.stripe.com/4gM8wJ6qbgfO7U342n6oo02"

# Wizard steps, in order. Each key maps to the i18n entries
# step.<key>.short / step.<key>.title / step.<key>.help and to a
# _build_step_<key> method.
_STEP_KEYS = (
    "template", "files", "validate", "output",
    "mode", "place", "run", "results",
)

# Stepper chip palette (light, dark).
_COL_ACCENT = ("#3B8ED0", "#1F6AA5")     # current step
_COL_DONE = ("#6fbf73", "#4e8f52")       # completed, no errors: light green
_COL_ERROR = ("#e08a8a", "#a85454")      # step with problems: light red
_COL_TODO = ("gray55", "gray45")         # pending, reachable
_COL_LOCKED = ("gray80", "gray25")       # not reachable yet

_COL_CARD_BG = ("#e3f3e6", "#1e3a26")    # step-7 "insert your eID card" box
_COL_CARD_FG = ("#1d4d2a", "#bfe3c6")
_COL_LINK = ("#1a5fb4", "#78aeed")       # documentation links (light, dark)
_COL_SELECTED = ("#d6e9f8", "#27425c")   # step-6 list: selected element row

VIGNETTE_ID = "vignette"      # element id of the crypto vignette; never a signature id
                              # (profile_store.is_valid_id only accepts 12 hex digits)
_ELEMENTS_PANEL_W = 290       # step-6 left column (element list), px
_ELEMENTS_INNER_W = 270       # width of the widgets inside that column, px
_PREVIEW_MAX_PX = 1200        # long side of a cached element preview, px
_SCALED_CACHE_MAX = 64        # entries of the per-size preview cache
_MAX_PAGE_DIGITS = 9          # target-page field: anything longer is not a page number
_TABLE_CELL_PAD = 16          # room a table cell needs around its text, px
_DRAW_W, _DRAW_H = 620, 240   # freehand dialog: drawing canvas, px
_DRAW_PEN = 3                 # pen width on that canvas, px
_DRAW_SCALE = 4               # supersampling of the stored drawing
_DRAW_PT_PER_PX = 0.4         # width on the page of a drawing, per canvas px


def _asset_path(name: str) -> Path:
    """Path of a bundled asset (e.g. the logo): next to this file in a
    checkout, under the PyInstaller extraction dir (``sys._MEIPASS``) in the
    frozen binary — cachet.spec ships it in ``gui_datas``."""
    return stamplib.asset_path(name)


_logo_pil = None     # cached PIL image; False = tried and failed


def _load_logo_pil():
    """PIL image of ``logo.png``, downscaled once and cached (the source is
    2048² — 4× the display size is plenty of HiDPI headroom). None if the
    file is missing or unreadable — the brand then degrades to the name
    alone, the app never fails over a decorative asset."""
    global _logo_pil
    if _logo_pil is None:
        try:
            im = Image.open(_asset_path("logo.png"))
            im.thumbnail((_LOGO_SIZE * 4, _LOGO_SIZE * 4))
            _logo_pil = im
        except Exception:  # noqa: BLE001 - decorative only
            _logo_pil = False
    return _logo_pil or None


def _load_logo():
    """Fresh ``CTkImage`` of the logo, or None. Created per call — NEVER
    cache the CTkImage itself: it binds to the Tk root that first renders it
    (its PhotoImages are minted lazily against that root), so a cached one
    would crash any later root (tests create several). Only the PIL source
    is cached."""
    pil = _load_logo_pil()
    if pil is None:
        return None
    w, h = pil.size
    return ctk.CTkImage(light_image=pil, dark_image=pil,
                        size=(round(_LOGO_SIZE * w / h), _LOGO_SIZE))


def _alive(widget) -> bool:
    """True if a widget still exists (guards updates after a rebuild)."""
    try:
        return bool(widget and widget.winfo_exists())
    except Exception:  # noqa: BLE001 - interpreter shutting down, etc.
        return False


def _elide(text: str, max_px: int, font) -> str:
    """``text`` if it fits ``max_px`` in ``font``, else its longest prefix
    that fits with a trailing "…" — a long user label never widens a panel."""
    if font.measure(text) <= max_px:
        return text
    for end in range(len(text) - 1, 0, -1):
        if font.measure(text[:end] + "…") <= max_px:
            return text[:end] + "…"
    return "…"


def _text_on(color: str) -> str:
    """Black or white, whichever reads better on a ``#RRGGBB`` background."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#000000" if (299 * r + 587 * g + 114 * b) / 1000 > 140 else "#ffffff"


def _bold_tag(box) -> None:
    """(Re)define the "bold" text tag on a CTkTextbox. ``CTkTextbox.tag_config``
    refuses ``font`` (its scaling guard), so the tag goes on the inner
    ``tk.Text``, as a bold copy of the font the body uses at that moment —
    already DPI-scaled by CustomTkinter. It is re-derived on every fill
    (help panel: each step change) and at popup creation, so a DPI change
    is picked up at the next rebuild rather than tracked live. The Font
    object is pinned on the box: tkinter deletes a named font it created as
    soon as the Python object is garbage-collected."""
    inner = getattr(box, "_textbox", box)
    bold = tkfont.Font(font=inner.cget("font")).copy()
    bold.configure(weight="bold")
    box._cachet_bold_font = bold                # noqa: SLF001 - keep-alive ref
    inner.tag_config("bold", font=bold)


def _insert_markup(box, text: str) -> None:
    """Append ``text`` to a CTkTextbox, rendering the catalog's light
    ``**bold**`` markup (i18n.split_markup) through the "bold" tag (see
    ``_bold_tag``). The caller unlocks/locks the box."""
    for segment, bold in i18n.split_markup(text):
        box.insert("end", segment, ("bold",) if bold else ())


def _fill_textbox(box, text: str) -> None:
    """Replace a read-only CTkTextbox's content with marked-up ``text``."""
    box.configure(state="normal")
    box.delete("1.0", "end")
    _bold_tag(box)
    _insert_markup(box, text)
    box.configure(state="disabled")


class CachetApp(ctk.CTk):
    """Main window: landing page + 8-step wizard."""

    def __init__(self, args, store=None):
        super().__init__()
        self._apply_title()
        self.geometry("1180x950")
        _style = ttk.Style()
        _style.configure("Treeview", rowheight=30, font=("", 11))   # tall rows, full text
        _style.configure("Treeview.Heading", font=("", 11, "bold"))

        # --- persistent widgets/state containers -------------------------
        self.default_lib = getattr(args, "lib", None)
        # NOTE: only radio/option-menu widgets get tk variables — their CTk
        # classes detach the variable trace in destroy(). CTkEntry (5.2.2)
        # does NOT, so entry-backed state is kept as plain strings instead
        # (self.azure_vault / self.azure_key / self.page_text, and the
        # editor-dialog fields, read with .get() when the dialog is applied)
        # and synced via key bindings; a shared StringVar would fire
        # dead-widget callbacks after every step rebuild.
        self.mode_var = ctk.StringVar(value="beid")
        self.pades_level_var = ctk.StringVar(value="b-lta")
        self.azure_auth_var = ctk.StringVar(value="interactive")  # GUI default
        self._running = False                      # batch in progress
        self._azure_login_q: queue.Queue | None = None
        self._result_q: queue.Queue | None = None
        self._canvas_imgs: list = []               # PhotoImage refs (elements)
        self._bg_img = None                        # PhotoImage ref (page render)
        self._page_img_cache: dict = {}            # (template, page) -> PIL image
        self._last_win_size = None
        self._stepper_btns: list[ctk.CTkButton] = []
        self.step_index = 0
        self.canvas = None
        # User profile: the signature library + the wizard settings,
        # loaded by _reset_state() and saved on every change. Tests inject a
        # store; the default one reads CACHET_CONFIG_DIR / CACHET_DATA_DIR,
        # else the platform's user folders.
        self.store = store if store is not None else profile_store.ProfileStore()
        self.profile = profile_store.Profile()
        self._loading = False                      # _reset_state applying the profile
        self._profile_error: str | None = None     # last save failure (_save_profile)
        self._element_notice: str | None = None    # last element operation refused
        # content_key -> (PIL thumbnail, (w_pt, h_pt)), or -> the error
        # message (str) of content that cannot be rendered
        self._preview_cache: dict = {}
        self._scaled_cache: dict = {}              # (content_key, w_px, h_px) -> PIL
        self._dialog = None                        # the single editor dialog
        self._dialog_state: dict = {}
        self._blank_img = None                     # 1×1 CTkImage: "no preview"
        self.selected_id: str | None = None        # VIGNETTE_ID or a signature id

        self._reset_state()

        # Config edits invalidate a previous run + refresh the stepper, and
        # are persisted (never while _reset_state applies the profile).
        self.mode_var.trace_add("write", lambda *_: self._on_config_edit())
        self.pades_level_var.trace_add("write", lambda *_: self._on_config_edit())
        self.azure_auth_var.trace_add("write", lambda *_: self._on_azure_auth_edit())

        self._screen = ctk.CTkFrame(self, fg_color="transparent")
        self._screen.pack(fill="both", expand=True)
        self._show_landing()
        self.bind("<Configure>", self._on_resize)  # the canvas grows with the window

    # ------------------------------------------------------------- state
    def _apply_title(self) -> None:
        self.title(tr("app.title", version=core.__version__))

    def _reset_state(self) -> None:
        """Back to a blank wizard (fresh Start, Cancel, or Finish). Only the
        SESSION is reset (template, files, validation, results); the
        signature library and the saved settings are reloaded from the user
        profile, never wiped."""
        self._close_docs_popup()                   # it belongs to the old wizard
        self._close_dialog()
        self.template_path: Path | None = None
        self.template_dims: list[tuple[float, float]] = []
        self.template_error: str | None = None
        self.input_paths: list[Path] = []
        self.validation_results = None             # list[ValidationResult] | None
        self.valid_paths: list[Path] = []
        self.cur_page = 0                          # 0-based, preview only
        self.page_text = ""                        # target-page field content
        self.selected_id = None
        self._element_notice = None
        self._preview_cache.clear()
        self._scaled_cache.clear()
        # Page anchor (validation step): when some files' page count differs
        # from the template, `anchor_choice` decides whether EVERY file is
        # signed on its own first or last page (default: last, matching the
        # historical vignette position). Inactive while all counts match.
        self.anchor_choice = "last"
        self.count_mismatch = False
        self.run_rows: list = []                   # DocResult, streamed
        self.run_results: list | None = None       # DocResult list once done
        self.run_error: str | None = None          # batch-level failure
        self._page_img_cache.clear()
        self.step_index = 0
        # Settings precedence: saved user choice > CACHET_AZURE_* env >
        # built-in default. The tk-variable traces fire below: _loading keeps
        # them from writing these very values back as "user edits".
        self._loading = True
        try:
            self.profile = self.store.load()
            s = self.profile.settings
            # os.path.isdir / exists, not pathlib: they are False on ANY
            # error, whereas Path.is_dir() / exists() raise for an unreachable
            # share, a permission problem or an over-long name — and a saved
            # path must never keep the application from starting.
            self.output_dir: Path | None = (
                Path(s.output_dir) if s.output_dir and os.path.isdir(s.output_dir)
                else None)
            self.azure_vault = s.azure_vault_url or os.environ.get(
                core.ENV_AZURE_VAULT_URL, "https://login.live.com")
            self.azure_key = os.environ.get(core.ENV_AZURE_KEY_NAME, "")
            saved = s.azure_trust_anchors
            env = os.environ.get(core.ENV_AZURE_TRUST_ANCHORS)
            self.azure_anchors_path: Path | None = (
                Path(saved) if saved and os.path.exists(saved)
                else (Path(env) if env else None))
            self.azure_user_upn: str | None = None
            self.mode_var.set(s.mode if s.mode in ("beid", "azure", "image") else "beid")
            self.pades_level_var.set(
                s.pades_level if s.pades_level in core.PADES_LEVELS else "b-lta")
            self.azure_auth_var.set(
                s.azure_auth if s.azure_auth in core.AZURE_AUTH_METHODS
                else "interactive")
        finally:
            self._loading = False

    def _invalidate_run(self) -> None:
        self.run_rows = []
        self.run_results = None
        self.run_error = None

    def _on_config_edit(self) -> None:
        if self._running or self._loading:
            return
        s = self.profile.settings
        s.mode = self.mode_var.get()
        s.pades_level = self.pades_level_var.get()
        self._save_profile()
        self._invalidate_run()
        self._refresh_chrome()

    def _on_azure_auth_edit(self) -> None:
        if self._loading:
            return
        self.profile.settings.azure_auth = self.azure_auth_var.get()
        self._save_profile()

    def _save_profile(self) -> None:
        """Persist the library + settings. Never raises: a failure is kept
        in `_profile_error` and shown on the placement step. Main thread
        only (the batch worker never calls it)."""
        if self._loading:
            return
        try:
            self.store.save(self.profile)
            self._profile_error = None
        except OSError as exc:
            self._profile_error = str(exc) or exc.__class__.__name__

    # ------------------------------------------- elements (read-only helpers)
    def _sig(self, sig_id):
        """The library signature with that id, or None."""
        for sig in self.profile.signatures:
            if sig.id == sig_id:
                return sig
        return None

    def _enabled_signatures(self) -> list:
        return [s for s in self.profile.signatures if s.enabled]

    def _has_vignette(self) -> bool:
        return self.mode_var.get() in ("beid", "azure")

    def _element_ids(self) -> list[str]:
        """Step-6 elements, in list order: the vignette (beid/azure), then
        the whole library (enabled or not)."""
        return (([VIGNETTE_ID] if self._has_vignette() else [])
                + [s.id for s in self.profile.signatures])

    def _sig_placed(self, sig) -> bool:
        """True while the signature's saved placement is IN FORCE for the
        current template and anchor (placements are never destroyed)."""
        return profile_store.stamp_placement_applies(
            sig.stamp, sig.placed_on, self.template_dims, self._page_anchor())

    def _vignette_placed(self) -> bool:
        v = self.profile.settings.vignette
        return v is not None and profile_store.page_placement_applies(
            v.page, v.placed_on, self.template_dims, self._page_anchor())

    def _sample_filename(self) -> str:
        """File stem the previews substitute for {filename}."""
        if self.valid_paths:
            return self.valid_paths[0].stem
        if self.template_path:
            return self.template_path.stem
        return "document"

    def _content_key(self, sig) -> tuple:
        return stamplib.content_key(sig.stamp, filename=self._sample_filename(),
                                    date=datetime.date.today())

    def _content_entry(self, sig):
        """Cached preview of a signature's content: `(thumbnail, (w_pt,
        h_pt))`, or the error message (str) when it cannot be rendered. Runs
        inside _refresh_chrome on every navigation, so it never raises and
        never renders the same failing content twice."""
        key = self._content_key(sig)
        if key not in self._preview_cache:
            try:
                img, size_pt = stamplib.stamp_content(
                    sig.stamp, filename=self._sample_filename(),
                    date=datetime.date.today())
                thumb = img.convert("RGBA")       # a copy, whatever the mode
                thumb.thumbnail((_PREVIEW_MAX_PX, _PREVIEW_MAX_PX))
                self._preview_cache[key] = (thumb, size_pt)
            except Exception as exc:  # noqa: BLE001 - MemoryError, bomb guard, …
                self._preview_cache[key] = str(exc) or type(exc).__name__
        return self._preview_cache[key]

    def _content(self, sig):
        """`(thumbnail, (w_pt, h_pt))` of a signature, or None when unusable."""
        entry = self._content_entry(sig)
        return None if isinstance(entry, str) else entry

    def _sig_error(self, sig) -> str | None:
        """Why a signature cannot be used (missing image/font, …), or None."""
        entry = self._content_entry(sig)
        return entry if isinstance(entry, str) else None

    def _element_size_pt(self, element_id) -> tuple[float, float]:
        """Size (pt) of an element on the previewed page: a 3:1 vignette box
        of width page/5, or the signature's real content size."""
        if element_id == VIGNETTE_ID:
            return core.vignette_size_pt(self.template_dims[self.cur_page][0])
        sig = self._sig(element_id)
        content = self._content(sig) if sig else None
        return content[1] if content else (150.0, 50.0)

    def _element_page(self, element_id) -> int | None:
        """1-based TEMPLATE page an element sits on when its placement is in
        force on one single page, else None (unplaced, every page, unknown)."""
        if element_id == VIGNETTE_ID:
            return self.profile.settings.vignette.page if self._vignette_placed() else None
        sig = self._sig(element_id)
        if sig is None or not self._sig_placed(sig) or sig.stamp.all_pages:
            return None
        if sig.stamp.page_anchor is not None:
            return 1 if sig.stamp.page_anchor == "first" else len(self.template_dims)
        return sig.stamp.page

    def _ensure_selection(self) -> bool:
        """Re-pick the selected element when the current one no longer
        exists (mode change, deletion, fresh wizard). True if it re-picked."""
        if self.selected_id in self._element_ids():
            return False
        if self._has_vignette():
            self.selected_id = VIGNETTE_ID
        else:
            sigs = self.profile.signatures
            todo = [s for s in sigs if s.enabled and not self._sig_placed(s)]
            first = todo[0] if todo else (sigs[0] if sigs else None)
            self.selected_id = first.id if first else None
        self._sync_page_to_selection()
        return True

    def _sync_page_to_selection(self) -> None:
        """Target-page field + preview page follow the selected element. No
        drawing. The ONLY place that derives `page_text` from an element."""
        if self._page_anchor():
            self._set_page_entry("")          # field disabled; cur_page is locked by _draw_page
            return
        page = self._element_page(self.selected_id)
        if page is None:
            self._set_page_entry("")          # unplaced / every page / nothing selected
        else:
            self.cur_page = page - 1
            self._set_page_entry(str(page))

    # ============================================================= CHROME
    def _build_top_bar(self, parent, *, lang_command):
        """Top bar shared by the landing page and the wizard: the brand
        (logo + app name) on the left; on the right, the language selector
        with the support link to its right. Returns the language option menu
        (the caller keeps the handle it re-renders/locks with)."""
        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.pack(fill="x")
        brand = ctk.CTkFrame(top, fg_color="transparent")
        brand.pack(side="left", padx=(2, 8), pady=(0, 6))
        logo = _load_logo()
        self._brand_logo_lbl = None
        if logo is not None:
            self._brand_logo_lbl = ctk.CTkLabel(brand, image=logo, text="")
            self._brand_logo_lbl.pack(side="left")
        self._brand_name_lbl = ctk.CTkLabel(
            brand, text="Cachet", font=ctk.CTkFont(size=18, weight="bold"))
        self._brand_name_lbl.pack(side="left", padx=(8, 0))
        # side="right" packs outermost first: the support link stays to the
        # RIGHT of the language selector.
        self._support_btn = ctk.CTkButton(
            top, text=tr("support.button"), width=170,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
            command=lambda: webbrowser.open(_SUPPORT_URL))
        self._support_btn.pack(side="right", padx=(8, 2), pady=(0, 6))
        menu = ctk.CTkOptionMenu(
            top, width=150,
            values=[i18n.LANGUAGE_NAMES[c] for c in i18n.LANGUAGES],
            command=lang_command,
        )
        menu.set(i18n.LANGUAGE_NAMES[i18n.get_language()])
        menu.pack(side="right", padx=8, pady=(0, 6))
        ctk.CTkLabel(top, text=tr("landing.language_label")
                     ).pack(side="right", padx=(0, 2), pady=(0, 6))
        return menu

    # ============================================================ LANDING
    def _clear_screen(self) -> None:
        self._close_dialog()
        for w in self._screen.winfo_children():
            w.destroy()
        self._stepper_btns = []
        self.canvas = None

    def _show_landing(self) -> None:
        self._clear_screen()
        page = ctk.CTkFrame(self._screen, fg_color="transparent")
        page.pack(fill="both", expand=True, padx=28, pady=20)

        # Top bar: brand left, language selector + support link right
        # (no stepper on this page).
        self._lang_menu = self._build_top_bar(page, lang_command=self._on_language_change)

        # Center: overview of what the app does.
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.pack(fill="both", expand=True)
        ctk.CTkLabel(body, text=tr("landing.heading"),
                     font=ctk.CTkFont(size=28, weight="bold")).pack(pady=(60, 4))
        ctk.CTkLabel(body, text=f"Cachet {core.__version__}",
                     text_color=("gray40", "gray60"),
                     font=ctk.CTkFont(size=12)).pack(pady=(0, 24))
        ctk.CTkLabel(body, text=tr("landing.intro"), justify="left",
                     wraplength=760, font=ctk.CTkFont(size=14)).pack(padx=40)

        # Bottom bar: Start in the bottom-right corner.
        bottom = ctk.CTkFrame(page, fg_color="transparent")
        bottom.pack(fill="x", side="bottom")
        ctk.CTkButton(bottom, text=tr("landing.start") + "  ▸", width=170, height=40,
                      font=ctk.CTkFont(size=15, weight="bold"),
                      command=self._start_wizard).pack(side="right", pady=8)

    @staticmethod
    def _language_code(display_name: str) -> str | None:
        for code, name in i18n.LANGUAGE_NAMES.items():
            if name == display_name:
                return code
        return None

    def _persist_language(self, code: str) -> None:
        if self.profile.settings.language != code:
            self.profile.settings.language = code
            self._save_profile()

    def _on_language_change(self, display_name: str) -> None:
        code = self._language_code(display_name)
        if code:
            i18n.set_language(code)
            self._persist_language(code)
        self._apply_title()
        self._close_docs_popup()                   # its text is language-bound
        self._close_dialog()
        self._show_landing()   # re-render the landing texts

    def _on_wizard_language_change(self, display_name: str) -> None:
        """Language switch from inside the wizard. Widgets are disposable and
        all state lives on the app (the library and the placements on the
        profile), so the chrome and the current step are simply rebuilt in
        place — nothing the user entered is lost. Ignored while a batch runs
        (the menu is disabled then anyway)."""
        code = self._language_code(display_name)
        if self._running or code is None:
            return
        if code != i18n.get_language():
            i18n.set_language(code)
            self._apply_title()
            self._close_docs_popup()            # its text is language-bound
        self._persist_language(code)
        self._close_dialog()                    # language-bound too
        step = self.step_index
        self._build_wizard()
        self._goto_step(step)      # the current step is always re-enterable

    def _start_wizard(self) -> None:
        self._reset_state()
        self._build_wizard()
        self._goto_step(0)

    # ============================================================= WIZARD
    def _build_wizard(self) -> None:
        self._clear_screen()
        outer = ctk.CTkFrame(self._screen, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=12, pady=(10, 8))

        # --- top bar: brand + language selector + support, every step ----
        self._wizard_lang_menu = self._build_top_bar(
            outer, lang_command=self._on_wizard_language_change)

        # --- stepper bar -------------------------------------------------
        stepper = ctk.CTkFrame(outer)
        stepper.pack(fill="x")
        self._stepper_btns = []
        for i, key in enumerate(_STEP_KEYS):
            btn = ctk.CTkButton(
                stepper, text=f"{i + 1}. {tr(f'step.{key}.short')}",
                height=34, corner_radius=8, border_width=2,
                font=ctk.CTkFont(size=12),
                command=lambda i=i: self._goto_step(i),
            )
            btn.pack(side="left", fill="x", expand=True, padx=3, pady=6)
            self._stepper_btns.append(btn)

        # --- navigation footer (bottom) ----------------------------------
        footer = ctk.CTkFrame(outer)
        footer.pack(fill="x", side="bottom", pady=(8, 0))
        self._btn_cancel = ctk.CTkButton(
            footer, text=tr("nav.cancel"), width=120,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
            command=self._cancel_wizard)
        self._btn_cancel.pack(side="left", padx=8, pady=8)
        self._btn_next = ctk.CTkButton(footer, width=250, command=self._nav_next)
        self._btn_next.pack(side="right", padx=8, pady=8)
        self._btn_prev = ctk.CTkButton(
            footer, width=250, fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"), command=self._nav_prev)
        self._btn_prev.pack(side="right", padx=8, pady=8)

        # --- split body: form left, contextual help right ----------------
        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(8, 0))
        right = ctk.CTkFrame(body, width=_HELP_PANEL_W)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)
        ctk.CTkLabel(right, text=tr("help.heading"),
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).pack(anchor="w", padx=12, pady=(10, 2))
        self._help_box = ctk.CTkTextbox(right, wrap="word", font=ctk.CTkFont(size=12))
        self._help_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        _bold_tag(self._help_box)
        self._help_box.configure(state="disabled")

        left = ctk.CTkFrame(body)
        left.pack(side="left", fill="both", expand=True)
        self._step_header = ctk.CTkLabel(left, text="",
                                         font=ctk.CTkFont(size=17, weight="bold"))
        self._step_header.pack(anchor="w", padx=14, pady=(10, 0))
        self._content_left = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self._content_left.pack(fill="both", expand=True, padx=6, pady=6)

    # ------------------------------------------------------ step routing
    def _first_incomplete(self) -> int:
        for i in range(len(_STEP_KEYS)):
            if not self._step_complete(i):
                return i
        return len(_STEP_KEYS)

    def _goto_step(self, idx: int) -> None:
        self._close_dialog()                        # it edits the step being left
        if self._running or not self._stepper_btns:
            return
        idx = max(0, min(len(_STEP_KEYS) - 1, idx))
        # Steps beyond the first incomplete one are locked — except that
        # going BACK from the current step is always allowed: the step-6
        # selector can re-validate step 3 down to zero accepted files while
        # the user stands on step 6, and they must be able to retreat.
        if idx > max(self._first_incomplete(), self.step_index):
            return
        self.step_index = idx
        key = _STEP_KEYS[idx]
        for w in self._content_left.winfo_children():
            w.destroy()
        self.canvas = None                          # rebuilt by the place step
        self._step_header.configure(text=tr(
            "step.header", n=idx + 1, total=len(_STEP_KEYS),
            title=tr(f"step.{key}.title")))
        _fill_textbox(self._help_box, tr(f"step.{key}.help"))
        getattr(self, f"_build_step_{key}")(self._content_left)
        if key == "validate" and self.validation_results is None:
            self._validate()                        # auto-run on first entry
        self._refresh_chrome()

    def _nav_next(self) -> None:
        if self.step_index == len(_STEP_KEYS) - 1:
            self._finish()
        else:
            self._goto_step(self.step_index + 1)

    def _nav_prev(self) -> None:
        self._goto_step(self.step_index - 1)

    def _finish(self) -> None:
        self._reset_state()
        self._show_landing()

    def _cancel_wizard(self) -> None:
        if self._running:
            return

        def do_cancel():
            self._reset_state()
            self._show_landing()

        self._confirm_modal(tr("cancel.title"), tr("cancel.body"),
                            tr("cancel.confirm"), tr("cancel.keep"),
                            on_confirm=do_cancel)

    def _confirm_modal(self, title, body, confirm_text, keep_text, on_confirm):
        """Small confirmation window (Cancel wizard, Delete signature): the
        red button destroys it then calls ``on_confirm``; the other one just
        closes it. The button row stays the window's LAST child."""
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("480x190")
        win.transient(self)
        win.resizable(False, False)
        ctk.CTkLabel(win, text=title,
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(win, text=body, justify="left",
                     wraplength=440).pack(anchor="w", padx=18)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=18, pady=14)

        def do_confirm():
            win.destroy()
            on_confirm()

        ctk.CTkButton(row, text=confirm_text, width=150,
                      fg_color=_COL_ERROR, hover_color=("#c96a6a", "#8a4444"),
                      command=do_confirm).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text=keep_text, width=150,
                      fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"),
                      command=win.destroy).pack(side="right")
        win.after(80, lambda: _alive(win) and win.grab_set())
        return win

    # ------------------------------------------- completion / error state
    def _mode_error(self) -> str | None:
        if self.mode_var.get() == "azure":
            if not self.azure_vault.strip():
                return tr("azure.vault_missing")
            lvl = self.pades_level_var.get()
            needs_anchors = core.level_needs_ltv(lvl) or core.level_needs_timestamp(lvl)
            if needs_anchors and not self.azure_anchors_path:
                return tr("azure.anchors_missing", level=lvl)
        return None

    def _page_entry_error(self) -> str | None:
        if not self.template_dims or self._page_anchor():
            return None            # field disabled: the anchor fixes the page
        raw = self.page_text.strip()
        if not raw:
            return None
        # len: int() raises on a pasted number of several thousand digits
        if not raw.isdecimal() or len(raw) > _MAX_PAGE_DIGITS or int(raw) < 1:
            return tr("place.page_invalid")
        if int(raw) > len(self.template_dims):
            # blocking: it could only give "page out of range" for
            # every accepted document
            return tr("place.page_beyond", page=int(raw),
                      total=len(self.template_dims))
        return None

    def _place_error(self) -> str | None:
        """First problem of the placement step, or None. beid/azure with no
        enabled signature is complete without a click (default vignette);
        disabled signatures never block."""
        err = self._page_entry_error()
        if err:
            return err
        enabled = self._enabled_signatures()
        for sig in enabled:
            broken = self._sig_error(sig)
            if broken:
                return tr("place.el_broken", name=sig.label, error=broken)
        if self.mode_var.get() == "image" and not enabled:
            return tr("place.need_signature")
        for sig in enabled:
            if not self._sig_placed(sig):
                return tr("place.unplaced", name=sig.label)
        return None

    def _step_complete(self, idx: int) -> bool:
        key = _STEP_KEYS[idx]
        if key == "template":
            return bool(self.template_path and self.template_dims)
        if key == "files":
            return bool(self.input_paths)
        if key == "validate":
            return self.validation_results is not None and bool(self.valid_paths)
        if key == "output":
            return self.output_dir is not None
        if key == "mode":
            return self._mode_error() is None
        if key == "place":
            return self._place_error() is None
        # run + results: reachable/complete once a batch has finished.
        return self.run_results is not None and not self._running

    def _step_error(self, idx: int) -> str | None:
        key = _STEP_KEYS[idx]
        if key == "template":
            return (tr("tpl.unreadable", error=self.template_error)
                    if self.template_error else None)
        if key == "validate":
            if self.validation_results is not None and any(
                    not r.ok for r in self.validation_results):
                ok = len(self.valid_paths)
                return tr("val.summary", ok=ok, total=len(self.validation_results))
            return None
        if key == "mode":
            return self._mode_error()
        if key == "place":
            return self._place_error()
        if key in ("run", "results"):
            if self.run_error:
                return tr("run.error", error=self.run_error)
            if self.run_results is not None and any(not r.ok for r in self.run_results):
                ok = sum(1 for r in self.run_results if r.ok)
                return tr("res.partial", ok=ok, total=len(self.run_results),
                          fail=len(self.run_results) - ok)
        return None

    def _refresh_chrome(self) -> None:
        """Stepper colors + footer buttons, from the current state."""
        if not self._stepper_btns or not _alive(self._stepper_btns[0]):
            return
        first_inc = self._first_incomplete()
        for i, btn in enumerate(self._stepper_btns):
            accessible = (i == self.step_index) if self._running else (
                i <= max(first_inc, self.step_index))   # current + backwards
            error = self._step_error(i) is not None
            # "Completed" (green) only applies to steps the user has passed:
            # a step beyond the first incomplete one is merely unreached,
            # even if its defaults would already satisfy the conditions.
            complete = self._step_complete(i) and i < max(first_inc, self.step_index + 1)
            current = i == self.step_index
            if not accessible:
                border = _COL_LOCKED
            elif error:
                border = _COL_ERROR
            elif complete:
                border = _COL_DONE
            else:
                border = _COL_TODO
            btn.configure(
                state="normal" if accessible else "disabled",
                border_color=border,
                fg_color=_COL_ACCENT if current else "transparent",
                text_color=("white", "white") if current else ("gray10", "gray90"),
            )

        i = self.step_index
        last = len(_STEP_KEYS) - 1
        if i == 0:
            self._btn_prev.configure(text=tr("nav.previous_plain"), state="disabled")
        else:
            self._btn_prev.configure(
                text=tr("nav.previous", step=tr(f"step.{_STEP_KEYS[i - 1]}.title")),
                state="disabled" if self._running else "normal")
        if i == last:
            self._btn_next.configure(
                text=tr("nav.finish"),
                state="disabled" if self._running else "normal")
        else:
            # Next needs the current step AND everything upstream complete:
            # the step-6 first/last selector re-validates step 3, possibly
            # down to zero accepted documents, which must lock the way on.
            can_next = i < first_inc and not self._running
            self._btn_next.configure(
                text=tr("nav.next", step=tr(f"step.{_STEP_KEYS[i + 1]}.title")),
                state="normal" if can_next else "disabled")
        self._btn_cancel.configure(state="disabled" if self._running else "normal")
        if _alive(getattr(self, "_wizard_lang_menu", None)):
            self._wizard_lang_menu.configure(
                state="disabled" if self._running else "normal")

    # ------------------------------------------------------------ tables
    def _make_table(self, parent, columns: list[tuple[str, int]], height: int):
        """ttk table; ``columns`` = [(localized heading, width px), …]. The
        last column takes the remaining width (it follows the window); a
        horizontal scrollbar shows up while its text is wider than that
        (see ``_fill_table``)."""
        names = [c[0] for c in columns]
        table = ttk.Treeview(parent, columns=names, show="headings", height=height)
        for name, width in columns:
            table.heading(name, text=name)
            table.column(name, width=width, anchor="w", stretch=False)
        table.column(names[-1], stretch=True)
        table.heading(names[-1], anchor="w")       # stays in view when scrolled to
        table.pack(fill="x", pady=4, padx=8)
        xbar = ttk.Scrollbar(parent, orient="horizontal", command=table.xview)

        def on_xscroll(first, last) -> None:
            xbar.set(first, last)
            if float(first) <= 0 and float(last) >= 1:      # everything is visible
                xbar.pack_forget()
            else:
                xbar.pack(fill="x", padx=8, after=table)

        table.configure(xscrollcommand=on_xscroll)
        return table

    @staticmethod
    def _fill_table(table, rows) -> None:
        """Replace the rows of a table. Its last column is never narrower
        than its longest text: what the window cannot show is scrolled to."""
        table.delete(*table.get_children())
        for row in rows:
            table.insert("", "end", values=row)
        font = tkfont.Font(font=ttk.Style().lookup("Treeview", "font"))
        need = max((font.measure(row[-1]) for row in rows), default=0) + _TABLE_CELL_PAD
        # width too: ttk then re-fits the column to the space that is left
        table.column(table["columns"][-1], width=need, minwidth=need)

    # ===================================================== step 1: template
    def _build_step_template(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(12, 4), padx=8)
        ctk.CTkButton(row, text=tr("tpl.choose"), width=200,
                      command=self._pick_template).pack(side="left")
        self.template_lbl = ctk.CTkLabel(row, text="", justify="left",
                                         anchor="w", wraplength=440)
        self.template_lbl.pack(side="left", padx=12)
        self._update_template_label()

    def _update_template_label(self) -> None:
        if not _alive(getattr(self, "template_lbl", None)):
            return
        if self.template_error:
            text = tr("tpl.unreadable", error=self.template_error)
        elif self.template_path:
            text = tr("tpl.selected", name=self.template_path.name,
                      pages=len(self.template_dims))
        else:
            text = tr("common.none")
        self.template_lbl.configure(text=text)

    def _pick_template(self) -> None:
        path = filedialog.askopenfilename(title=tr("step.template.title"),
                                          filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.template_path = Path(path)
        self.template_error = None
        self._page_img_cache.clear()              # new template -> new renders
        try:
            self.template_dims = core.page_dimensions(self.template_path)
        except Exception as exc:  # noqa: BLE001
            self.template_dims = []
            self.template_error = str(exc)
        # A new template invalidates validation and results. No saved
        # placement is destroyed: each one is in force again whenever the
        # template page has the size it was placed on (_sig_placed).
        self.validation_results = None
        self.valid_paths = []
        self.cur_page = 0
        self.page_text = ""                       # a stale page number would block step 6
        self.selected_id = None
        self._element_notice = None
        self._preview_cache.clear()               # {filename} sample may change
        self._scaled_cache.clear()
        self._invalidate_run()
        self._update_template_label()
        self._refresh_chrome()

    # ======================================================= step 2: files
    def _build_step_files(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(12, 4), padx=8)
        ctk.CTkButton(row, text=tr("files.choose"), width=200,
                      command=self._pick_inputs).pack(side="left")
        self.inputs_lbl = ctk.CTkLabel(row, text="")
        self.inputs_lbl.pack(side="left", padx=12)
        self.files_box = ctk.CTkTextbox(parent, height=300, font=ctk.CTkFont(size=12))
        self.files_box.pack(fill="both", expand=True, padx=8, pady=6)
        self._update_files_widgets()

    def _update_files_widgets(self) -> None:
        if _alive(getattr(self, "inputs_lbl", None)):
            self.inputs_lbl.configure(
                text=tr("files.count", count=len(self.input_paths))
                if self.input_paths else tr("common.none"))
        if _alive(getattr(self, "files_box", None)):
            self.files_box.configure(state="normal")
            self.files_box.delete("1.0", "end")
            self.files_box.insert("1.0", "\n".join(p.name for p in self.input_paths))
            self.files_box.configure(state="disabled")

    def _pick_inputs(self) -> None:
        paths = filedialog.askopenfilenames(title=tr("step.files.title"),
                                            filetypes=[("PDF", "*.pdf")])
        if not paths:
            return
        self.input_paths = [Path(p) for p in paths]
        self.validation_results = None            # new files -> revalidate
        self.valid_paths = []
        self._invalidate_run()
        self._update_files_widgets()
        self._refresh_chrome()

    # ================================================== step 3: validation
    def _build_step_validate(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(12, 4), padx=8)
        ctk.CTkButton(row, text=tr("val.revalidate"), width=200,
                      command=self._validate).pack(side="left")
        self.val_status_lbl = ctk.CTkLabel(row, text="", justify="left",
                                           anchor="w", wraplength=420)
        self.val_status_lbl.pack(side="left", padx=12)
        self.valid_table = self._make_table(
            parent,
            [(tr("val.col_file"), 240), (tr("val.col_result"), 110),
             (tr("val.col_detail"), 360)],
            height=10,
        )
        # Shown by _update_validation_widgets() only when some files' page
        # count differs from the template: the whole batch is then signed on
        # each file's own first/last page (core: RunConfig.page_anchor).
        self.anchor_row = ctk.CTkFrame(parent, fg_color="transparent")
        inner = ctk.CTkFrame(self.anchor_row, fg_color="transparent")
        inner.pack(fill="x")
        ctk.CTkLabel(inner, text=tr("val.anchor_label"),
                     justify="left", anchor="w", wraplength=440
                     ).pack(side="left")
        self.anchor_menu = ctk.CTkOptionMenu(
            inner, width=180,
            values=[tr("anchor.opt_last"), tr("anchor.opt_first")],
            command=self._on_anchor_menu)
        self.anchor_menu.pack(side="left", padx=8)
        ctk.CTkLabel(self.anchor_row, text=tr("val.anchor_hint"),
                     justify="left", anchor="w", wraplength=620,
                     text_color=("gray25", "gray70"),
                     font=ctk.CTkFont(size=11)).pack(fill="x", pady=(2, 0))
        self._update_validation_widgets()

    def _page_anchor(self) -> str | None:
        """"first"/"last" while the validation-step selector applies, else
        None (all page counts match the template)."""
        return self.anchor_choice if self.count_mismatch else None

    def _on_anchor_menu(self, display: str) -> None:
        code = "first" if display == tr("anchor.opt_first") else "last"
        self._set_page_anchor_choice(code)

    def _set_page_anchor_choice(self, code: str) -> None:
        """Switch the first/last anchor and re-validate (the acceptance of
        page-count mismatches depends on the anchor page)."""
        self.anchor_choice = code if code in core.PAGE_ANCHORS else "last"
        if self.validation_results is not None:
            self._validate()
        else:
            self._sync_anchor_page()
            self._refresh_chrome()

    def _validate(self) -> None:
        """Compare every chosen document with the template (page count +
        exact per-page dimensions); rejected files are excluded from the
        batch. Runs automatically when the step is first shown. Files whose
        page COUNT differs are accepted iff their first/last (anchor) page
        matches the template's — the selector below the table picks which."""
        if not self.template_path or not self.input_paths or not self.template_dims:
            return

        def count_differs(p) -> bool:
            try:
                return len(core.page_dimensions(p)) != len(self.template_dims)
            except Exception:  # noqa: BLE001 - unreadable: reported below
                return False

        self.count_mismatch = any(count_differs(p) for p in self.input_paths)
        anchor = self._page_anchor()
        self.validation_results = [
            core.validate_against_template(self.template_dims, p,
                                           page_anchor=anchor)
            for p in self.input_paths
        ]
        self.valid_paths = [r.path for r in self.validation_results if r.ok]
        self._invalidate_run()
        self._update_validation_widgets()
        self._update_place_anchor_widgets()
        self._sync_anchor_page()
        self._refresh_chrome()

    def _sync_anchor_page(self) -> None:
        """Lock the placement preview onto the template page that will carry
        the elements (first/last) while the anchor selector applies. The
        manual target page is cleared — `page` and `page_anchor` are mutually
        exclusive. No position is dropped: an element placed on another
        template page simply reads "not placed" under this anchor (the
        placement predicates take the anchor) and comes back with it."""
        anchor = self._page_anchor()
        if not anchor or not self.template_dims:
            return
        self.cur_page = 0 if anchor == "first" else len(self.template_dims) - 1
        self._set_page_entry("")
        self._refresh_elements()
        self._update_place_labels()
        self._draw_page()

    def _update_validation_widgets(self) -> None:
        if _alive(getattr(self, "valid_table", None)):
            self._fill_table(self.valid_table, [
                (r.path.name, tr("val.ok") if r.ok else tr("val.rejected"),
                 r.reason or "—")
                for r in self.validation_results or []])
        if _alive(getattr(self, "val_status_lbl", None)):
            if self.validation_results is None:
                self.val_status_lbl.configure(text="")
            else:
                text = tr("val.summary", ok=len(self.valid_paths),
                          total=len(self.validation_results))
                if not self.valid_paths:
                    text += "  " + tr("val.none_valid")
                self.val_status_lbl.configure(text=text)
        if _alive(getattr(self, "anchor_row", None)):
            self.anchor_menu.set(tr(f"anchor.opt_{self.anchor_choice}"))
            if self.count_mismatch:
                self.anchor_row.pack(fill="x", pady=(2, 4), padx=8)
            else:
                self.anchor_row.pack_forget()

    # ====================================================== step 4: output
    def _build_step_output(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(12, 4), padx=8)
        ctk.CTkButton(row, text=tr("out.choose"), width=200,
                      command=self._pick_output).pack(side="left")
        self.output_lbl = ctk.CTkLabel(
            row, text=str(self.output_dir) if self.output_dir else tr("common.none"),
            justify="left", anchor="w", wraplength=440)
        self.output_lbl.pack(side="left", padx=12)

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title=tr("step.output.title"))
        if not path:
            return
        self.output_dir = Path(path)
        self.profile.settings.output_dir = str(self.output_dir)
        self._save_profile()
        self._invalidate_run()
        if _alive(getattr(self, "output_lbl", None)):
            self.output_lbl.configure(text=str(self.output_dir))
        self._refresh_chrome()

    # ======================================================== step 5: mode
    def _build_step_mode(self, parent) -> None:
        def hint(container, key, **pack_kw):
            lbl = ctk.CTkLabel(container, text=tr(key), justify="left", anchor="w",
                               wraplength=_HINT_WRAP,
                               text_color=("gray25", "gray70"),
                               font=ctk.CTkFont(size=11))
            lbl.pack(fill="x", padx=(30, 8), **pack_kw)
            return lbl

        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", pady=(10, 0))
        for value, label_key, hint_key in (
                ("beid", "mode.beid", "mode.beid_hint"),
                ("azure", "mode.azure", "mode.azure_hint"),
                ("image", "mode.image", "mode.image_hint")):
            ctk.CTkRadioButton(box, text=tr(label_key), variable=self.mode_var,
                               value=value, command=self._on_mode_change
                               ).pack(anchor="w", padx=8, pady=(8, 0))
            hint(box, hint_key, pady=(2, 4))
        hint(box, "mode.stamps_hint", pady=(6, 4))

        lrow = ctk.CTkFrame(box, fg_color="transparent")
        lrow.pack(fill="x", padx=8, pady=(8, 0))
        ctk.CTkLabel(lrow, text=tr("mode.level")).pack(side="left")
        ctk.CTkOptionMenu(lrow, variable=self.pades_level_var,
                          values=list(core.PADES_LEVELS), width=110
                          ).pack(side="left", padx=8)
        hint(box, "mode.level_hint", pady=(2, 8))

        # Azure panel (visible only in azure mode).
        self.azure_section = ctk.CTkFrame(box, fg_color="transparent")
        az = self.azure_section
        ctk.CTkLabel(az, text=tr("azure.settings"),
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).pack(anchor="w", padx=8, pady=(6, 0))
        arow1 = ctk.CTkFrame(az, fg_color="transparent")
        arow1.pack(fill="x", padx=8, pady=(4, 0))
        ctk.CTkLabel(arow1, text=tr("azure.vault"), width=170, anchor="w").pack(side="left")
        self.azure_vault_entry = ctk.CTkEntry(arow1)
        self.azure_vault_entry.insert(0, self.azure_vault)
        self.azure_vault_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))
        hint(az, "azure.vault_hint", pady=(2, 6))
        arow2 = ctk.CTkFrame(az, fg_color="transparent")
        arow2.pack(fill="x", padx=8)
        ctk.CTkLabel(arow2, text=tr("azure.key"), width=170, anchor="w").pack(side="left")
        self.azure_key_entry = ctk.CTkEntry(arow2)
        self.azure_key_entry.insert(0, self.azure_key)
        self.azure_key_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))
        hint(az, "azure.key_hint", pady=(2, 6))
        for entry in (self.azure_vault_entry, self.azure_key_entry):
            for seq in ("<KeyRelease>", "<FocusOut>", "<ButtonRelease-2>"):
                entry.bind(seq, lambda _e: self._on_azure_entry_edit())
        arow3 = ctk.CTkFrame(az, fg_color="transparent")
        arow3.pack(fill="x", padx=8)
        ctk.CTkButton(arow3, text=tr("azure.anchors"), width=210,
                      command=self._pick_azure_anchors).pack(side="left")
        self.azure_anchors_lbl = ctk.CTkLabel(
            arow3, text=str(self.azure_anchors_path or tr("azure.anchors_none")),
            justify="left", anchor="w", wraplength=340)
        self.azure_anchors_lbl.pack(side="left", padx=8)
        hint(az, "azure.anchors_hint", pady=(2, 6))
        arow4 = ctk.CTkFrame(az, fg_color="transparent")
        arow4.pack(fill="x", padx=8)
        ctk.CTkLabel(arow4, text=tr("azure.auth"), width=170, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(arow4, variable=self.azure_auth_var,
                          values=list(core.AZURE_AUTH_METHODS), width=140
                          ).pack(side="left", padx=(4, 12))
        self.azure_login_btn = ctk.CTkButton(
            arow4, text=tr("azure.signin"), command=self._azure_sign_in)
        self.azure_login_btn.pack(side="left")
        self.azure_login_lbl = ctk.CTkLabel(
            arow4,
            text=tr("azure.signed_in", upn=self.azure_user_upn)
            if self.azure_user_upn else tr("azure.not_signed_in"))
        self.azure_login_lbl.pack(side="left", padx=8)
        hint(az, "azure.auth_hint", pady=(2, 8))

        ctk.CTkButton(box, text=tr("docs.more"), width=260,
                      fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"),
                      command=self._show_docs_popup).pack(anchor="w", padx=8, pady=(6, 8))
        self._refresh_azure_visibility()

    def _on_mode_change(self) -> None:
        # (the mode_var trace already invalidates the run + refreshes chrome)
        self._refresh_azure_visibility()

    def _on_azure_entry_edit(self) -> None:
        if _alive(getattr(self, "azure_vault_entry", None)):
            new = self.azure_vault_entry.get()
            # Persist only a real edit: a mere focus-out must not turn
            # the env/default value into a saved user choice.
            if new != self.azure_vault:
                self.azure_vault = new
                self.profile.settings.azure_vault_url = new.strip() or None
        if _alive(getattr(self, "azure_key_entry", None)):
            self.azure_key = self.azure_key_entry.get()   # never persisted
        self._on_config_edit()

    def _refresh_azure_visibility(self) -> None:
        if not _alive(getattr(self, "azure_section", None)):
            return
        if self.mode_var.get() == "azure":
            self.azure_section.pack(fill="x", pady=(0, 4))
        else:
            self.azure_section.pack_forget()

    def _show_docs_popup(self) -> None:
        """'Full documentation' window in the active language: the sections
        of i18n.DOC_SECTIONS (modes, levels, AES vs QES, glossary, levels at
        a glance) followed by the clickable sources of i18n.DOC_SOURCES
        (opened in the system browser)."""
        win = getattr(self, "_docs_win", None)
        if _alive(win):
            win.lift()
            win.focus()
            return
        win = ctk.CTkToplevel(self)
        win.title(tr("docs.title"))
        win.geometry("840x780")
        box = ctk.CTkTextbox(win, wrap="word", font=ctk.CTkFont(size=13))
        box.pack(fill="both", expand=True, padx=12, pady=12)
        dark = ctk.get_appearance_mode() == "Dark"
        _bold_tag(box)
        box.tag_config("link", foreground=_COL_LINK[dark], underline=True)
        box.tag_config("url", foreground="gray55" if dark else "gray40")
        for key in i18n.DOC_SECTIONS:
            _insert_markup(box, tr(key))
            box.insert("end", "\n\n\n")
        _insert_markup(box, tr("docs.sources_heading"))
        box.insert("end", "\n\n" + tr("docs.sources_intro") + "\n\n")
        inner = getattr(box, "_textbox", box)     # the tk.Text, for the cursor
        for i, (title_key, url) in enumerate(i18n.DOC_SOURCES):
            tag = f"src{i}"
            box.insert("end", "• ")
            box.insert("end", tr(title_key), ("link", tag))
            box.insert("end", "\n    " + url + "\n", ("url",))
            box.tag_bind(tag, "<Button-1>", lambda _e, u=url: webbrowser.open(u))
            box.tag_bind(tag, "<Enter>", lambda _e: inner.configure(cursor="hand2"))
            box.tag_bind(tag, "<Leave>", lambda _e: inner.configure(cursor=""))
        box.configure(state="disabled")
        self._docs_win = win

    def _close_docs_popup(self) -> None:
        win = getattr(self, "_docs_win", None)
        if _alive(win):
            win.destroy()
        self._docs_win = None

    # ------------------------------------------------------------ azure auth
    def _pick_azure_anchors(self) -> None:
        path = filedialog.askopenfilename(
            title=tr("azure.anchors"),
            filetypes=[("Certificates", "*.pem *.crt *.cer *.der"),
                       ("All files", "*.*")])
        if not path:
            return
        self.azure_anchors_path = Path(path)
        self.profile.settings.azure_trust_anchors = path
        self._save_profile()
        if _alive(getattr(self, "azure_anchors_lbl", None)):
            self.azure_anchors_lbl.configure(text=path)
        self._invalidate_run()
        self._refresh_chrome()

    def _azure_sign_in(self) -> None:
        """Interactive Microsoft login on a WORKER thread (system browser /
        device code), result delivered through a queue + after() — tkinter
        is never touched off the main thread. The credential is cached
        process-wide (azure_signer.get_cached_credential), so the batch
        reuses this login instead of prompting again."""
        self.azure_login_btn.configure(state="disabled")
        self.azure_login_lbl.configure(text=tr("azure.signing_in"))
        self._azure_login_q = queue.Queue()
        method = self.azure_auth_var.get()

        def worker(q: queue.Queue) -> None:
            try:
                import azure_signer as az

                user = az.acquire_user(az.get_cached_credential(method))
                q.put(("ok", user.upn))
            except Exception as exc:  # noqa: BLE001 - report, don't crash
                q.put(("err", str(exc) or exc.__class__.__name__))

        threading.Thread(target=worker, args=(self._azure_login_q,),
                         daemon=True).start()
        self.after(100, self._poll_azure_login)

    def _poll_azure_login(self) -> None:
        if self._azure_login_q is None:
            return
        if not _alive(self):                      # window closed mid-login
            return
        try:
            kind, payload = self._azure_login_q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_azure_login)
            return
        self._azure_login_q = None
        if kind == "ok":
            self.azure_user_upn = payload
        # The user may have navigated away meanwhile: only touch the step-5
        # widgets if they still exist (the state above survives rebuilds).
        if _alive(getattr(self, "azure_login_btn", None)):
            self.azure_login_btn.configure(state="normal")
        if _alive(getattr(self, "azure_login_lbl", None)):
            if kind == "ok":
                self.azure_login_lbl.configure(
                    text=tr("azure.signed_in", upn=payload))
            else:
                self.azure_login_lbl.configure(
                    text=tr("azure.signin_failed", error=payload))

    # =================================================== step 6: placement
    def _build_step_place(self, parent) -> None:
        # Documents whose page count differs from the template: the same
        # first/last choice as on the validation step (shared state
        # `anchor_choice`), mirrored here because it decides which page the
        # preview locks onto. Built only while a mismatch exists — that fact
        # depends on the files/template alone, so it cannot change on this
        # step; switching re-validates and reports the accepted count.
        self.place_anchor_row = None
        if self.count_mismatch:
            self.place_anchor_row = ctk.CTkFrame(parent, fg_color="transparent")
            self.place_anchor_row.pack(fill="x", pady=(10, 2), padx=8)
            inner = ctk.CTkFrame(self.place_anchor_row, fg_color="transparent")
            inner.pack(fill="x")
            ctk.CTkLabel(inner, text=tr("val.anchor_label"), justify="left",
                         anchor="w", wraplength=440).pack(side="left")
            self.place_anchor_menu = ctk.CTkOptionMenu(
                inner, width=180,
                values=[tr("anchor.opt_last"), tr("anchor.opt_first")],
                command=self._on_anchor_menu)
            self.place_anchor_menu.pack(side="left", padx=8)
            self.place_anchor_status = ctk.CTkLabel(
                self.place_anchor_row, text="", justify="left", anchor="w",
                wraplength=620)
            self.place_anchor_status.pack(fill="x", pady=(2, 0))
            ctk.CTkLabel(self.place_anchor_row, text=tr("place.anchor_hint"),
                         justify="left", anchor="w", wraplength=620,
                         text_color=("gray25", "gray70"),
                         font=ctk.CTkFont(size=11)).pack(fill="x")
            self._update_place_anchor_widgets()

        # Page navigation, at FULL width above the editor: it needs up to
        # ~650 px in the longest language, more than the preview column has
        # beside the element list.
        nav = ctk.CTkFrame(parent, fg_color="transparent")
        nav.pack(fill="x", pady=6, padx=8)
        ctk.CTkLabel(nav, text=tr("place.page")).pack(side="left")
        self.page_entry = ctk.CTkEntry(nav, width=64, justify="center")
        self.page_entry.insert(0, self.page_text)
        self.page_entry.bind("<KeyRelease>", lambda _e: self._on_page_entry_change())
        self.page_entry.pack(side="left", padx=(6, 16))
        self.page_prev_btn = ctk.CTkButton(nav, text=tr("place.prev"), width=140,
                                           command=lambda: self._turn_page(-1))
        self.page_prev_btn.pack(side="left")
        self.page_lbl = ctk.CTkLabel(nav, text="")
        self.page_lbl.pack(side="left", padx=10)
        self.page_next_btn = ctk.CTkButton(nav, text=tr("place.next"), width=140,
                                           command=lambda: self._turn_page(1))
        self.page_next_btn.pack(side="left")
        if self._page_anchor():
            # the anchor fixes the signature page per document: manual page
            # and preview navigation are locked (see _sync_anchor_page); the
            # useless page buttons are hidden so the row stays uncluttered
            self.page_entry.configure(state="disabled")
            self.page_prev_btn.configure(state="disabled")
            self.page_next_btn.configure(state="disabled")
            self.page_prev_btn.pack_forget()
            self.page_next_btn.pack_forget()

        # Editor: the element list on the left, the page preview on the right.
        self.editor_row = ctk.CTkFrame(parent, fg_color="transparent")
        self.editor_row.pack(fill="x", padx=8, pady=(4, 0))

        # Left column. Its width is bounded by its children: each one is at
        # most _ELEMENTS_INNER_W wide (a CTkButton GROWS with its label, so
        # the add buttons are stacked, never side by side).
        panel = self.elements_panel = ctk.CTkFrame(self.editor_row)
        panel.pack(side="left", anchor="n", padx=(0, 10))
        ctk.CTkLabel(panel, text=tr("place.elements_title"), anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).pack(anchor="w", padx=10, pady=(8, 2))
        self.add_text_btn = ctk.CTkButton(
            panel, text=tr("sig.add_text"), width=_ELEMENTS_INNER_W,
            command=self._open_text_dialog)
        self.add_image_btn = ctk.CTkButton(
            panel, text=tr("sig.add_image"), width=_ELEMENTS_INNER_W,
            command=self._pick_image_signature)
        self.draw_btn = ctk.CTkButton(
            panel, text=tr("sig.draw"), width=_ELEMENTS_INNER_W,
            command=self._open_draw_dialog)
        for btn in (self.add_text_btn, self.add_image_btn, self.draw_btn):
            btn.pack(padx=10, pady=2)
        # A plain frame, not a CTkScrollableFrame (nesting one inside the
        # step's own scrollable area double-scrolls). _refresh_elements()
        # always leaves a child in it: an empty CTkFrame keeps 200×200.
        self.elements_list = ctk.CTkFrame(panel, fg_color="transparent", height=1)
        self.elements_list.pack(fill="x", padx=10, pady=(8, 4))
        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.pack(anchor="w", padx=10, pady=2)
        self.edit_btn = ctk.CTkButton(actions, text=tr("sig.edit"), width=130,
                                      command=self._edit_selected)
        self.edit_btn.pack(side="left", padx=(0, 6))
        self.delete_btn = ctk.CTkButton(
            actions, text=tr("sig.delete"), width=130,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"), command=self._delete_selected)
        self.delete_btn.pack(side="left")
        self.all_pages_chk = ctk.CTkCheckBox(
            panel, text=tr("place.all_pages"), command=self._toggle_all_pages)
        self.all_pages_chk.pack(anchor="w", padx=10, pady=(8, 4))
        self.reset_btn = ctk.CTkButton(
            panel, text=tr("place.reset"), width=_ELEMENTS_INNER_W,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
            command=self._reset_selected_position)
        self.reset_btn.pack(padx=10, pady=2)
        ctk.CTkLabel(panel, text=tr("place.storage_note"), justify="left",
                     anchor="w", wraplength=_ELEMENTS_INNER_W,
                     text_color=("gray25", "gray70"),
                     font=ctk.CTkFont(size=11)
                     ).pack(anchor="w", padx=10, pady=(6, 8))

        # Right column: position of the selected element, problems, preview.
        self.preview_col = ctk.CTkFrame(self.editor_row, fg_color="transparent")
        self.preview_col.pack(side="left", anchor="n")
        self.pos_lbl = ctk.CTkLabel(self.preview_col, text="", anchor="w",
                                    justify="left", wraplength=320)
        self.pos_lbl.pack(anchor="w")
        self.place_warn_lbl = ctk.CTkLabel(self.preview_col, text="", anchor="w",
                                           justify="left", wraplength=320,
                                           text_color=("#b3261e", "#e08a8a"))
        self.place_warn_lbl.pack(anchor="w")

        # tkinter Canvas: background = rendered template page; a click places
        # the SELECTED element (selection is made in the list only).
        import tkinter as tk
        self.canvas = tk.Canvas(self.preview_col, width=_FRAME_MAX_W,
                                height=_FRAME_MAX_H, bg="#d9d9d9",
                                highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(pady=6)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        # Before the first draw: a restored element is shown on its own page,
        # with its page number in the field.
        self._ensure_selection()
        self._sync_page_to_selection()
        self._refresh_elements()
        self._update_place_labels()
        self._draw_page()

    def _update_place_anchor_widgets(self) -> None:
        """Refresh the step-6 mirror of the first/last selector (menu text +
        accepted-count line) after a (re-)validation."""
        if not _alive(getattr(self, "place_anchor_row", None)):
            return
        self.place_anchor_menu.set(tr(f"anchor.opt_{self.anchor_choice}"))
        self.place_anchor_status.configure(text=tr(
            "place.anchor_status", ok=len(self.valid_paths),
            total=len(self.validation_results or [])))

    # ---------------------------------------------------- step 6: element list
    def _element_status(self, element_id) -> str:
        """Second line of an element's row: where it stands."""
        anchor = self._page_anchor()
        if element_id == VIGNETTE_ID:
            if self._vignette_placed():
                return tr("place.st_page", page=self.profile.settings.vignette.page)
            return tr("place.st_default")
        sig = self._sig(element_id)
        if self._sig_error(sig):
            return tr("place.st_error")
        if not self._sig_placed(sig):
            return tr("place.st_unplaced")
        if anchor:
            return tr(f"anchor.{anchor}_page")
        if sig.stamp.all_pages:
            return tr("place.st_all")
        return tr("place.st_page", page=self._element_page(element_id))

    def _refresh_elements(self) -> None:
        """Rebuild the element rows (vignette + library) and sync the action
        widgets with the selection."""
        if not _alive(getattr(self, "elements_list", None)):
            return
        for w in self.elements_list.winfo_children():
            w.destroy()
        self._element_rows: dict = {}        # id -> row frame
        self._element_labels: dict = {}      # id -> (name label, status label)
        self._element_checks: dict = {}      # signature id -> enabled checkbox
        ids = self._element_ids()
        if not ids:                          # image mode, empty library
            ctk.CTkLabel(self.elements_list, text=tr("place.list_empty"),
                         justify="left", anchor="w",
                         wraplength=_ELEMENTS_INNER_W,
                         text_color=("gray25", "gray70")).pack(anchor="w")
        name_font = ctk.CTkFont(size=13)
        for eid in ids:
            sig = self._sig(eid)
            row = ctk.CTkFrame(
                self.elements_list,
                fg_color=_COL_SELECTED if eid == self.selected_id else "transparent")
            row.pack(fill="x", pady=1)
            if sig is not None:
                chk = ctk.CTkCheckBox(row, text="", width=24,
                                      command=lambda i=eid: self._toggle_enabled(i))
                if sig.enabled:
                    chk.select()
                chk.pack(side="left", padx=(4, 0))
                self._element_checks[eid] = chk
            else:                            # the vignette cannot be disabled
                ctk.CTkFrame(row, fg_color="transparent", width=24, height=24
                             ).pack(side="left", padx=(4, 0))
            # Two stacked labels, not a two-line button: CTkButton has no
            # `justify` and centres the second line.
            col = ctk.CTkFrame(row, fg_color="transparent")
            col.pack(side="left", fill="x", expand=True, padx=(4, 0))
            name = ctk.CTkLabel(
                col, anchor="w", cursor="hand2", font=name_font,
                text=_elide(tr("place.el_vignette") if sig is None else sig.label,
                            _ELEMENTS_INNER_W - 50, name_font))
            name.pack(anchor="w")
            status = ctk.CTkLabel(col, anchor="w", cursor="hand2", height=16,
                                  text=self._element_status(eid),
                                  text_color="gray50", font=ctk.CTkFont(size=11))
            status.pack(anchor="w")
            for w in (row, col, name, status):
                w.bind("<Button-1>", lambda _e, i=eid: self._select(i))
            self._element_rows[eid] = row
            self._element_labels[eid] = (name, status)

        sig = self._sig(self.selected_id)
        anchor = self._page_anchor()
        for btn in (self.edit_btn, self.delete_btn):
            btn.configure(state="normal" if sig else "disabled")
        self.reset_btn.configure(
            state="normal" if self.selected_id in ids else "disabled")
        # select()/deselect() never call `command` and work while disabled
        if sig and sig.stamp.all_pages:
            self.all_pages_chk.select()
        else:
            self.all_pages_chk.deselect()
        self.all_pages_chk.configure(
            state="normal" if sig and not anchor else "disabled")

    def _refresh_place(self) -> None:
        """List, labels, preview and stepper after an element changed. Every
        part is a no-op while step 6 is not built."""
        self._refresh_elements()
        self._update_place_labels()
        self._draw_page()
        self._refresh_chrome()

    def _commit_elements(self) -> None:
        """End of every library/placement mutation: persist, drop the
        results of a previous run, refresh the step."""
        self._save_profile()
        self._invalidate_run()
        self._refresh_place()

    # ----------------------------------------------- step 6: element operations
    def _select(self, element_id) -> None:
        self._element_notice = None
        self.selected_id = element_id
        self._sync_page_to_selection()       # preview + page field follow it
        self._refresh_place()

    def _add_signature(self, stamp, label: str):
        """Append a signature to the library and select it. Safe to call
        while step 6 is not built."""
        self._element_notice = None
        sig = profile_store.Signature(id=profile_store.new_id(),
                                      label=label[:profile_store.MAX_LABEL_LEN], stamp=stamp)
        self.profile.signatures.append(sig)
        self.selected_id = sig.id
        self._sync_page_to_selection()
        self._commit_elements()
        return sig

    def _add_image_signature(self, path, label=None):
        """Copy an image into the user's signature store and add it as an
        image signature. When it cannot be read or stored NOTHING is added
        (no fallback to the original file) and the reason is shown."""
        self._element_notice = None
        try:
            stored = self.store.import_image(Path(path))
        except stamplib.StampError as exc:           # not an image
            self._element_notice = tr("sig.err_image", error=exc)
        except OSError as exc:                       # the data dir cannot be written
            self._element_notice = tr("sig.err_store", error=exc)
        else:
            return self._add_signature(
                stamplib.Stamp(kind="image", image_path=stored),
                label or Path(path).stem)
        self._update_place_labels()
        return None

    def _pick_image_signature(self) -> None:
        path = filedialog.askopenfilename(
            title=tr("sig.add_image"),
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")])
        if path:
            self._add_image_signature(path)

    def _toggle_enabled(self, sig_id) -> None:
        sig = self._sig(sig_id)
        if sig is None:
            return
        self._element_notice = None
        sig.enabled = not sig.enabled        # the model decides; the list re-syncs
        self._commit_elements()

    def _toggle_all_pages(self) -> None:
        """"On every page" for the selected signature. The direction comes
        from the MODEL, never from the checkbox (the list re-syncs it)."""
        sig = self._sig(self.selected_id)
        if sig is None or self._page_anchor():
            return
        self._element_notice = None
        if not sig.stamp.all_pages:
            sig.stamp = dataclasses.replace(sig.stamp, all_pages=True,
                                            page=None, page_anchor=None)
        else:                                # back onto the previewed page
            # _sig_placed, not "has coordinates": a placement saved for
            # another page size must stay unplaced until the user clicks.
            placed = self._sig_placed(sig)
            sig.stamp = dataclasses.replace(
                sig.stamp, all_pages=False,
                page=(self.cur_page + 1) if placed else None)
            if placed:
                sig.placed_on = tuple(self.template_dims[self.cur_page])
        self._sync_page_to_selection()
        self._commit_elements()

    def _reset_selected_position(self) -> None:
        """Forget the selected element's position (the vignette goes back to
        its default corner; a signature keeps its "every page" flag)."""
        self._element_notice = None
        if self.selected_id == VIGNETTE_ID:
            self.profile.settings.vignette = None
        else:
            sig = self._sig(self.selected_id)
            if sig is None:
                return
            sig.stamp = dataclasses.replace(sig.stamp, page=None, page_anchor=None,
                                            x=None, y=None)
            sig.placed_on = None
        self._sync_page_to_selection()       # empties the field
        self._commit_elements()

    def _edit_selected(self) -> None:
        sig = self._sig(self.selected_id)
        if sig is None:
            return
        if sig.stamp.kind == "text":
            self._open_text_dialog(sig)
        else:
            self._open_image_dialog(sig)

    def _delete_selected(self) -> None:
        sig = self._sig(self.selected_id)
        if sig is None:
            return
        self._confirm_modal(
            tr("sig.delete_title"), tr("sig.delete_body", name=sig.label),
            tr("sig.delete_confirm"), tr("sig.delete_keep"),
            on_confirm=lambda: self._delete_signature(sig.id))

    def _delete_signature(self, sig_id) -> None:
        """Remove a signature from the library, with its stored image when
        no other signature uses it (profile_store decides)."""
        self._element_notice = None
        self.store.remove_signature(self.profile, sig_id)
        self._preview_cache.clear()
        self._scaled_cache.clear()
        self.selected_id = None
        self._ensure_selection()             # re-picks + re-syncs the page field
        self._commit_elements()

    def _close_dialog(self) -> None:
        """Close the editor dialog, if any (step change, reset, language)."""
        if _alive(self._dialog):
            self._dialog.destroy()
        self._dialog = None
        self._dialog_state = {}

    # ------------------------------------------------- step 6: editor dialogs
    # One non-blocking window at a time (self._dialog), closed by any step
    # change, reset or language switch. Its fields are plain widgets read
    # with .get() — no tk variable (see __init__); the non-widget values
    # (font, colour, strokes) live in self._dialog_state.
    def _open_dialog(self, title: str, geometry: str):
        """Open the editor dialog (any previous one is closed first). No
        wait_window: the handlers do the work, so tests can drive them."""
        self._close_dialog()
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry(geometry)
        win.transient(self)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._close_dialog)   # same as Cancel
        self._dialog = win
        win.after(80, lambda: _alive(win) and win.grab_set())
        return win

    def _dialog_footer(self, win, on_save, *, wrap: int) -> None:
        """Last row of every dialog (Cancel / Save) with the red error label
        above it. Packed FIRST, from the bottom: whatever the content needs,
        the buttons stay inside the fixed-size window."""
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=18, pady=(4, 14))
        self.dlg_save_btn = ctk.CTkButton(row, text=tr("sig.save"), width=150,
                                          command=on_save)
        self.dlg_save_btn.pack(side="right", padx=(8, 0))
        self.dlg_cancel_btn = ctk.CTkButton(
            row, text=tr("sig.cancel"), width=150,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"), command=self._close_dialog)
        self.dlg_cancel_btn.pack(side="right")
        self.dlg_error_lbl = ctk.CTkLabel(win, text="", anchor="w", justify="left",
                                          wraplength=wrap,
                                          text_color=("#b3261e", "#e08a8a"))
        self.dlg_error_lbl.pack(side="bottom", fill="x", padx=18)

    def _dialog_error(self, message: str) -> None:
        if _alive(getattr(self, "dlg_error_lbl", None)):
            self.dlg_error_lbl.configure(text=message)

    @staticmethod
    def _dialog_number(raw: str, maximum: float) -> float | None:
        """A dialog field as a number in (0, maximum] (decimal comma
        accepted), or None."""
        try:
            value = float(raw.strip().replace(",", "."))
        except ValueError:
            return None
        return value if 0 < value <= maximum else None     # (NaN fails both)

    def _blank_ctk_image(self):
        """1×1 transparent image standing for "no preview", created once per
        app (a CTkImage binds to the root that first renders it)."""
        if self._blank_img is None:
            pil = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            self._blank_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(1, 1))
        return self._blank_img

    def _preview_box(self, win, height: int):
        """White box holding the dialog's preview label (starts blank)."""
        frame = ctk.CTkFrame(win, height=height, fg_color="white")
        frame.pack(fill="x", padx=18)
        frame.pack_propagate(False)               # fixed height, whatever it shows
        self.dlg_preview_lbl = ctk.CTkLabel(frame, text="")
        self.dlg_preview_lbl.pack(expand=True)
        self._set_dialog_preview(None)

    def _set_dialog_preview(self, img, fit=(1, 1), max_zoom: float = 1.0) -> None:
        """Show a PIL image in the dialog's preview label, scaled down to fit
        ``fit`` and never enlarged beyond ``max_zoom``; None shows nothing.
        Each preview is a FRESH CTkImage pinned on the label, and "nothing"
        is the blank image — never ``image=None``: with it CustomTkinter
        5.2.2 leaves the previous picture on screen, and once its last
        reference is gone the next configure raises TclError."""
        if img is None:
            shown = self._blank_ctk_image()
        else:
            zoom = min(fit[0] / img.width, fit[1] / img.height, max_zoom)
            size = (max(1, round(img.width * zoom)), max(1, round(img.height * zoom)))
            small = img.copy()
            small.thumbnail((size[0] * 2, size[1] * 2))   # HiDPI headroom, cheap rescale
            shown = ctk.CTkImage(light_image=small, dark_image=small, size=size)
        self.dlg_preview_lbl.configure(image=shown)
        self.dlg_preview_lbl._cachet_img = shown  # noqa: SLF001 - keep-alive ref

    def _paint_color_button(self) -> None:
        color = self._dialog_state["color"]
        self.dlg_color_btn.configure(text=color, fg_color=color, hover_color=color,
                                     text_color=_text_on(color))

    def _pick_dialog_color(self) -> None:
        """Colour of the text / of the drawing, through the native chooser."""
        state = self._dialog_state
        _rgb, hexv = colorchooser.askcolor(
            color=state["color"], parent=self._dialog, title=tr("sig.color_title"))
        if not _alive(self._dialog) or not hexv:  # closed meanwhile, or cancelled
            return
        state["color"] = hexv.upper()
        self._paint_color_button()
        if state["kind"] == "draw":               # the strokes already on screen
            self.dlg_draw_canvas.itemconfigure("all", fill=state["color"])
        else:
            self._update_text_preview()

    # --- text signature (add / edit)
    @staticmethod
    def _font_display(font: str) -> str:
        """Menu text of a font: a bundled font's name, a custom file's name."""
        bundled = stamplib.BUNDLED_FONTS.get(font.strip().lower())
        return bundled[0] if bundled else Path(font).name

    def _open_text_dialog(self, sig=None) -> None:
        """Add (``sig`` None) or edit a text signature: text, font, size and
        colour, with a live preview rendered by the stamping code itself."""
        win = self._open_dialog(
            tr("sig.text_title_edit" if sig else "sig.text_title_add"), "560x600")
        stamp = sig.stamp if sig else stamplib.Stamp(kind="text")
        self._dialog_state = {"kind": "text", "sig_id": sig.id if sig else None,
                              "font": stamp.font, "color": stamp.color.upper()}
        self._dialog_footer(win, self._apply_text_dialog, wrap=520)
        gray = {"text_color": ("gray25", "gray70"), "font": ctk.CTkFont(size=11)}

        ctk.CTkLabel(win, text=tr("sig.name")).pack(anchor="w", padx=18, pady=(12, 0))
        self.dlg_name_entry = ctk.CTkEntry(win)
        if sig:
            self.dlg_name_entry.insert(0, sig.label)
        self.dlg_name_entry.pack(fill="x", padx=18)
        ctk.CTkLabel(win, text=tr("sig.text")).pack(anchor="w", padx=18, pady=(6, 0))
        # wrap="none": the stamp breaks lines only at real newlines, so the
        # box must not wrap visually (a long line would look like several
        # and be stamped as one, wider than the page). It scrolls instead.
        self.dlg_text_box = ctk.CTkTextbox(win, height=90, wrap="none")
        self.dlg_text_box.insert("1.0", stamp.text)
        self.dlg_text_box.pack(fill="x", padx=18)
        # no kwargs: {date} and {filename} are shown literally
        ctk.CTkLabel(win, text=tr("sig.text_hint"), justify="left", anchor="w",
                     wraplength=520, **gray).pack(anchor="w", padx=18, pady=(2, 4))

        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=2)
        ctk.CTkLabel(row, text=tr("sig.font")).pack(side="left")
        self.dlg_font_menu = ctk.CTkOptionMenu(
            row, width=240,
            values=[name for _id, name in stamplib.font_choices()]
            + [tr("sig.font_custom")],
            command=self._on_dialog_font)
        self.dlg_font_menu.set(self._font_display(stamp.font))
        self.dlg_font_menu.pack(side="left", padx=8)

        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=2)
        ctk.CTkLabel(row, text=tr("sig.size")).pack(side="left")
        self.dlg_size_entry = ctk.CTkEntry(row, width=70, justify="center")
        self.dlg_size_entry.insert(0, f"{stamp.font_size:g}")
        self.dlg_size_entry.pack(side="left", padx=(8, 18))
        ctk.CTkLabel(row, text=tr("sig.color")).pack(side="left")
        self.dlg_color_btn = ctk.CTkButton(row, width=110,
                                           command=self._pick_dialog_color)
        self.dlg_color_btn.pack(side="left", padx=8)
        self._paint_color_button()

        ctk.CTkLabel(win, text=tr("sig.preview")).pack(anchor="w", padx=18, pady=(6, 0))
        self._preview_box(win, 130)
        self.dlg_size_lbl = ctk.CTkLabel(win, text="", height=18, **gray)
        self.dlg_size_lbl.pack(anchor="w", padx=18)
        for widget in (self.dlg_text_box, self.dlg_size_entry):
            for seq in ("<KeyRelease>", "<ButtonRelease-2>"):
                widget.bind(seq, self._on_text_dialog_edit)
        self._update_text_preview()

    def _on_text_dialog_edit(self, _event=None) -> None:
        """The user typed (or pasted) in the text dialog."""
        self._dialog_state["edited"] = True
        self._update_text_preview()

    def _on_dialog_font(self, display: str) -> None:
        state = self._dialog_state
        if display == tr("sig.font_custom"):
            path = filedialog.askopenfilename(
                parent=self._dialog, title=tr("sig.font_custom"),
                filetypes=[("Fonts", "*.ttf *.otf")])
            if not _alive(self._dialog):          # closed while the picker was open
                return
            if path:
                state["font"] = path
            # shows the file's name — or, cancelled, the font still in use
            self.dlg_font_menu.set(self._font_display(state["font"]))
        else:
            state["font"] = next(
                (fid for fid, name in stamplib.font_choices() if name == display),
                state["font"])
        self._update_text_preview()

    def _dialog_text_stamp(self):
        """``(stamp, None)`` from the text dialog's fields, or ``(None,
        message)`` when the text is empty or the size is not usable."""
        text = self.dlg_text_box.get("1.0", "end-1c")
        if not text.strip():
            return None, tr("sig.err_text_empty")
        size = self._dialog_number(self.dlg_size_entry.get(), stamplib.MAX_FONT_SIZE)
        if size is None:
            return None, tr("sig.err_size", max=int(stamplib.MAX_FONT_SIZE))
        state = self._dialog_state
        return stamplib.Stamp(kind="text", text=text, font=state["font"],
                              color=state["color"], font_size=size), None

    def _update_text_preview(self) -> None:
        """Render the text as it will be stamped (same code as the PDF), on
        every change. An oversized text is refused by the pixel budget in
        milliseconds, so typing can never freeze the window; whatever goes
        wrong becomes the dialog's message, never a traceback in a Tk
        callback."""
        if not _alive(self._dialog) or self._dialog_state.get("kind") != "text":
            return
        stamp, error = self._dialog_text_stamp()
        content = None
        if stamp is not None:
            try:
                content = stamplib.stamp_content(
                    stamp, filename=self._sample_filename())
            except Exception as exc:  # noqa: BLE001 - MemoryError, font, budget, …
                error = tr("sig.err_render", error=str(exc) or type(exc).__name__)
        elif (not self._dialog_state.get("edited")
              and not self.dlg_text_box.get("1.0", "end-1c").strip()):
            # A dialog just opened on an empty text: no reproach before the
            # user has typed anything or tried to save.
            error = None
        self._dialog_error(error or "")
        if content is None:
            self._set_dialog_preview(None)
            self.dlg_size_lbl.configure(text="")
            return
        img, (w_pt, h_pt) = content
        # at most 1.5 screen px per point: a small text is not blown up, and
        # the size line below tells how large it really is on the page
        self._set_dialog_preview(img, fit=(500, 120),
                                 max_zoom=1.5 * 72 / stamplib.TEXT_DPI)
        self.dlg_size_lbl.configure(
            text=tr("sig.preview_size", w=round(w_pt), h=round(h_pt)))

    def _apply_text_dialog(self) -> None:
        """Save of the text dialog. On an error the dialog stays open with
        the message; an oversized text is refused here for good (never added
        to the library, never saved)."""
        if not _alive(self._dialog) or self._dialog_state.get("kind") != "text":
            return
        self._dialog_state["edited"] = True       # from now on, errors stay visible
        stamp, error = self._dialog_text_stamp()
        if stamp is not None:
            try:
                stamplib.validate_stamp(stamp, placed=False)
            except stamplib.StampError as exc:
                error = tr("sig.err_render", error=exc)
        if error:
            self._dialog_error(error)
            return
        first_line = next(ln.strip() for ln in stamp.text.splitlines() if ln.strip())
        label = (self.dlg_name_entry.get().strip()
                 or first_line[:40])[:profile_store.MAX_LABEL_LEN]
        sig = self._sig(self._dialog_state["sig_id"])
        if sig is None:
            self._add_signature(stamp, label)
        else:                                     # placement and placed_on are KEPT
            sig.stamp = dataclasses.replace(
                sig.stamp, text=stamp.text, font=stamp.font, color=stamp.color,
                font_size=stamp.font_size)
            sig.label = label
            self._content_changed()
        self._close_dialog()

    def _content_changed(self) -> None:
        """A signature's content was edited: its cached previews are stale."""
        self._element_notice = None
        self._preview_cache.clear()
        self._scaled_cache.clear()
        self._commit_elements()

    # --- freehand drawing (add)
    def _open_draw_dialog(self) -> None:
        """Draw a signature by hand. The strokes are the model — the canvas
        is only their on-screen echo — and stamps.render_strokes_image turns
        them into a transparent PNG kept in the user's signature store."""
        import tkinter as tk
        win = self._open_dialog(tr("sig.draw_title"), "660x420")
        self._dialog_state = {"kind": "draw", "sig_id": None,
                              "color": stamplib.DEFAULT_COLOR, "strokes": []}
        self._dialog_footer(win, self._apply_draw_dialog, wrap=620)
        ctk.CTkLabel(win, text=tr("sig.draw_hint"), justify="left", anchor="w",
                     wraplength=620, text_color=("gray25", "gray70"),
                     font=ctk.CTkFont(size=11)
                     ).pack(anchor="w", padx=18, pady=(12, 4))
        self.dlg_draw_canvas = tk.Canvas(
            win, width=_DRAW_W, height=_DRAW_H, bg="white", highlightthickness=1,
            highlightbackground="#888", cursor="pencil")
        self.dlg_draw_canvas.pack(padx=18)
        self.dlg_draw_canvas.bind("<Button-1>", self._draw_start)
        self.dlg_draw_canvas.bind("<B1-Motion>", self._draw_move)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(6, 2))
        self.dlg_color_btn = ctk.CTkButton(row, width=110,
                                           command=self._pick_dialog_color)
        self.dlg_color_btn.pack(side="left")
        self._paint_color_button()
        self.dlg_clear_btn = ctk.CTkButton(
            row, text=tr("sig.draw_clear"), width=110,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"), command=self._draw_clear)
        self.dlg_clear_btn.pack(side="left", padx=8)

    @staticmethod
    def _draw_point(event) -> tuple[float, float]:
        """Pointer position, clamped to the drawing canvas: a drag goes on
        outside it, and far-away points would only inflate the raster."""
        return (min(max(event.x, 0), _DRAW_W), min(max(event.y, 0), _DRAW_H))

    def _draw_start(self, event) -> None:
        state = self._dialog_state
        if state.get("kind") != "draw":
            return
        self._dialog_error("")                     # "draw something first" is answered
        x, y = self._draw_point(event)
        state["strokes"].append([(x, y)])
        r = _DRAW_PEN / 2                          # a click alone leaves a dot
        self.dlg_draw_canvas.create_oval(x - r, y - r, x + r, y + r,
                                         fill=state["color"], outline="")

    def _draw_move(self, event) -> None:
        state = self._dialog_state
        if not state.get("strokes"):               # motion without a press
            return
        x, y = self._draw_point(event)
        px, py = state["strokes"][-1][-1]
        state["strokes"][-1].append((x, y))
        self.dlg_draw_canvas.create_line(px, py, x, y, width=_DRAW_PEN,
                                         capstyle="round", fill=state["color"])

    def _draw_clear(self) -> None:
        if self._dialog_state.get("kind") != "draw":
            return
        self._dialog_state["strokes"] = []
        self.dlg_draw_canvas.delete("all")
        self._dialog_error("")

    def _apply_draw_dialog(self) -> None:
        """Save of the drawing dialog: rasterise, store, add as an image
        signature. Nothing is added when the drawing is empty or cannot be
        stored; the dialog then stays open with the reason."""
        state = self._dialog_state
        if not _alive(self._dialog) or state.get("kind") != "draw":
            return
        if not any(state["strokes"]):
            self._dialog_error(tr("sig.err_draw_empty"))
            return
        try:
            img = stamplib.render_strokes_image(
                state["strokes"], state["color"], width=_DRAW_PEN, scale=_DRAW_SCALE)
        except stamplib.StampError:                # the pixel budget
            self._dialog_error(tr("sig.err_draw_too_large"))
            return
        try:
            stored = self.store.store_image(img)
        except OSError as exc:                     # the data dir cannot be written
            self._dialog_error(tr("sig.err_store", error=exc))
            return
        width_pt = min(240.0, max(30.0, round(
            img.width / _DRAW_SCALE * _DRAW_PT_PER_PX, 1)))
        self._add_signature(
            stamplib.Stamp(kind="image", image_path=stored, width_pt=width_pt),
            tr("sig.drawn_label"))
        self._close_dialog()

    # --- image signature (edit only: a new one comes from the file picker)
    def _open_image_dialog(self, sig) -> None:
        """Edit an image signature: its name and its width on the page."""
        win = self._open_dialog(tr("sig.image_title"), "480x380")
        self._dialog_state = {"kind": "image", "sig_id": sig.id}
        self._dialog_footer(win, self._apply_image_dialog, wrap=440)
        ctk.CTkLabel(win, text=tr("sig.name")).pack(anchor="w", padx=18, pady=(12, 0))
        self.dlg_name_entry = ctk.CTkEntry(win)
        self.dlg_name_entry.insert(0, sig.label)
        self.dlg_name_entry.pack(fill="x", padx=18)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(6, 8))
        ctk.CTkLabel(row, text=tr("sig.width")).pack(side="left")
        self.dlg_width_entry = ctk.CTkEntry(row, width=80, justify="center")
        self.dlg_width_entry.insert(0, f"{sig.stamp.width_pt:g}")
        self.dlg_width_entry.pack(side="left", padx=8)
        self._preview_box(win, 170)
        content = self._content(sig)
        if content is None:                        # missing / unreadable file
            self._dialog_error(tr("sig.err_image", error=self._sig_error(sig)))
        else:
            self._set_dialog_preview(content[0], fit=(420, 160))

    def _apply_image_dialog(self) -> None:
        if not _alive(self._dialog) or self._dialog_state.get("kind") != "image":
            return
        sig = self._sig(self._dialog_state["sig_id"])
        width = self._dialog_number(self.dlg_width_entry.get(), stamplib.MAX_WIDTH_PT)
        if width is None:
            self._dialog_error(tr("sig.err_width", max=int(stamplib.MAX_WIDTH_PT)))
            return
        if sig is not None:
            sig.stamp = dataclasses.replace(sig.stamp, width_pt=width)
            sig.label = (self.dlg_name_entry.get().strip()
                         or sig.label)[:profile_store.MAX_LABEL_LEN]
            self._content_changed()
        self._close_dialog()

    def _set_page_entry(self, text: str) -> None:
        """Programmatic update of the target-page field (click, reset)."""
        self.page_text = text
        if _alive(getattr(self, "page_entry", None)):
            self.page_entry.delete(0, "end")
            self.page_entry.insert(0, text)

    def _on_page_entry_change(self) -> None:
        """Manual target-page field: a valid page number moves the SELECTED
        element (when it is placed on a numbered page) to that page and the
        preview follows. A number beyond the template blocks the step."""
        if self._running or self._page_anchor():
            return
        if _alive(getattr(self, "page_entry", None)):
            self.page_text = self.page_entry.get()
        raw = self.page_text.strip()
        if (raw.isdecimal() and len(raw) <= _MAX_PAGE_DIGITS
                and 1 <= int(raw) <= len(self.template_dims)):
            v = int(raw)
            dims = tuple(self.template_dims[v - 1])
            sig = self._sig(self.selected_id)
            if self.selected_id == VIGNETTE_ID and self._vignette_placed():
                vignette = self.profile.settings.vignette
                vignette.page, vignette.placed_on = v, dims
                self._save_profile()
            elif sig and self._sig_placed(sig) and not sig.stamp.all_pages:
                sig.stamp = dataclasses.replace(sig.stamp, page=v, page_anchor=None)
                sig.placed_on = dims
                self._save_profile()
            self.cur_page = v - 1
        self._invalidate_run()
        self._refresh_place()

    def _position_text(self) -> str:
        """Position line of the SELECTED element."""
        anchor = self._page_anchor()
        all_pages = False
        if self.selected_id == VIGNETTE_ID:
            if not self._vignette_placed():
                return (tr("place.pos_default_anchor",
                           anchor=tr(f"anchor.{anchor}_page"))
                        if anchor else tr("place.pos_default"))
            vignette = self.profile.settings.vignette
            x, y, page = vignette.x, vignette.y, vignette.page
        else:
            sig = self._sig(self.selected_id)
            if sig is None:
                return tr("place.select_hint")
            if not self._sig_placed(sig):
                return tr("place.pos_none")
            x, y, page = sig.stamp.x, sig.stamp.y, self._element_page(sig.id)
            all_pages = sig.stamp.all_pages
        xy = {"x": f"{x:.0f}", "y": f"{y:.0f}"}
        if anchor:
            return tr("place.pos_anchor", anchor=tr(f"anchor.{anchor}_page"), **xy)
        if all_pages:
            return tr("place.pos_all", **xy)
        return tr("place.pos", page=page, **xy)

    def _update_place_labels(self) -> None:
        if _alive(getattr(self, "pos_lbl", None)):
            self.pos_lbl.configure(text=self._position_text())
        if _alive(getattr(self, "place_warn_lbl", None)):
            warn = self._element_notice or self._place_error() or ""
            if not warn and self._profile_error:
                warn = tr("place.save_failed", error=self._profile_error)
            self.place_warn_lbl.configure(text=warn)

    def _turn_page(self, delta: int) -> None:
        if not self.template_dims or self._page_anchor():
            return  # anchored: the preview is locked on the anchor page
        self.cur_page = max(0, min(len(self.template_dims) - 1, self.cur_page + delta))
        self._draw_page()

    def _canvas_target_size(self) -> tuple[int, int]:
        """Target canvas size, derived from the window (grows/shrinks with it)."""
        w = max(320, self.winfo_width() - _HELP_PANEL_W - _ELEMENTS_PANEL_W - 170)
        h = max(260, int(self.winfo_height() * 0.45))
        return w, h

    def _get_page_image(self, page_index):
        """Full-resolution (PIL) image of the template page, cached."""
        if not self.template_path:
            return None
        key = (str(self.template_path), page_index)
        if key not in self._page_img_cache:
            self._page_img_cache[key] = core.render_page_image(
                self.template_path, page_index, px_width=900
            )
        return self._page_img_cache[key]

    def _draw_page(self) -> None:
        if not _alive(self.canvas) or not self.template_dims:
            return
        anchor = self._page_anchor()
        if anchor:  # preview locked onto the page that carries the signature
            self.cur_page = 0 if anchor == "first" else len(self.template_dims) - 1
        cw, ch = self._canvas_target_size()
        self.canvas.configure(width=cw, height=ch)
        self.canvas.delete("all")
        self._canvas_imgs = []                      # keep-alive refs, per draw
        pw, ph = self.template_dims[self.cur_page]
        fw, fh = core.fit_frame(pw, ph, cw, ch)
        ox, oy = (cw - fw) / 2, (ch - fh) / 2
        pil = self._get_page_image(self.cur_page)   # background = actual page render
        if pil is not None:
            self._bg_img = ImageTk.PhotoImage(
                pil.resize((max(1, int(fw)), max(1, int(fh))))
            )
            self.canvas.create_image(ox, oy, anchor="nw", image=self._bg_img)
            self.canvas.create_rectangle(ox, oy, ox + fw, oy + fh, outline="#333")
        else:                                       # render unavailable -> blank frame
            self.canvas.create_rectangle(ox, oy, ox + fw, oy + fh,
                                         fill="white", outline="#333")
        if _alive(getattr(self, "page_lbl", None)):
            label = tr("place.preview_page", cur=self.cur_page + 1,
                       total=len(self.template_dims))
            if anchor:
                label += tr("place.locked_suffix",
                            anchor=tr(f"anchor.{anchor}_page"))
            self.page_lbl.configure(text=label)
        self._frame_geom = (fw, fh, ox, oy)
        self._draw_elements()

    def _on_canvas_click(self, event) -> None:
        """A click PLACES the selected element: the clicked point becomes its
        lower-left corner on the previewed page (no hit-testing — selection
        is made in the list only)."""
        if not self.template_dims or not hasattr(self, "_frame_geom"):
            return
        fw, fh, ox, oy = self._frame_geom
        cx, cy = event.x - ox, event.y - oy
        if not (0 <= cx <= fw and 0 <= cy <= fh):
            return
        self._element_notice = None
        self._ensure_selection()
        if self.selected_id is None:              # nothing to place yet
            self._update_place_labels()
            return
        pw, ph = self.template_dims[self.cur_page]
        x, y = core.frame_click_to_pdf_xy(pw, ph, fw, fh, cx, cy)
        page = self.cur_page + 1                  # the anchor page while locked
        dims = tuple(self.template_dims[self.cur_page])
        all_pages = False
        if self.selected_id == VIGNETTE_ID:
            self.profile.settings.vignette = profile_store.Placement(
                page=page, x=x, y=y, placed_on=dims)
        else:
            sig = self._sig(self.selected_id)
            all_pages = sig.stamp.all_pages       # kept: only x/y move
            sig.stamp = dataclasses.replace(
                sig.stamp, x=x, y=y, page_anchor=None,
                page=None if all_pages else page)
            sig.placed_on = dims
        if self._page_anchor() is None and not all_pages:
            self._set_page_entry(str(page))       # click -> target page
        self._commit_elements()

    def _sig_on_preview(self, sig) -> bool:
        """True if the signature's placement is in force AND lands on the
        previewed page (under an anchor the preview is locked on the one
        page every element goes to)."""
        if not self._sig_placed(sig):
            return False
        return bool(self._page_anchor()) or (
            self.cur_page in sig.stamp.page_indexes(len(self.template_dims)))

    def _vignette_on_preview(self) -> bool:
        if not self._has_vignette():
            return False
        if self._vignette_placed():
            return self.profile.settings.vignette.page == self.cur_page + 1
        # default corner box: the anchor page (the preview is locked on it),
        # else the template's LAST page
        return bool(self._page_anchor()) or self.cur_page == len(self.template_dims) - 1

    def _draw_elements(self) -> None:
        """Every element shown on the previewed page, to scale: the enabled
        signatures in library order, then the vignette; the selected element
        last, so it is never hidden under another one."""
        shown = [s.id for s in self.profile.signatures
                 if s.enabled and self._sig_on_preview(s)]
        if self._vignette_on_preview():
            shown.append(VIGNETTE_ID)
        selected = self._sig(self.selected_id)
        if self.selected_id in shown:
            shown.remove(self.selected_id)
            shown.append(self.selected_id)
        elif selected and not selected.enabled and self._sig_on_preview(selected):
            # an unticked signature is drawn only while selected (outline
            # only), so the user sees where a click put it
            shown.append(self.selected_id)
        for element_id in shown:
            self._draw_element(element_id)

    def _scaled_preview(self, sig, thumb, w: int, h: int):
        """The cached thumbnail resized for the canvas, itself cached per
        size: a window resize rescales, it never re-renders the content."""
        key = (self._content_key(sig), w, h)
        if key not in self._scaled_cache:
            if len(self._scaled_cache) > _SCALED_CACHE_MAX:
                self._scaled_cache.clear()
            self._scaled_cache[key] = thumb.resize((w, h), Image.LANCZOS)
        return self._scaled_cache[key]

    def _draw_element(self, element_id) -> None:
        fw, fh, ox, oy = self._frame_geom
        pw, ph = self.template_dims[self.cur_page]
        selected = element_id == self.selected_id
        sig = self._sig(element_id)
        if sig is not None:
            rect = (sig.stamp.x, sig.stamp.y, *self._element_size_pt(element_id))
        elif self._vignette_placed():
            vignette = self.profile.settings.vignette
            rect = (vignette.x, vignette.y, *self._element_size_pt(VIGNETTE_ID))
        else:
            rect = core.default_vignette_rect(pw, ph)
        left, top, w, h = core.pdf_rect_to_frame_rect(pw, ph, fw, fh, *rect)
        left, top = left + ox, top + oy
        box = (left, top, left + w, top + h)
        tags = ("element", f"el:{element_id}")    # (a bare all-digit id would read as an item id)
        # selected: solid red; others: thin blue dashes
        outline = ({"outline": "#c00", "width": 2} if selected
                   else {"outline": "#3B8ED0", "width": 1, "dash": (3, 2)})
        gray = {"outline": "#777", "width": 2 if selected else 1, "dash": (4, 3)}
        if sig is None:                           # vignette: a labelled box
            if self._vignette_placed():
                outline.pop("dash", None)         # placed -> solid outline
            else:
                outline = gray                    # default position -> gray dashes
            self.canvas.create_text(
                left + w / 2, top + h / 2, text=tr("place.el_vignette_short"),
                font=("", 9), fill="#555", tags=tags)
        elif not sig.enabled:
            outline = gray                        # selected but unticked: outline only
        else:
            content = self._content(sig)
            if content is None:                   # unusable: red box + warning sign
                outline = {"outline": "#c00", "width": 2 if selected else 1}
                self.canvas.create_text(left + w / 2, top + h / 2, text="⚠",
                                        fill="#c00", tags=tags)
            else:
                scaled = self._scaled_preview(sig, content[0],
                                              max(1, int(w)), max(1, int(h)))
                photo = ImageTk.PhotoImage(scaled, master=self.canvas)
                self._canvas_imgs.append(photo)   # Tk only keeps a name
                self.canvas.create_image(left, top, anchor="nw", image=photo,
                                         tags=tags)
        self.canvas.create_rectangle(*box, tags=tags, **outline)

    def _on_resize(self, event) -> None:
        # the canvas follows the window size, keeping the page proportions
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == self._last_win_size:
            return
        self._last_win_size = size
        if self.template_dims and _alive(self.canvas):
            self._draw_page()

    # ========================================================= step 7: run
    def _build_step_run(self, parent) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", pady=(12, 4), padx=8)
        mode = self.mode_var.get()
        lines = [
            tr("run.summary_docs", count=len(self.valid_paths)),
            tr("run.summary_mode", mode=tr(f"mode.{mode}")),
        ]
        if mode in ("beid", "azure"):
            lines.append(tr("run.summary_level", level=self.pades_level_var.get()))
        anchor = self._page_anchor()
        anchor_name = tr(f"anchor.{anchor}_page") if anchor else None

        def placed_phrase(page, x, y, all_pages=False) -> str:
            xy = {"x": f"{x:.0f}", "y": f"{y:.0f}"}
            if anchor:
                return tr("run.place_custom_anchor", anchor=anchor_name, **xy)
            if all_pages:
                return tr("run.place_all", **xy)
            return tr("run.place_custom", page=page, **xy)

        if mode in ("beid", "azure"):
            if self._vignette_placed():
                v = self.profile.settings.vignette
                place = placed_phrase(v.page, v.x, v.y)
            elif anchor:
                place = tr("run.place_default_anchor", anchor=anchor_name)
            else:
                place = tr("run.place_default")
            lines.append(tr("run.summary_place", place=place))
        enabled = self._enabled_signatures()
        if enabled:
            lines.append(tr("run.summary_stamps", count=len(enabled)))
            for sig in enabled:
                place = (placed_phrase(self._element_page(sig.id), sig.stamp.x,
                                       sig.stamp.y, sig.stamp.all_pages)
                         if self._sig_placed(sig) else tr("place.st_unplaced"))
                lines.append(tr("run.stamp_line", name=sig.label, place=place))
        if anchor:
            lines.append(tr(f"run.summary_anchor_{anchor}"))
        lines.append(tr("run.summary_output", output=self.output_dir))
        ctk.CTkLabel(box, text="\n".join(lines), justify="left", anchor="w",
                     wraplength=640,
                     font=ctk.CTkFont(size=13)).pack(anchor="w")
        note_key = {"beid": "run.pin_note", "azure": "run.azure_note"}.get(mode)
        if note_key:
            ctk.CTkLabel(box, text=tr(note_key), justify="left",
                         wraplength=620,
                         text_color=("gray25", "gray70"),
                         font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(6, 0))

        # eID: green reminder to insert the card BEFORE starting — the batch
        # opens the PKCS#11 session first thing and fails without a card.
        # Hidden once the batch starts; shown again if it fails to start.
        self.card_box = ctk.CTkFrame(parent, fg_color=_COL_CARD_BG,
                                     border_color=_COL_DONE, border_width=2,
                                     corner_radius=8)
        ctk.CTkLabel(self.card_box, text=tr("run.card_title"),
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_COL_CARD_FG, anchor="w"
                     ).pack(anchor="w", padx=14, pady=(8, 0))
        ctk.CTkLabel(self.card_box, text=tr("run.card_body"), justify="left",
                     anchor="w", wraplength=560, text_color=_COL_CARD_FG
                     ).pack(anchor="w", padx=14, pady=(2, 10))
        if mode == "beid" and self.run_results is None:
            self._show_card_box()

        self.launch_btn = ctk.CTkButton(parent, text=tr("run.start"),
                                        width=240, height=40,
                                        font=ctk.CTkFont(size=14, weight="bold"),
                                        fg_color="#2a7", hover_color="#196",
                                        command=self._launch)
        self.launch_btn.pack(anchor="w", padx=8, pady=(14, 4))
        self.progress = ctk.CTkProgressBar(parent, width=520)
        self.progress.set(0)
        self.progress.pack(anchor="w", padx=8, pady=(10, 2))
        self.run_status_lbl = ctk.CTkLabel(parent, text="", justify="left",
                                           anchor="w", wraplength=640)
        self.run_status_lbl.pack(anchor="w", padx=8, pady=2)
        if self.run_results is not None:           # returning after a batch
            ok = sum(1 for r in self.run_results if r.ok)
            self.progress.set(1)
            self.run_status_lbl.configure(
                text=tr("run.done", ok=ok, total=len(self.run_results)))
        elif self.run_error:
            self.run_status_lbl.configure(
                text=tr("run.error", error=self.run_error))

    def _show_card_box(self) -> None:
        box = getattr(self, "card_box", None)
        if not _alive(box) or box.winfo_manager():
            return
        launch = getattr(self, "launch_btn", None)
        kw = {"before": launch} if _alive(launch) else {}   # stays above Start
        box.pack(anchor="w", fill="x", padx=8, pady=(12, 0), **kw)

    def _launch(self) -> None:
        if self._running or not self.valid_paths or not self.output_dir:
            return
        # Visual signatures: every ENABLED library entry, in list order.
        # page/x/y/page_anchor carry the VIGNETTE placement
        # only (beid/azure; all None in image mode): a placed vignette, or
        # None -> default corner box (bottom-right, last page). With the
        # validation-step anchor active, every element is resolved PER
        # DOCUMENT on its first/last page instead of a fixed page — `page`
        # and `page_anchor` stay mutually exclusive.
        anchor = self._page_anchor()
        crypto = self._has_vignette()
        v = self.profile.settings.vignette if (crypto and self._vignette_placed()) else None
        stamp_list = [
            dataclasses.replace(s.stamp, page=None, all_pages=False, page_anchor=anchor)
            if anchor else s.stamp
            for s in self._enabled_signatures()
        ]
        cfg = core.RunConfig(
            inputs=list(self.valid_paths),
            output=self.output_dir,
            mode=self.mode_var.get(),
            template=self.template_path,
            pades_level=self.pades_level_var.get(),
            lib=self.default_lib,
            stamps=stamp_list,
            page=None if (anchor or v is None) else v.page,
            page_anchor=anchor if crypto else None,
            x=v.x if v else None,
            y=v.y if v else None,
            # azure settings (ignored by the other modes). The worker batch
            # reuses the credential cached by "Sign in with Microsoft"; if
            # the user skipped it, the login happens on the worker thread.
            azure_vault_url=self.azure_vault.strip() or None,
            azure_key_name=self.azure_key.strip() or None,
            azure_auth=self.azure_auth_var.get(),
            azure_trust_anchors=self.azure_anchors_path,
        )
        try:
            core.validate_config(cfg)
        except ValueError as exc:
            self.run_status_lbl.configure(text=tr("run.error", error=exc))
            return
        self._invalidate_run()
        self._running = True
        if _alive(getattr(self, "card_box", None)):
            self.card_box.pack_forget()               # the card is in use now
        self.launch_btn.configure(state="disabled")   # avoids concurrent batches
        self.progress.set(0)
        self.run_status_lbl.configure(text=tr("run.working"))
        self._refresh_chrome()                        # locks all navigation
        # Tkinter is not thread-safe: the worker writes ONLY to a queue,
        # and the main thread drains it via a periodic after().
        self._result_q = queue.Queue()
        threading.Thread(target=self._run_batch, args=(cfg,), daemon=True).start()
        self.after(100, self._poll_results)

    def _run_batch(self, cfg) -> None:
        # run off the main thread: NO Tk calls here, only the queue.
        try:
            results = core.process_batch(
                cfg, on_progress=lambda r: self._result_q.put(("row", r))
            )
            self._result_q.put(("done", results))
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            # open_eid_session() raises SystemExit ("no reader/card") —
            # SystemExit is NOT an Exception: without this case, the thread would
            # die silently and the GUI would stay stuck on "Processing…".
            self._result_q.put(("error", str(exc) or exc.__class__.__name__))

    def _poll_results(self) -> None:
        # main thread: drain the queue and update the widgets.
        if not _alive(self):              # window closed during processing
            return
        total = max(1, len(self.valid_paths))
        try:
            while True:
                kind, payload = self._result_q.get_nowait()
                if kind == "row":
                    self.run_rows.append(payload)
                    if _alive(getattr(self, "progress", None)):
                        self.progress.set(len(self.run_rows) / total)
                    if _alive(getattr(self, "run_status_lbl", None)):
                        self.run_status_lbl.configure(text=tr(
                            "run.progress", done=len(self.run_rows),
                            total=total, name=payload.path.name))
                elif kind == "done":
                    self.run_results = payload
                    self._running = False
                    if _alive(getattr(self, "launch_btn", None)):
                        self.launch_btn.configure(state="normal")
                    self._goto_step(_STEP_KEYS.index("results"))
                    return
                elif kind == "error":
                    self.run_error = payload
                    self._running = False
                    if _alive(getattr(self, "launch_btn", None)):
                        self.launch_btn.configure(state="normal")
                    if self.mode_var.get() == "beid":
                        self._show_card_box()         # e.g. "no card": retry
                    if _alive(getattr(self, "run_status_lbl", None)):
                        self.run_status_lbl.configure(
                            text=tr("run.error", error=payload))
                    self._refresh_chrome()
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_results)

    # ===================================================== step 8: results
    def _build_step_results(self, parent) -> None:
        results = self.run_results or []
        ok = sum(1 for r in results if r.ok)
        if self.run_error:
            headline = tr("run.error", error=self.run_error)
        elif ok == len(results):
            headline = tr("res.all_ok", total=len(results))
        else:
            headline = tr("res.partial", ok=ok, total=len(results),
                          fail=len(results) - ok)
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", pady=(12, 4), padx=8)
        ctk.CTkLabel(box, text=headline,
                     font=ctk.CTkFont(size=13, weight="bold"),
                     justify="left", anchor="w", wraplength=640).pack(anchor="w")
        ctk.CTkLabel(box, text=tr("run.summary_output", output=self.output_dir),
                     justify="left", anchor="w", wraplength=640
                     ).pack(anchor="w", pady=(2, 0))
        if self.mode_var.get() == "beid":
            ctk.CTkLabel(box, text=tr("res.rrn_note"), justify="left",
                         wraplength=620,
                         text_color=("gray25", "gray70"),
                         font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(6, 0))
        self.summary_table = self._make_table(
            parent,
            [(tr("res.col_doc"), 240), (tr("res.col_status"), 130),
             (tr("res.col_detail"), 360)],
            height=12,
        )
        self._fill_table(self.summary_table, [
            (r.path.name, tr("res.ok") if r.ok else tr("res.fail"), r.detail)
            for r in results])
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=(6, 4))
        self.open_folder_btn = ctk.CTkButton(
            row, text=tr("res.open_folder"), width=220,
            command=self._open_output_folder)
        self.open_folder_btn.pack(side="left")
        self.open_folder_lbl = ctk.CTkLabel(row, text="", anchor="w",
                                            justify="left", wraplength=400,
                                            text_color=("#b3261e", "#e08a8a"))
        self.open_folder_lbl.pack(side="left", padx=12)

    def _open_output_folder(self) -> None:
        """Show the signed files in the OS file manager (core helper); a
        failure is reported inline, never raised into the Tk loop."""
        if not self.output_dir:
            return
        try:
            core.open_in_file_manager(self.output_dir)
            msg = ""
        except Exception as exc:  # noqa: BLE001 - xdg-open missing, etc.
            msg = tr("res.open_folder_failed", error=exc)
        if _alive(getattr(self, "open_folder_lbl", None)):
            self.open_folder_lbl.configure(text=msg)


def launch_gui(args) -> int:
    """Entry point called by `sign_pdfs_beid.py --gui`."""
    ctk.set_appearance_mode("system")
    # Language: the user's saved choice, else the system's.
    store = profile_store.ProfileStore()
    lang = store.load().settings.language
    i18n.set_language(lang if lang in i18n.LANGUAGES else i18n.system_language())
    app = CachetApp(args, store=store)
    app.mainloop()
    return 0
