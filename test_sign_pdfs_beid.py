#!/usr/bin/env python3
"""Tests headless (sans carte, sans tkinter) pour signApp.

Couvre la logique métier ajoutée : extraction des dimensions de page, validation
face au modèle, insertion d'image, calcul de placement et résolution des
arguments CLI. Lancer avec :  ./venv/bin/python -m unittest -v
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import sign_pdfs_beid as core


def make_pdf(path, page_sizes) -> Path:
    """Écrit un PDF minimal mais valide (xref correct) avec les pages données,
    chacune décrite par un (largeur, hauteur) en points. Chaque page a un flux
    /Contents (vide) — requis par pyHanko pour tamponner."""
    n = len(page_sizes)
    objs = [b"<</Type/Catalog/Pages 2 0 R>>"]                      # obj 1
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(f"<</Type/Pages/Kids[{kids}]/Count {n}>>".encode())  # obj 2
    content = b"q Q\n"
    for i, (w, h) in enumerate(page_sizes):
        objs.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {w} {h}]"
            f"/Contents {4 + 2 * i} 0 R>>".encode()
        )
        objs.append(b"<</Length %d>>\nstream\n" % len(content) + content + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    size = len(objs) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {size}/Root 1 0 R>>\nstartxref\n{xref_pos}\n%%EOF".encode()
    Path(path).write_bytes(out)
    return Path(path)


def make_png(path, size=(300, 120)) -> Path:
    Image.new("RGBA", size, (255, 0, 0, 128)).save(path)
    return Path(path)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def p(self, name):
        return self.tmp / name


class PageGeometry(TmpCase):
    def test_dimensions_in_order(self):
        pdf = make_pdf(self.p("a.pdf"), [(200, 300), (400, 500), (612, 792)])
        self.assertEqual(core.page_dimensions(pdf), [(200.0, 300.0), (400.0, 500.0), (612.0, 792.0)])


class Validation(TmpCase):
    def setUp(self):
        super().setUp()
        self.tpl = make_pdf(self.p("tpl.pdf"), [(595, 842), (595, 842)])

    def test_identical_passes(self):
        same = make_pdf(self.p("same.pdf"), [(595, 842), (595, 842)])
        dims = core.page_dimensions(self.tpl)
        self.assertTrue(core.validate_against_template(dims, same).ok)

    def test_page_count_mismatch_rejected(self):
        fewer = make_pdf(self.p("fewer.pdf"), [(595, 842)])
        r = core.validate_against_template(core.page_dimensions(self.tpl), fewer)
        self.assertFalse(r.ok)
        self.assertIn("page", r.reason)

    def test_dimension_mismatch_rejected_no_tolerance(self):
        # même nombre de pages, mais 1 pt d'écart sur la 2e page → rejet
        off = make_pdf(self.p("off.pdf"), [(595, 842), (595, 843)])
        r = core.validate_against_template(core.page_dimensions(self.tpl), off)
        self.assertFalse(r.ok)
        self.assertIn("page 2", r.reason)

    def test_validate_files_batch(self):
        good = make_pdf(self.p("good.pdf"), [(595, 842), (595, 842)])
        bad = make_pdf(self.p("bad.pdf"), [(595, 842)])
        results = core.validate_files(self.tpl, [good, bad])
        self.assertEqual([r.ok for r in results], [True, False])


class ImageInsertion(TmpCase):
    def test_insert_preserves_pages_and_writes_output(self):
        src = make_pdf(self.p("src.pdf"), [(400, 600), (400, 600)])
        png = make_png(self.p("sig.png"))
        dst = self.p("out.pdf")
        core.insert_image_one(src, dst, png, page_index=1, x=50, y=60)
        self.assertTrue(dst.exists())
        self.assertGreater(dst.stat().st_size, src.stat().st_size)
        # le nombre de pages doit être inchangé
        self.assertEqual(len(core.page_dimensions(dst)), 2)

    def test_image_size_keeps_aspect(self):
        png = make_png(self.p("s.png"), size=(300, 150))
        w, h = core.image_size_pt(png)
        self.assertEqual(w, core._IMG_TARGET_W_PT)
        self.assertAlmostEqual(h, core._IMG_TARGET_W_PT * 0.5)

    def test_page_out_of_range_clear_error(self):
        src = make_pdf(self.p("src.pdf"), [(400, 600), (400, 600)])
        png = make_png(self.p("sig.png"))
        with self.assertRaises(ValueError) as cm:
            core.insert_image_one(src, self.p("o.pdf"), png, page_index=5, x=10, y=10)
        self.assertIn("hors limites", str(cm.exception))

    def test_inserted_image_has_no_black_border(self):
        # image gris clair opaque sur page blanche : une bordure noire (le défaut
        # border_width=3 de StaticStampStyle) produirait un cadre de pixels noirs.
        src = make_pdf(self.p("p.pdf"), [(300, 400)])
        png = self.p("s.png")
        Image.new("RGB", (160, 80), (210, 210, 210)).save(png)
        dst = self.p("o.pdf")
        core.insert_image_one(src, dst, png, page_index=0, x=60, y=160)
        img = core.render_page_image(dst, 0, px_width=300)
        if img is None:
            self.skipTest("pdftoppm (poppler) indisponible")
        px = img.convert("RGB").load()
        W, H = img.size
        black = sum(1 for yy in range(H) for xx in range(W)
                    if sum(px[xx, yy]) < 90)
        self.assertLess(black, 30, f"{black} pixels (quasi) noirs -> bordure présente")


class BatchImageMode(TmpCase):
    """Chemin partagé CLI/GUI : validation + insertion d'image de bout en bout."""

    def test_process_batch_validates_then_inserts(self):
        tpl = make_pdf(self.p("tpl.pdf"), [(595, 842), (595, 842)])
        good = make_pdf(self.p("good.pdf"), [(595, 842), (595, 842)])
        bad = make_pdf(self.p("bad.pdf"), [(595, 842)])           # mauvais nb de pages
        png = make_png(self.p("sig.png"))
        outdir = self.p("out")

        seen = []
        cfg = core.RunConfig(
            inputs=[good, bad], output=outdir, mode="image", template=tpl,
            image_path=png, page=1, x=100, y=120,
        )
        results = core.process_batch(cfg, on_progress=seen.append)

        by_name = {r.path.name: r for r in results}
        self.assertTrue(by_name["good.pdf"].ok)
        self.assertFalse(by_name["bad.pdf"].ok)
        self.assertIn("rejeté", by_name["bad.pdf"].detail)
        self.assertTrue((outdir / "good_signe.pdf").exists())
        self.assertFalse((outdir / "bad_signe.pdf").exists())     # rejeté → pas de sortie
        self.assertEqual(len(seen), 2)                            # progression par doc


class BatchBeidWiring(TmpCase):
    """Câblage du placement de la vignette beid (sans carte : sign_one est moqué)."""

    def _run(self, cfg):
        calls = []
        saved = (core.open_eid_session, core.BEIDSigner, core.read_card_identity, core.sign_one)
        core.open_eid_session = lambda *a, **k: object()
        core.BEIDSigner = lambda *a, **k: object()
        core.read_card_identity = lambda *a, **k: core.CardIdentity("X Y", None)
        core.sign_one = lambda *a, **k: calls.append((a, k))
        try:
            results = core.process_batch(cfg)
        finally:
            (core.open_eid_session, core.BEIDSigner,
             core.read_card_identity, core.sign_one) = saved
        return calls, results

    def test_placement_passes_page_index_and_pos(self):
        src = make_pdf(self.p("d.pdf"), [(595, 842), (595, 842)])
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid",
                             page=2, x=100, y=50)
        calls, results = self._run(cfg)
        self.assertTrue(results[0].ok)
        (_, kw) = calls[0]
        self.assertEqual(kw.get("page_index"), 1)          # page 2 (1-based) -> index 1
        self.assertEqual(kw.get("pos"), (100.0, 50.0))

    def test_no_placement_uses_default_vignette(self):
        src = make_pdf(self.p("d.pdf"), [(595, 842)])
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid")  # pas de x/y
        calls, _ = self._run(cfg)
        (_, kw) = calls[0]
        self.assertIsNone(kw.get("page_index"))            # -> _last_page_box / on_page=-1
        self.assertIsNone(kw.get("pos"))


class OutputNaming(TmpCase):
    def test_unique_output_path_increments(self):
        self.assertEqual(core.unique_output_path(self.tmp, "doc").name, "doc_signe.pdf")
        (self.tmp / "doc_signe.pdf").write_bytes(b"x")
        self.assertEqual(core.unique_output_path(self.tmp, "doc").name, "doc_signe - 1.pdf")
        (self.tmp / "doc_signe - 1.pdf").write_bytes(b"x")
        self.assertEqual(core.unique_output_path(self.tmp, "doc").name, "doc_signe - 2.pdf")

    def test_process_batch_never_overwrites(self):
        src = make_pdf(self.p("doc.pdf"), [(400, 600)])
        png = make_png(self.p("s.png"))
        out = self.p("out"); out.mkdir()
        (out / "doc_signe.pdf").write_bytes(b"EXISTING")     # collision pré-existante
        cfg = core.RunConfig(inputs=[src], output=out, mode="image",
                             image_path=png, page=1, x=10, y=10)
        results = core.process_batch(cfg)
        self.assertEqual((out / "doc_signe.pdf").read_bytes(), b"EXISTING")  # intact
        self.assertEqual(results[0].output.name, "doc_signe - 1.pdf")
        self.assertTrue((out / "doc_signe - 1.pdf").exists())


class VignetteGeometry(unittest.TestCase):
    def test_3to1_landscape_one_fifth_width(self):
        for page_w in (595.0, 842.0, 1000.0):
            w, h = core.vignette_size_pt(page_w)
            self.assertAlmostEqual(w, page_w / 5)        # largeur = 1/5 de la page
            self.assertAlmostEqual(w / h, 3.0)           # ratio 3:1 paysage


class PageRender(TmpCase):
    def test_render_page_image(self):
        pdf = make_pdf(self.p("two.pdf"), [(300, 400), (300, 400)])
        img = core.render_page_image(pdf, 0, px_width=120)
        if img is None:
            self.skipTest("pdftoppm (poppler) indisponible")
        self.assertEqual(img.size[0], 120)               # mis à l'échelle en largeur
        self.assertAlmostEqual(img.size[1] / img.size[0], 400 / 300, delta=0.05)


class PlacementMath(unittest.TestCase):
    def test_fit_frame_keeps_aspect(self):
        fw, fh = core.fit_frame(600, 800, 300, 400)
        self.assertAlmostEqual(fw / fh, 600 / 800)
        self.assertLessEqual(fw, 300)
        self.assertLessEqual(fh, 400)

    def test_click_corners(self):
        pw, ph, fw, fh = 600, 800, 300, 400
        # haut-gauche du cadre -> (0, ph)
        self.assertEqual(core.frame_click_to_pdf_xy(pw, ph, fw, fh, 0, 0), (0.0, ph))
        # bas-gauche -> (0, 0)
        self.assertEqual(core.frame_click_to_pdf_xy(pw, ph, fw, fh, 0, fh), (0.0, 0.0))
        # bas-droite -> (pw, 0)
        self.assertEqual(core.frame_click_to_pdf_xy(pw, ph, fw, fh, fw, fh), (pw, 0.0))

    def test_pdf_rect_round_trips_into_frame(self):
        pw, ph, fw, fh = 600, 800, 300, 400
        left, top, w, h = core.pdf_rect_to_frame_rect(pw, ph, fw, fh, 0, 0, 150, 100)
        self.assertEqual((left, w), (0.0, 75.0))            # 150 pt * 0.5 = 75 px
        self.assertAlmostEqual(top, fh - 50.0)              # image en bas du cadre
        self.assertAlmostEqual(h, 50.0)


class _Args:
    """Imite l'objet renvoyé par argparse pour tester resolve_config."""

    def __init__(self, **kw):
        defaults = dict(inputs=[], input=None, output=None, template=None, mode="beid",
                        image_path=None, page=1, x=0.0, y=0.0, lib=None, field="Signature",
                        pades=False, gui=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


class ArgResolution(TmpCase):
    def setUp(self):
        super().setUp()
        self.a = make_pdf(self.p("a.pdf"), [(595, 842)])
        self.b = make_pdf(self.p("b.pdf"), [(595, 842)])

    def test_new_flags(self):
        cfg = core.resolve_config(_Args(input=[str(self.a), str(self.b)],
                                        output=str(self.tmp), mode="beid"))
        self.assertEqual([p.name for p in cfg.inputs], ["a.pdf", "b.pdf"])
        self.assertEqual(cfg.output, self.tmp)
        self.assertEqual(cfg.mode, "beid")

    def test_backward_compat_positionals(self):
        # ancien style : « entrées... sortie »
        cfg = core.resolve_config(_Args(inputs=[str(self.a), str(self.tmp)]))
        self.assertEqual([p.name for p in cfg.inputs], ["a.pdf"])
        self.assertEqual(cfg.output, self.tmp)

    def test_image_mode_requires_image(self):
        with self.assertRaises(ValueError):
            core.resolve_config(_Args(input=[str(self.a)], output=str(self.tmp), mode="image"))

    def test_image_mode_ok(self):
        png = make_png(self.p("s.png"))
        cfg = core.resolve_config(_Args(input=[str(self.a)], output=str(self.tmp),
                                        mode="image", image_path=str(png), page=2, x=10, y=20))
        self.assertEqual((cfg.mode, cfg.page, cfg.x, cfg.y), ("image", 2, 10.0, 20.0))

    def test_missing_output_rejected(self):
        with self.assertRaises(ValueError):
            core.resolve_config(_Args(input=[str(self.a)]))  # pas de --output ni positionnel

    def test_template_not_found_rejected(self):
        with self.assertRaises(ValueError):
            core.resolve_config(_Args(input=[str(self.a)], output=str(self.tmp),
                                      template=str(self.tmp / "nope.pdf")))


class GuiImageEndToEnd(unittest.TestCase):
    """Régression : chemin GUI lancer → thread → queue → tableau (mode image).

    Tkinter n'étant pas thread-safe, le worker ne doit PAS appeler `after()` ;
    il pousse dans une queue drainée par le thread principal. Sauté si
    tkinter/affichage indisponible (ex. CI headless)."""

    def setUp(self):
        import types
        try:
            import tkinter
            tkinter.Tk().destroy()      # détecte l'absence d'affichage
            import gui                   # noqa: F401 - nécessite customtkinter+tk
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"tkinter/GUI indisponible : {exc}")
        self.tmp = Path(tempfile.mkdtemp())
        self.types = types

    def test_launch_populates_summary_and_writes_output(self):
        import time
        import gui
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842), (595, 842)])
        png = make_png(self.tmp / "s.png")
        out = self.tmp / "out"
        app = gui.SignApp(self.types.SimpleNamespace(lib=None))
        app.update()
        app.template_path = tpl
        app.template_dims = core.page_dimensions(tpl)
        app.input_paths = [tpl]
        app.output_dir = out
        app._validate()
        app.mode_var.set("image")
        app._refresh_placement_section()
        app.image_path = png
        app._draw_page()
        app.update()
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * 0.5, y=oy + fh * 0.5))
        app.update()
        app._launch()
        self.assertEqual(str(app.launch_btn.cget("state")), "disabled")  # verrouillé pendant
        for _ in range(120):                       # pompe la boucle Tk (max ~6 s)
            app.update()
            time.sleep(0.05)
            if app.status_lbl.cget("text").startswith(("Terminé", "Erreur")):
                break
        app.update()
        status = app.status_lbl.cget("text")
        n_rows = len(app.summary_table.get_children())
        btn_state = str(app.launch_btn.cget("state"))
        app.destroy()
        self.assertTrue(status.startswith("Terminé"), status)   # pas "Erreur" / pas figé
        self.assertEqual(n_rows, 1)
        self.assertEqual(btn_state, "normal")                   # ré-activé après
        self.assertTrue((out / "t_signe.pdf").exists())

    def test_run_batch_reports_systemexit_without_hanging(self):
        # open_eid_session() lève SystemExit (pas de lecteur/carte) ; le worker
        # doit l'attraper et publier une erreur, pas mourir en silence.
        import gui
        app = gui.SignApp(self.types.SimpleNamespace(lib=None))
        app.update()
        app._result_q = __import__("queue").Queue()

        def boom(*a, **k):
            raise SystemExit("Aucun lecteur de carte détecté.")

        orig, core.process_batch = core.process_batch, boom
        try:
            app._run_batch(core.RunConfig(inputs=[], output=self.tmp))  # synchrone ici
        finally:
            core.process_batch = orig
        kind, payload = app._result_q.get_nowait()
        app.destroy()
        self.assertEqual(kind, "error")
        self.assertIn("lecteur", payload)


class GuiBeidPlacement(unittest.TestCase):
    """Refinements 1/4/5 côté GUI en mode beid : placeholder 3:1 (1/5 page),
    pas de sélecteur d'image, canvas qui suit la fenêtre. Sauté sans affichage."""

    def setUp(self):
        import types
        try:
            import tkinter
            tkinter.Tk().destroy()
            import gui  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"tkinter/GUI indisponible : {exc}")
        self.types = types
        self.tmp = Path(tempfile.mkdtemp())

    def test_beid_placeholder_and_resize(self):
        import gui
        tpl = make_pdf(self.tmp / "t.pdf", [(600, 800), (600, 800)])
        app = gui.SignApp(self.types.SimpleNamespace(lib=None))
        app.geometry("900x950")
        app.update()
        app.template_path = tpl
        app._page_img_cache.clear()
        app.template_dims = core.page_dimensions(tpl)
        app.mode_var.set("beid")
        app._refresh_placement_section()
        app.update()
        # mode beid : pas de sélecteur d'image (rangée non gérée par un manager)
        self.assertEqual(app.image_row.winfo_manager(), "")
        # placeholder = vignette 3:1, largeur = page/5
        iw, ih = app._placeholder_size_pt()
        self.assertAlmostEqual(iw, 600 / 5)
        self.assertAlmostEqual(iw / ih, 3.0)
        # un clic fixe la position (mode beid aussi, pas seulement image)
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * 0.5, y=oy + fh * 0.5))
        self.assertIsNotNone(app.place_x)
        big = app.canvas.winfo_reqwidth()
        # rétrécir la fenêtre -> le canvas rétrécit, proportions conservées
        app.geometry("520x680")
        app.update()
        small = app.canvas.winfo_reqwidth()
        app.destroy()
        self.assertLess(small, big)


class HeadlessImport(unittest.TestCase):
    def test_core_imports_without_tkinter(self):
        code = "import sign_pdfs_beid, sys; print('tkinter' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(Path(__file__).parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
