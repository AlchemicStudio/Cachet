#!/usr/bin/env python3
"""CustomTkinter graphical interface for signApp.

This module is imported ONLY when running `sign_pdfs_beid.py --gui`: it
depends on tkinter/customtkinter, which are absent in CLI/headless mode. All the
business logic (validation, image insertion, eID signing, placement math) lives
in `sign_pdfs_beid.py` and is tested without tkinter; the GUI is merely a
façade that follows the requested workflow:

    1. Choose the template PDF.      5. Choose the mode (eID | image).
    2. Choose the files.             6. Page + position (both modes;
    3. Choose the output folder.        image: also the image choice).
    4. Validate the files.           7. Run.  8. Per-document summary.

The selection frame (step 6) shows the actual rendering of the template's
current page and resizes with the window; a click there places the signature
(scaled placeholder: image in image mode, 3:1 box of width page/5 in beid
mode).
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

import sign_pdfs_beid as core

_FRAME_MAX_W = 360   # max size of the page preview frame (px)
_FRAME_MAX_H = 460


class SignApp(ctk.CTk):
    """Main window: the whole workflow in a scrollable view."""

    def __init__(self, args):
        super().__init__()
        self.title("signApp — PDF signing")
        self.geometry("900x900")
        _style = ttk.Style()
        _style.configure("Treeview", rowheight=30, font=("", 11))   # tall rows, full text
        _style.configure("Treeview.Heading", font=("", 11, "bold"))

        # --- state ---
        self.default_lib = getattr(args, "lib", None)
        self.template_path: Path | None = None
        self.template_dims: list[tuple[float, float]] = []
        self.input_paths: list[Path] = []
        self.valid_paths: list[Path] = []
        self.output_dir: Path | None = None
        self.mode_var = ctk.StringVar(value="beid")
        self.pades_level_var = ctk.StringVar(value="b-lta")  # PAdES level (beid mode)
        self.image_path: Path | None = None
        self.cur_page = 0                         # 0-based, for the preview
        self.place_page: int | None = None        # 1-based, chosen position
        self.place_x: float | None = None
        self.place_y: float | None = None
        self._canvas_img = None                   # PhotoImage ref of the placeholder
        self._bg_img = None                       # PhotoImage ref of the page background
        self._page_img_cache: dict = {}           # (template, page) -> full-resolution PIL
        self._last_win_size = None

        root = ctk.CTkScrollableFrame(self)
        root.pack(fill="both", expand=True, padx=12, pady=12)
        self._build_steps(root)
        self._refresh_placement_section()
        self.bind("<Configure>", self._on_resize)  # the canvas grows with the window

    # ------------------------------------------------------------------ UI
    def _build_steps(self, root) -> None:
        def header(txt):
            lbl = ctk.CTkLabel(root, text=txt, font=ctk.CTkFont(size=15, weight="bold"))
            lbl.pack(anchor="w", pady=(12, 2))
            return lbl

        # 1. template
        header("1. Template PDF")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choose template…", command=self._pick_template).pack(side="left")
        self.template_lbl = ctk.CTkLabel(row, text="(none)"); self.template_lbl.pack(side="left", padx=10)

        # 2. files
        header("2. Files to sign")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choose files…", command=self._pick_inputs).pack(side="left")
        self.inputs_lbl = ctk.CTkLabel(row, text="(none)"); self.inputs_lbl.pack(side="left", padx=10)

        # 3. output
        header("3. Output folder")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choose folder…", command=self._pick_output).pack(side="left")
        self.output_lbl = ctk.CTkLabel(row, text="(none)"); self.output_lbl.pack(side="left", padx=10)

        # 4. validation
        header("4. Validation (page count + exact dimensions)")
        ctk.CTkButton(root, text="Validate files", command=self._validate).pack(anchor="w")
        self.valid_table = self._make_table(root, ("File", "Result", "Detail"), height=5)

        # 5. mode
        header("5. Signing mode")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkRadioButton(row, text="eID (card + vignette)", variable=self.mode_var,
                           value="beid", command=self._refresh_placement_section).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(row, text="Image insertion", variable=self.mode_var,
                           value="image", command=self._refresh_placement_section).pack(side="left")
        ctk.CTkLabel(row, text="PAdES level:").pack(side="left", padx=(16, 4))
        ctk.CTkOptionMenu(row, variable=self.pades_level_var,
                          values=list(core.PADES_LEVELS), width=110).pack(side="left")
        # R9: surface the RRN privacy implication of eID signatures.
        ctk.CTkLabel(root, text="⚠ eID signatures embed the signer's national "
                                "register number (RRN) — mind PDF distribution.",
                     text_color=("gray25", "gray70")).pack(anchor="w")

        # 6. page + position (BOTH modes; the image choice appears only in image mode)
        self.image_section = ctk.CTkFrame(root)
        ctk.CTkLabel(self.image_section, text="6. Page + position",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.image_row = ctk.CTkFrame(self.image_section, fg_color="transparent")
        self.image_row.pack(fill="x")
        ctk.CTkButton(self.image_row, text="Choose image…", command=self._pick_image).pack(side="left")
        self.image_lbl = ctk.CTkLabel(self.image_row, text="(none)"); self.image_lbl.pack(side="left", padx=10)

        self._nav_row = ctk.CTkFrame(self.image_section, fg_color="transparent")
        self._nav_row.pack(fill="x", pady=4)
        ctk.CTkButton(self._nav_row, text="◀ Previous", width=110,
                      command=lambda: self._turn_page(-1)).pack(side="left")
        self.page_lbl = ctk.CTkLabel(self._nav_row, text="page —/—"); self.page_lbl.pack(side="left", padx=10)
        ctk.CTkButton(self._nav_row, text="Next ▶", width=110,
                      command=lambda: self._turn_page(1)).pack(side="left")
        self.pos_lbl = ctk.CTkLabel(self._nav_row, text="position: (click in the frame)")
        self.pos_lbl.pack(side="left", padx=16)

        # tkinter Canvas: background = rendered page, click = position. Grows with the window.
        import tkinter as tk
        self.canvas = tk.Canvas(self.image_section, width=_FRAME_MAX_W, height=_FRAME_MAX_H,
                                bg="#d9d9d9", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(pady=6, fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        # 7. run  (anchor: the image section is placed JUST before this header)
        self._after_image_anchor = header("7. Run")
        self.launch_btn = ctk.CTkButton(root, text="Run",
                                        command=self._launch, fg_color="#2a7", hover_color="#196")
        self.launch_btn.pack(anchor="w")
        self.status_lbl = ctk.CTkLabel(root, text=""); self.status_lbl.pack(anchor="w", pady=2)

        # 8. summary
        header("8. Summary")
        self.summary_table = self._make_table(root, ("Document", "Signed", "Detail"), height=8)

    def _make_table(self, parent, columns, height):
        table = ttk.Treeview(parent, columns=columns, show="headings", height=height)
        widths = {"File": 220, "Document": 220, "Detail": 380}
        for c in columns:
            table.heading(c, text=c)
            table.column(c, width=widths.get(c, 90), anchor="w")
        table.pack(fill="x", pady=4)
        return table

    @staticmethod
    def _clear(table):
        for item in table.get_children():
            table.delete(item)

    def _refresh_placement_section(self) -> None:
        # Section visible in BOTH modes (placement of the vignette OR the
        # image). Placed BEFORE "7. Run" to respect the workflow order.
        self.image_section.pack(fill="both", expand=True, pady=6, before=self._after_image_anchor)
        if self.mode_var.get() == "image":
            self.image_row.pack(fill="x", before=self._nav_row)   # image choice
        else:
            self.image_row.pack_forget()                          # beid: no image
        self._draw_page()

    # -------------------------------------------------------------- actions
    def _pick_template(self) -> None:
        path = filedialog.askopenfilename(title="Template PDF", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.template_path = Path(path)
        self._page_img_cache.clear()              # new template -> new renders
        try:
            self.template_dims = core.page_dimensions(self.template_path)
        except Exception as exc:  # noqa: BLE001
            self.template_dims = []
            self.template_lbl.configure(text=f"unreadable: {exc}")
            return
        self.cur_page = 0
        self.template_lbl.configure(
            text=f"{self.template_path.name}  ({len(self.template_dims)} pages)"
        )
        self._draw_page()

    def _pick_inputs(self) -> None:
        paths = filedialog.askopenfilenames(title="Files to sign", filetypes=[("PDF", "*.pdf")])
        if not paths:
            return
        self.input_paths = [Path(p) for p in paths]
        self.inputs_lbl.configure(text=f"{len(self.input_paths)} file(s)")

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Output folder")
        if not path:
            return
        self.output_dir = Path(path)
        self.output_lbl.configure(text=str(self.output_dir))

    def _pick_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Signature image", filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp")]
        )
        if not path:
            return
        self.image_path = Path(path)
        self.image_lbl.configure(text=self.image_path.name)
        self._draw_page()

    def _validate(self) -> None:
        self._clear(self.valid_table)
        self.valid_paths = []
        if not self.template_path or not self.input_paths:
            self.status_lbl.configure(text="Choose a template and files first.")
            return
        for r in core.validate_files(self.template_path, self.input_paths):
            self.valid_table.insert("", "end",
                                    values=(r.path.name, "✓ OK" if r.ok else "✗ rejected", r.reason or "—"))
            if r.ok:
                self.valid_paths.append(r.path)
        self.status_lbl.configure(
            text=f"{len(self.valid_paths)}/{len(self.input_paths)} valid file(s)."
        )

    def _turn_page(self, delta: int) -> None:
        if not self.template_dims:
            return
        self.cur_page = max(0, min(len(self.template_dims) - 1, self.cur_page + delta))
        self._draw_page()

    def _canvas_target_size(self) -> tuple[int, int]:
        """Target canvas size, derived from the window (grows/shrinks with it)."""
        w = max(320, self.winfo_width() - 130)
        h = max(260, int(self.winfo_height() * 0.55))
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
        if not hasattr(self, "canvas") or not self.template_dims:
            return
        cw, ch = self._canvas_target_size()
        self.canvas.configure(width=cw, height=ch)
        self.canvas.delete("all")
        pw, ph = self.template_dims[self.cur_page]
        fw, fh = core.fit_frame(pw, ph, cw, ch)
        ox, oy = (cw - fw) / 2, (ch - fh) / 2
        pil = self._get_page_image(self.cur_page)   # background = actual page rendering
        if pil is not None:
            self._bg_img = ImageTk.PhotoImage(
                pil.resize((max(1, int(fw)), max(1, int(fh))))
            )
            self.canvas.create_image(ox, oy, anchor="nw", image=self._bg_img)
            self.canvas.create_rectangle(ox, oy, ox + fw, oy + fh, outline="#333")
        else:                                       # render unavailable -> blank frame
            self.canvas.create_rectangle(ox, oy, ox + fw, oy + fh, fill="white", outline="#333")
        self.page_lbl.configure(text=f"page {self.cur_page + 1}/{len(self.template_dims)}")
        self._frame_geom = (fw, fh, ox, oy)
        if self.place_page == self.cur_page + 1 and self.place_x is not None:
            self._draw_placeholder(self.place_x, self.place_y)

    def _on_canvas_click(self, event) -> None:
        if self.mode_var.get() not in ("image", "beid") or not self.template_dims:
            return
        if not hasattr(self, "_frame_geom"):
            return
        fw, fh, ox, oy = self._frame_geom
        cx, cy = event.x - ox, event.y - oy
        if not (0 <= cx <= fw and 0 <= cy <= fh):
            return
        pw, ph = self.template_dims[self.cur_page]
        x, y = core.frame_click_to_pdf_xy(pw, ph, fw, fh, cx, cy)
        self.place_page, self.place_x, self.place_y = self.cur_page + 1, x, y
        self.pos_lbl.configure(text=f"position: page {self.place_page} @ ({x:.0f}, {y:.0f}) pt")
        self._draw_page()

    def _placeholder_size_pt(self) -> tuple[float, float]:
        """Placeholder size (pt): actual image in image mode, otherwise a 3:1
        vignette box of width page/5 of the current page (beid mode)."""
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
        self.canvas.create_rectangle(left, top, left + w, top + h, outline="#c00", width=2)

    def _on_resize(self, event) -> None:
        # the canvas follows the window size, keeping the page proportions
        if event.widget is not self:
            return
        size = (event.width, event.height)
        if size == self._last_win_size:
            return
        self._last_win_size = size
        if self.template_dims:
            self._draw_page()

    # --------------------------------------------------------------- launch
    def _launch(self) -> None:
        files = self.valid_paths or self.input_paths
        if not files or not self.output_dir:
            self.status_lbl.configure(text="Validated files and an output folder are required.")
            return
        if self.mode_var.get() == "image" and (self.image_path is None or self.place_x is None):
            self.status_lbl.configure(
                text="Image mode: choose an image, then click in the frame to set the position."
            )
            return
        # beid: a click places the vignette; without a click, default vignette
        # (bottom-right, last page). So we pass place_* as-is (None if no
        # click) — process_batch derives the placement from it.
        cfg = core.RunConfig(
            inputs=files,
            output=self.output_dir,
            mode=self.mode_var.get(),
            template=self.template_path,
            pades_level=self.pades_level_var.get(),
            lib=self.default_lib,
            image_path=self.image_path,
            page=self.place_page,
            x=self.place_x,
            y=self.place_y,
        )
        try:
            core.validate_config(cfg)
        except ValueError as exc:
            self.status_lbl.configure(text=str(exc))
            return
        self._clear(self.summary_table)
        self.status_lbl.configure(text="Processing…")
        self.launch_btn.configure(state="disabled")   # avoids concurrent batches
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
        if not self.winfo_exists():       # window closed during processing
            return
        try:
            while True:
                kind, payload = self._result_q.get_nowait()
                if kind == "row":
                    self.summary_table.insert(
                        "", "end",
                        values=(payload.path.name, "✓" if payload.ok else "✗", payload.detail),
                    )
                elif kind == "done":
                    ok = sum(1 for r in payload if r.ok)
                    self.status_lbl.configure(
                        text=f"Done: {ok}/{len(payload)} document(s) processed."
                    )
                    self.launch_btn.configure(state="normal")
                    return
                elif kind == "error":
                    self.status_lbl.configure(text=f"Error: {payload}")
                    self.launch_btn.configure(state="normal")
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_results)


def launch_gui(args) -> int:
    """Entry point called by `sign_pdfs_beid.py --gui`."""
    ctk.set_appearance_mode("system")
    app = SignApp(args)
    app.mainloop()
    return 0
