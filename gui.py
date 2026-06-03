#!/usr/bin/env python3
"""Interface graphique CustomTkinter pour signApp.

Ce module n'est importé QUE lorsqu'on lance `sign_pdfs_beid.py --gui` : il
dépend de tkinter/customtkinter, absents en mode CLI/headless. Toute la logique
métier (validation, insertion d'image, signature eID, calcul de placement) vit
dans `sign_pdfs_beid.py` et est testée sans tkinter ; la GUI n'en est qu'une
façade qui suit le workflow demandé :

    1. Choisir le PDF modèle.        5. Choisir le mode (eID | image).
    2. Choisir les fichiers.         6. Page + position (les deux modes ;
    3. Choisir le dossier de sortie.    image : aussi le choix de l'image).
    4. Valider les fichiers.         7. Lancer.  8. Récapitulatif par document.

Le cadre de sélection (étape 6) affiche le rendu réel de la page courante du
modèle et se redimensionne avec la fenêtre ; un clic y place la signature
(placeholder à l'échelle : image en mode image, cadre 3:1 large d'1/5 de page
en mode beid).
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk
from PIL import Image, ImageTk

import sign_pdfs_beid as core

_FRAME_MAX_W = 360   # taille max du cadre d'aperçu de page (px)
_FRAME_MAX_H = 460


class SignApp(ctk.CTk):
    """Fenêtre principale : tout le workflow dans une vue défilante."""

    def __init__(self, args):
        super().__init__()
        self.title("signApp — signature de PDF")
        self.geometry("900x900")
        _style = ttk.Style()
        _style.configure("Treeview", rowheight=30, font=("", 11))   # lignes hautes, texte complet
        _style.configure("Treeview.Heading", font=("", 11, "bold"))

        # --- état ---
        self.default_lib = getattr(args, "lib", None)
        self.template_path: Path | None = None
        self.template_dims: list[tuple[float, float]] = []
        self.input_paths: list[Path] = []
        self.valid_paths: list[Path] = []
        self.output_dir: Path | None = None
        self.mode_var = ctk.StringVar(value="beid")
        self.pades_var = ctk.BooleanVar(value=False)
        self.image_path: Path | None = None
        self.cur_page = 0                         # 0-based, pour l'aperçu
        self.place_page: int | None = None        # 1-based, position retenue
        self.place_x: float | None = None
        self.place_y: float | None = None
        self._canvas_img = None                   # réf PhotoImage du placeholder
        self._bg_img = None                       # réf PhotoImage du fond de page
        self._page_img_cache: dict = {}           # (modèle, page) -> PIL pleine résolution
        self._last_win_size = None

        root = ctk.CTkScrollableFrame(self)
        root.pack(fill="both", expand=True, padx=12, pady=12)
        self._build_steps(root)
        self._refresh_placement_section()
        self.bind("<Configure>", self._on_resize)  # le canvas grandit avec la fenêtre

    # ------------------------------------------------------------------ UI
    def _build_steps(self, root) -> None:
        def header(txt):
            lbl = ctk.CTkLabel(root, text=txt, font=ctk.CTkFont(size=15, weight="bold"))
            lbl.pack(anchor="w", pady=(12, 2))
            return lbl

        # 1. modèle
        header("1. PDF modèle")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choisir le modèle…", command=self._pick_template).pack(side="left")
        self.template_lbl = ctk.CTkLabel(row, text="(aucun)"); self.template_lbl.pack(side="left", padx=10)

        # 2. fichiers
        header("2. Fichiers à signer")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choisir les fichiers…", command=self._pick_inputs).pack(side="left")
        self.inputs_lbl = ctk.CTkLabel(row, text="(aucun)"); self.inputs_lbl.pack(side="left", padx=10)

        # 3. sortie
        header("3. Dossier de sortie")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkButton(row, text="Choisir le dossier…", command=self._pick_output).pack(side="left")
        self.output_lbl = ctk.CTkLabel(row, text="(aucun)"); self.output_lbl.pack(side="left", padx=10)

        # 4. validation
        header("4. Validation (nombre de pages + dimensions exactes)")
        ctk.CTkButton(root, text="Valider les fichiers", command=self._validate).pack(anchor="w")
        self.valid_table = self._make_table(root, ("Fichier", "Résultat", "Détail"), height=5)

        # 5. mode
        header("5. Mode de signature")
        row = ctk.CTkFrame(root, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkRadioButton(row, text="eID (carte + vignette)", variable=self.mode_var,
                           value="beid", command=self._refresh_placement_section).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(row, text="Insertion d'image", variable=self.mode_var,
                           value="image", command=self._refresh_placement_section).pack(side="left")
        ctk.CTkCheckBox(row, text="PAdES", variable=self.pades_var).pack(side="left", padx=16)

        # 6. page + position (LES DEUX modes ; le choix d'image n'apparaît qu'en image)
        self.image_section = ctk.CTkFrame(root)
        ctk.CTkLabel(self.image_section, text="6. Page + position",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(4, 2))
        self.image_row = ctk.CTkFrame(self.image_section, fg_color="transparent")
        self.image_row.pack(fill="x")
        ctk.CTkButton(self.image_row, text="Choisir l'image…", command=self._pick_image).pack(side="left")
        self.image_lbl = ctk.CTkLabel(self.image_row, text="(aucune)"); self.image_lbl.pack(side="left", padx=10)

        self._nav_row = ctk.CTkFrame(self.image_section, fg_color="transparent")
        self._nav_row.pack(fill="x", pady=4)
        ctk.CTkButton(self._nav_row, text="◀ Précédent", width=110,
                      command=lambda: self._turn_page(-1)).pack(side="left")
        self.page_lbl = ctk.CTkLabel(self._nav_row, text="page —/—"); self.page_lbl.pack(side="left", padx=10)
        ctk.CTkButton(self._nav_row, text="Suivant ▶", width=110,
                      command=lambda: self._turn_page(1)).pack(side="left")
        self.pos_lbl = ctk.CTkLabel(self._nav_row, text="position : (cliquez dans le cadre)")
        self.pos_lbl.pack(side="left", padx=16)

        # tkinter Canvas : fond = page rendue, clic = position. Grandit avec la fenêtre.
        import tkinter as tk
        self.canvas = tk.Canvas(self.image_section, width=_FRAME_MAX_W, height=_FRAME_MAX_H,
                                bg="#d9d9d9", highlightthickness=1, highlightbackground="#888")
        self.canvas.pack(pady=6, fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        # 7. lancer  (ancre : la section image se range JUSTE avant ce titre)
        self._after_image_anchor = header("7. Lancer")
        self.launch_btn = ctk.CTkButton(root, text="Lancer le traitement",
                                        command=self._launch, fg_color="#2a7", hover_color="#196")
        self.launch_btn.pack(anchor="w")
        self.status_lbl = ctk.CTkLabel(root, text=""); self.status_lbl.pack(anchor="w", pady=2)

        # 8. récapitulatif
        header("8. Récapitulatif")
        self.summary_table = self._make_table(root, ("Document", "Signé", "Détail"), height=8)

    def _make_table(self, parent, columns, height):
        table = ttk.Treeview(parent, columns=columns, show="headings", height=height)
        widths = {"Fichier": 220, "Document": 220, "Détail": 380}
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
        # Section visible dans LES DEUX modes (placement de la vignette OU de
        # l'image). Rangée AVANT « 7. Lancer » pour respecter l'ordre du workflow.
        self.image_section.pack(fill="both", expand=True, pady=6, before=self._after_image_anchor)
        if self.mode_var.get() == "image":
            self.image_row.pack(fill="x", before=self._nav_row)   # choix d'image
        else:
            self.image_row.pack_forget()                          # beid : pas d'image
        self._draw_page()

    # -------------------------------------------------------------- actions
    def _pick_template(self) -> None:
        path = filedialog.askopenfilename(title="PDF modèle", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        self.template_path = Path(path)
        self._page_img_cache.clear()              # nouveau modèle -> nouveaux rendus
        try:
            self.template_dims = core.page_dimensions(self.template_path)
        except Exception as exc:  # noqa: BLE001
            self.template_dims = []
            self.template_lbl.configure(text=f"illisible : {exc}")
            return
        self.cur_page = 0
        self.template_lbl.configure(
            text=f"{self.template_path.name}  ({len(self.template_dims)} pages)"
        )
        self._draw_page()

    def _pick_inputs(self) -> None:
        paths = filedialog.askopenfilenames(title="Fichiers à signer", filetypes=[("PDF", "*.pdf")])
        if not paths:
            return
        self.input_paths = [Path(p) for p in paths]
        self.inputs_lbl.configure(text=f"{len(self.input_paths)} fichier(s)")

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Dossier de sortie")
        if not path:
            return
        self.output_dir = Path(path)
        self.output_lbl.configure(text=str(self.output_dir))

    def _pick_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Image de signature", filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp")]
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
            self.status_lbl.configure(text="Choisis d'abord un modèle et des fichiers.")
            return
        for r in core.validate_files(self.template_path, self.input_paths):
            self.valid_table.insert("", "end",
                                    values=(r.path.name, "✓ OK" if r.ok else "✗ rejeté", r.reason or "—"))
            if r.ok:
                self.valid_paths.append(r.path)
        self.status_lbl.configure(
            text=f"{len(self.valid_paths)}/{len(self.input_paths)} fichier(s) valides."
        )

    def _turn_page(self, delta: int) -> None:
        if not self.template_dims:
            return
        self.cur_page = max(0, min(len(self.template_dims) - 1, self.cur_page + delta))
        self._draw_page()

    def _canvas_target_size(self) -> tuple[int, int]:
        """Taille cible du canvas, dérivée de la fenêtre (grandit/rétrécit avec elle)."""
        w = max(320, self.winfo_width() - 130)
        h = max(260, int(self.winfo_height() * 0.55))
        return w, h

    def _get_page_image(self, page_index):
        """Image (PIL) pleine résolution de la page du modèle, mise en cache."""
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
        pil = self._get_page_image(self.cur_page)   # fond = rendu réel de la page
        if pil is not None:
            self._bg_img = ImageTk.PhotoImage(
                pil.resize((max(1, int(fw)), max(1, int(fh))))
            )
            self.canvas.create_image(ox, oy, anchor="nw", image=self._bg_img)
            self.canvas.create_rectangle(ox, oy, ox + fw, oy + fh, outline="#333")
        else:                                       # rendu indisponible -> cadre blanc
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
        self.pos_lbl.configure(text=f"position : page {self.place_page} @ ({x:.0f}, {y:.0f}) pt")
        self._draw_page()

    def _placeholder_size_pt(self) -> tuple[float, float]:
        """Taille (pt) du placeholder : image réelle en mode image, sinon cadre
        vignette 3:1 large d'1/5 de la page courante (mode beid)."""
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
        # le canvas suit la taille de la fenêtre, en gardant les proportions de page
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
            self.status_lbl.configure(text="Fichiers (validés) et dossier de sortie requis.")
            return
        if self.mode_var.get() == "image" and (self.image_path is None or self.place_x is None):
            self.status_lbl.configure(
                text="Mode image : choisis une image puis clique dans le cadre pour la position."
            )
            return
        # beid : un clic place la vignette ; sans clic, vignette par défaut
        # (bas à droite, dernière page). On transmet donc place_* tels quels (None
        # si pas de clic) — process_batch en déduit le placement.
        cfg = core.RunConfig(
            inputs=files,
            output=self.output_dir,
            mode=self.mode_var.get(),
            template=self.template_path,
            pades=self.pades_var.get(),
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
        self.status_lbl.configure(text="Traitement en cours…")
        self.launch_btn.configure(state="disabled")   # évite les lots concurrents
        # Tkinter n'est pas thread-safe : le worker n'écrit QUE dans une queue,
        # et le thread principal la draine via un after() périodique.
        self._result_q = queue.Queue()
        threading.Thread(target=self._run_batch, args=(cfg,), daemon=True).start()
        self.after(100, self._poll_results)

    def _run_batch(self, cfg) -> None:
        # exécuté hors thread principal : AUCUN appel Tk ici, seulement la queue.
        try:
            results = core.process_batch(
                cfg, on_progress=lambda r: self._result_q.put(("row", r))
            )
            self._result_q.put(("done", results))
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            # open_eid_session() lève SystemExit (« pas de lecteur/carte ») —
            # SystemExit n'est PAS une Exception : sans ce cas, le thread mourrait
            # en silence et la GUI resterait figée sur « Traitement en cours… ».
            self._result_q.put(("error", str(exc) or exc.__class__.__name__))

    def _poll_results(self) -> None:
        # thread principal : draine la queue et met à jour les widgets.
        if not self.winfo_exists():       # fenêtre fermée pendant le traitement
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
                        text=f"Terminé : {ok}/{len(payload)} document(s) traité(s)."
                    )
                    self.launch_btn.configure(state="normal")
                    return
                elif kind == "error":
                    self.status_lbl.configure(text=f"Erreur : {payload}")
                    self.launch_btn.configure(state="normal")
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_results)


def launch_gui(args) -> int:
    """Point d'entrée appelé par `sign_pdfs_beid.py --gui`."""
    ctk.set_appearance_mode("system")
    app = SignApp(args)
    app.mainloop()
    return 0
