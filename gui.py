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
placement (page preview + click, an explicit target-page field and — when
page counts differ — the first/last-page selector mirrored from validation)
→ signing (summary, green "insert your eID card" reminder, progress) →
results report (+ "Open output folder").

Threading rule (do not regress): tkinter is NOT thread-safe. The batch runs
on a worker thread that never touches widgets — it only pushes onto a
`queue.Queue` drained by the main thread via a periodic `after()`. The worker
catches `SystemExit` too: `open_eid_session()` raises it (no reader/card) and
it is *not* an `Exception`."""

from __future__ import annotations

import os
import queue
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import filedialog, font as tkfont, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

import i18n
import sign_pdfs_beid as core
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


def _asset_path(name: str) -> Path:
    """Path of a bundled asset (e.g. the logo): next to this file in a
    checkout, under the PyInstaller extraction dir (``sys._MEIPASS``) in the
    frozen binary — cachet.spec ships it in ``gui_datas``."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


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

    def __init__(self, args):
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
        # (self.azure_vault / self.azure_key / self.page_text) and synced via
        # key bindings; a shared StringVar would fire dead-widget callbacks
        # after every step rebuild.
        self.mode_var = ctk.StringVar(value="beid")
        self.pades_level_var = ctk.StringVar(value="b-lta")
        self.azure_auth_var = ctk.StringVar(value="interactive")  # GUI default
        self._running = False                      # batch in progress
        self._azure_login_q: queue.Queue | None = None
        self._result_q: queue.Queue | None = None
        self._canvas_img = None                    # PhotoImage ref (placeholder)
        self._bg_img = None                        # PhotoImage ref (page render)
        self._page_img_cache: dict = {}            # (template, page) -> PIL image
        self._last_win_size = None
        self._stepper_btns: list[ctk.CTkButton] = []
        self.step_index = 0
        self.canvas = None

        self._reset_state()

        # Config edits invalidate a previous run + refresh the stepper.
        self.mode_var.trace_add("write", lambda *_: self._on_config_edit())
        self.pades_level_var.trace_add("write", lambda *_: self._on_config_edit())

        self._screen = ctk.CTkFrame(self, fg_color="transparent")
        self._screen.pack(fill="both", expand=True)
        self._show_landing()
        self.bind("<Configure>", self._on_resize)  # the canvas grows with the window

    # ------------------------------------------------------------- state
    def _apply_title(self) -> None:
        self.title(tr("app.title", version=core.__version__))

    def _reset_state(self) -> None:
        """Back to a blank wizard (fresh Start, Cancel, or Finish)."""
        self._close_docs_popup()                   # it belongs to the old wizard
        self.template_path: Path | None = None
        self.template_dims: list[tuple[float, float]] = []
        self.template_error: str | None = None
        self.input_paths: list[Path] = []
        self.validation_results = None             # list[ValidationResult] | None
        self.valid_paths: list[Path] = []
        self.output_dir: Path | None = None
        self.image_path: Path | None = None
        self.cur_page = 0                          # 0-based, preview only
        self.place_page: int | None = None         # 1-based target page
        self.place_x: float | None = None
        self.place_y: float | None = None
        self.page_text = ""                        # target-page field content
        # Page anchor (validation step): when some files' page count differs
        # from the template, `anchor_choice` decides whether EVERY file is
        # signed on its own first or last page (default: last, matching the
        # historical vignette position). Inactive while all counts match.
        self.anchor_choice = "last"
        self.count_mismatch = False
        self.azure_vault = os.environ.get(
            core.ENV_AZURE_VAULT_URL, "https://login.live.com")
        self.azure_key = os.environ.get(core.ENV_AZURE_KEY_NAME, "")
        self.azure_anchors_path: Path | None = (
            Path(p) if (p := os.environ.get(core.ENV_AZURE_TRUST_ANCHORS)) else None
        )
        self.azure_user_upn: str | None = None
        self.run_rows: list = []                   # DocResult, streamed
        self.run_results: list | None = None       # DocResult list once done
        self.run_error: str | None = None          # batch-level failure
        self._page_img_cache.clear()
        self.step_index = 0
        self.mode_var.set("beid")
        self.pades_level_var.set("b-lta")
        self.azure_auth_var.set("interactive")

    def _invalidate_run(self) -> None:
        self.run_rows = []
        self.run_results = None
        self.run_error = None

    def _on_config_edit(self) -> None:
        if self._running:
            return
        self._invalidate_run()
        self._refresh_chrome()

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

    def _on_language_change(self, display_name: str) -> None:
        code = self._language_code(display_name)
        if code:
            i18n.set_language(code)
        self._apply_title()
        self._close_docs_popup()                   # its text is language-bound
        self._show_landing()   # re-render the landing texts

    def _on_wizard_language_change(self, display_name: str) -> None:
        """Language switch from inside the wizard. Widgets are disposable and
        all state lives on the app, so the chrome and the current step are
        simply rebuilt in place — nothing the user entered is lost. Ignored
        while a batch runs (the menu is disabled then anyway)."""
        code = self._language_code(display_name)
        if self._running or code is None:
            return
        if code != i18n.get_language():
            i18n.set_language(code)
            self._apply_title()
            self._close_docs_popup()            # its text is language-bound
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
        win = ctk.CTkToplevel(self)
        win.title(tr("cancel.title"))
        win.geometry("480x190")
        win.transient(self)
        win.resizable(False, False)
        ctk.CTkLabel(win, text=tr("cancel.title"),
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(win, text=tr("cancel.body"), justify="left",
                     wraplength=440).pack(anchor="w", padx=18)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(side="bottom", fill="x", padx=18, pady=14)

        def do_cancel():
            win.destroy()
            self._reset_state()
            self._show_landing()

        ctk.CTkButton(row, text=tr("cancel.confirm"), width=150,
                      fg_color=_COL_ERROR, hover_color=("#c96a6a", "#8a4444"),
                      command=do_cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(row, text=tr("cancel.keep"), width=150,
                      fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"),
                      command=win.destroy).pack(side="right")
        win.after(80, lambda: _alive(win) and win.grab_set())

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
        if self._page_anchor():
            return None            # field disabled: the anchor fixes the page
        raw = self.page_text.strip()
        if raw and (not raw.isdigit() or int(raw) < 1):
            return tr("place.page_invalid")
        return None

    def _place_error(self) -> str | None:
        err = self._page_entry_error()
        if err:
            return err
        if self.mode_var.get() == "image" and (
                self.image_path is None or self.place_x is None):
            return tr("place.image_missing")
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
        """ttk table; ``columns`` = [(localized heading, width px), …]."""
        names = [c[0] for c in columns]
        table = ttk.Treeview(parent, columns=names, show="headings", height=height)
        for name, width in columns:
            table.heading(name, text=name)
            table.column(name, width=width, anchor="w")
        table.pack(fill="x", pady=4, padx=8)
        return table

    @staticmethod
    def _clear(table) -> None:
        for item in table.get_children():
            table.delete(item)

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
        # A new template invalidates validation, placement and results.
        self.validation_results = None
        self.valid_paths = []
        self.cur_page = 0
        self._reset_position()
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
        the signature (first/last) while the anchor selector applies; a
        position clicked on another page no longer matches, so it is dropped.
        The manual target page is cleared too — `page` and `page_anchor` are
        mutually exclusive."""
        anchor = self._page_anchor()
        if not anchor or not self.template_dims:
            return
        target = 0 if anchor == "first" else len(self.template_dims) - 1
        self.cur_page = target
        if self.place_page is not None and self.place_page != target + 1:
            self.place_page = self.place_x = self.place_y = None
        self._set_page_entry("")
        self._update_place_labels()
        self._draw_page()

    def _update_validation_widgets(self) -> None:
        if _alive(getattr(self, "valid_table", None)):
            self._clear(self.valid_table)
            for r in self.validation_results or []:
                self.valid_table.insert(
                    "", "end",
                    values=(r.path.name,
                            tr("val.ok") if r.ok else tr("val.rejected"),
                            r.reason or "—"))
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
            self.azure_vault = self.azure_vault_entry.get()
        if _alive(getattr(self, "azure_key_entry", None)):
            self.azure_key = self.azure_key_entry.get()
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

        self.image_row = ctk.CTkFrame(parent, fg_color="transparent")
        if self.mode_var.get() == "image":        # image picker: image mode only
            self.image_row.pack(fill="x", pady=(10, 0), padx=8)
        ctk.CTkButton(self.image_row, text=tr("place.image_choose"), width=200,
                      command=self._pick_image).pack(side="left")
        self.image_lbl = ctk.CTkLabel(
            self.image_row,
            text=self.image_path.name if self.image_path else tr("common.none"))
        self.image_lbl.pack(side="left", padx=12)

        nav = ctk.CTkFrame(parent, fg_color="transparent")
        nav.pack(fill="x", pady=6, padx=8)
        ctk.CTkLabel(nav, text=tr("place.page")).pack(side="left")
        self.page_entry = ctk.CTkEntry(nav, width=64, justify="center")
        self.page_entry.insert(0, self.page_text)
        self.page_entry.bind("<KeyRelease>", lambda _e: self._on_page_entry_change())
        self.page_entry.pack(side="left", padx=(6, 16))
        # packed before the page buttons so it keeps its space when the
        # locked-preview label makes the row tight
        ctk.CTkButton(nav, text=tr("place.reset"), width=150,
                      fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"),
                      command=self._reset_position).pack(side="right")
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

        info = ctk.CTkFrame(parent, fg_color="transparent")
        info.pack(fill="x", padx=8)
        self.pos_lbl = ctk.CTkLabel(info, text="", anchor="w", justify="left",
                                    wraplength=380)
        self.pos_lbl.pack(side="left")
        self.place_warn_lbl = ctk.CTkLabel(info, text="", anchor="w", justify="left",
                                           wraplength=300,
                                           text_color=("#b3261e", "#e08a8a"))
        self.place_warn_lbl.pack(side="left", padx=16)

        # tkinter Canvas: background = rendered template page, click = position.
        import tkinter as tk
        self.canvas = tk.Canvas(parent, width=_FRAME_MAX_W, height=_FRAME_MAX_H,
                                bg="#d9d9d9", highlightthickness=1,
                                highlightbackground="#888")
        self.canvas.pack(pady=6, padx=8)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
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

    def _pick_image(self) -> None:
        path = filedialog.askopenfilename(
            title=tr("place.image_choose"),
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp")])
        if not path:
            return
        self.image_path = Path(path)
        if _alive(getattr(self, "image_lbl", None)):
            self.image_lbl.configure(text=self.image_path.name)
        self._invalidate_run()
        self._update_place_labels()
        self._draw_page()
        self._refresh_chrome()

    def _set_page_entry(self, text: str) -> None:
        """Programmatic update of the target-page field (click, reset)."""
        self.page_text = text
        if _alive(getattr(self, "page_entry", None)):
            self.page_entry.delete(0, "end")
            self.page_entry.insert(0, text)

    def _reset_position(self) -> None:
        self.place_page = self.place_x = self.place_y = None
        self._set_page_entry("")
        self._invalidate_run()
        self._update_place_labels()
        self._draw_page()
        self._refresh_chrome()

    def _on_page_entry_change(self) -> None:
        """Manual target-page field: lets the signature go to a page other
        than the previewed one (e.g. documents whose page of interest differs
        from the template). Follows the preview when in range; warns when
        beyond the template."""
        if self._running:
            return
        if _alive(getattr(self, "page_entry", None)):
            self.page_text = self.page_entry.get()
        raw = self.page_text.strip()
        if raw.isdigit() and int(raw) >= 1:
            v = int(raw)
            if self.place_x is not None:
                self.place_page = v
            if self.template_dims and 1 <= v <= len(self.template_dims) \
                    and self.cur_page != v - 1:
                self.cur_page = v - 1
                self._draw_page()
        self._invalidate_run()
        self._update_place_labels()
        self._refresh_chrome()

    def _update_place_labels(self) -> None:
        anchor = self._page_anchor()
        if _alive(getattr(self, "pos_lbl", None)):
            if self.place_x is not None:
                if anchor:
                    text = tr("place.pos_anchor",
                              anchor=tr(f"anchor.{anchor}_page"),
                              x=f"{self.place_x:.0f}", y=f"{self.place_y:.0f}")
                else:
                    text = tr("place.pos", page=self.place_page,
                              x=f"{self.place_x:.0f}", y=f"{self.place_y:.0f}")
            elif self.mode_var.get() in ("beid", "azure"):
                text = (tr("place.pos_default_anchor",
                           anchor=tr(f"anchor.{anchor}_page"))
                        if anchor else tr("place.pos_default"))
            else:
                text = tr("place.pos_none")
            self.pos_lbl.configure(text=text)
        if _alive(getattr(self, "place_warn_lbl", None)):
            warn = self._page_entry_error() or ""
            if not warn and not anchor and self.place_page and self.template_dims \
                    and self.place_page > len(self.template_dims):
                warn = tr("place.page_beyond", page=self.place_page,
                          total=len(self.template_dims))
            if not warn and self.mode_var.get() == "image" and (
                    self.image_path is None or self.place_x is None):
                warn = tr("place.image_missing")
            self.place_warn_lbl.configure(text=warn)

    def _turn_page(self, delta: int) -> None:
        if not self.template_dims or self._page_anchor():
            return  # anchored: the preview is locked on the anchor page
        self.cur_page = max(0, min(len(self.template_dims) - 1, self.cur_page + delta))
        self._draw_page()

    def _canvas_target_size(self) -> tuple[int, int]:
        """Target canvas size, derived from the window (grows/shrinks with it)."""
        w = max(320, self.winfo_width() - _HELP_PANEL_W - 160)
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
        if self.place_page == self.cur_page + 1 and self.place_x is not None:
            self._draw_placeholder(self.place_x, self.place_y)

    def _on_canvas_click(self, event) -> None:
        if not self.template_dims or not hasattr(self, "_frame_geom"):
            return
        fw, fh, ox, oy = self._frame_geom
        cx, cy = event.x - ox, event.y - oy
        if not (0 <= cx <= fw and 0 <= cy <= fh):
            return
        pw, ph = self.template_dims[self.cur_page]
        x, y = core.frame_click_to_pdf_xy(pw, ph, fw, fh, cx, cy)
        self.place_page, self.place_x, self.place_y = self.cur_page + 1, x, y
        if self._page_anchor() is None:
            self._set_page_entry(str(self.place_page))  # click -> target page
        self._invalidate_run()
        self._update_place_labels()
        self._draw_page()
        self._refresh_chrome()

    def _placeholder_size_pt(self) -> tuple[float, float]:
        """Placeholder size (pt): actual image in image mode, otherwise a 3:1
        vignette box of width page/5 of the current page (beid/azure)."""
        pw = self.template_dims[self.cur_page][0]
        if self.mode_var.get() == "image" and self.image_path:
            return core.image_size_pt(self.image_path)
        return core.vignette_size_pt(pw)

    def _draw_placeholder(self, x, y) -> None:
        fw, fh, ox, oy = self._frame_geom
        pw, ph = self.template_dims[self.cur_page]
        iw, ih = self._placeholder_size_pt()
        left, top, w, h = core.pdf_rect_to_frame_rect(pw, ph, fw, fh, x, y, iw, ih)
        left, top = left + ox, top + oy
        if self.mode_var.get() == "image" and self.image_path:
            try:
                im = Image.open(self.image_path).convert("RGBA")
                im = im.resize((max(1, int(w)), max(1, int(h))))
                self._canvas_img = ImageTk.PhotoImage(im)
                self.canvas.create_image(left, top, anchor="nw", image=self._canvas_img)
            except Exception:  # noqa: BLE001
                pass
        self.canvas.create_rectangle(left, top, left + w, top + h,
                                     outline="#c00", width=2)

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
        if self.place_x is not None:
            if anchor:
                place = tr("run.place_custom_anchor",
                           anchor=tr(f"anchor.{anchor}_page"),
                           x=f"{self.place_x:.0f}", y=f"{self.place_y:.0f}")
            else:
                place = tr("run.place_custom", page=self.place_page,
                           x=f"{self.place_x:.0f}", y=f"{self.place_y:.0f}")
        elif anchor:
            place = tr("run.place_default_anchor",
                       anchor=tr(f"anchor.{anchor}_page"))
        else:
            place = tr("run.place_default")
        lines.append(tr("run.summary_place", place=place))
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
        # beid/azure: a click places the vignette; without a click, default
        # placement (bottom-right, last page). place_* are None in that case
        # and process_batch derives the placement from them. With the
        # validation-step anchor active, the page is resolved PER DOCUMENT
        # (first/last) instead of the fixed clicked page — page and
        # page_anchor are mutually exclusive.
        anchor = self._page_anchor()
        cfg = core.RunConfig(
            inputs=list(self.valid_paths),
            output=self.output_dir,
            mode=self.mode_var.get(),
            template=self.template_path,
            pades_level=self.pades_level_var.get(),
            lib=self.default_lib,
            image_path=self.image_path,
            page=None if anchor else self.place_page,
            page_anchor=anchor,
            x=self.place_x,
            y=self.place_y,
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
        for r in results:
            self.summary_table.insert(
                "", "end",
                values=(r.path.name, tr("res.ok") if r.ok else tr("res.fail"),
                        r.detail))
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
    i18n.set_language(i18n.system_language())
    app = CachetApp(args)
    app.mainloop()
    return 0
