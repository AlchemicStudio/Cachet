#!/usr/bin/env python3
"""Headless tests (no card, no tkinter) for Cachet.

Covers the added business logic: page dimension extraction, validation against
the template, image insertion, placement math, and CLI argument resolution.
Run with:  ./venv/bin/python -m unittest -v
"""

import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from PIL import Image

import sign_pdfs_beid as core


def make_pdf(path, page_sizes) -> Path:
    """Writes a minimal but valid PDF (correct xref) with the given pages,
    each described by a (width, height) in points. Each page has a (empty)
    /Contents stream — required by pyHanko in order to stamp."""
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
        # same page count, but a 1 pt difference on the 2nd page → rejected
        off = make_pdf(self.p("off.pdf"), [(595, 842), (595, 843)])
        r = core.validate_against_template(core.page_dimensions(self.tpl), off)
        self.assertFalse(r.ok)
        self.assertIn("page 2", r.reason)

    def test_validate_files_batch(self):
        good = make_pdf(self.p("good.pdf"), [(595, 842), (595, 842)])
        bad = make_pdf(self.p("bad.pdf"), [(595, 842)])
        results = core.validate_files(self.tpl, [good, bad])
        self.assertEqual([r.ok for r in results], [True, False])

    def test_anchor_accepts_count_mismatch_when_anchor_page_matches(self):
        dims = core.page_dimensions(self.tpl)
        fewer = make_pdf(self.p("fewer.pdf"), [(595, 842)])
        more = make_pdf(self.p("more.pdf"), [(595, 842), (595, 842), (595, 842)])
        for pdf in (fewer, more):
            for anchor in ("first", "last"):
                r = core.validate_against_template(dims, pdf, page_anchor=anchor)
                self.assertTrue(r.ok, r.reason)
                self.assertIn(anchor, r.reason)     # informative detail for the tables

    def test_anchor_rejects_when_anchor_page_differs(self):
        dims = core.page_dimensions(self.tpl)
        # 3 pages, odd LAST page: fine for "first", rejected for "last"
        odd_last = make_pdf(self.p("odd_last.pdf"),
                            [(595, 842), (595, 842), (200, 200)])
        self.assertTrue(
            core.validate_against_template(dims, odd_last, page_anchor="first").ok)
        r = core.validate_against_template(dims, odd_last, page_anchor="last")
        self.assertFalse(r.ok)
        self.assertIn("last page", r.reason)

    def test_anchor_keeps_strict_check_for_same_page_count(self):
        dims = core.page_dimensions(self.tpl)
        # same page count, mismatch NOT on the anchor page → still rejected
        off = make_pdf(self.p("off1.pdf"), [(596, 842), (595, 842)])
        r = core.validate_against_template(dims, off, page_anchor="last")
        self.assertFalse(r.ok)
        self.assertIn("page 1", r.reason)


class ImageInsertion(TmpCase):
    def test_insert_preserves_pages_and_writes_output(self):
        src = make_pdf(self.p("src.pdf"), [(400, 600), (400, 600)])
        png = make_png(self.p("sig.png"))
        dst = self.p("out.pdf")
        core.insert_image_one(src, dst, png, page_index=1, x=50, y=60)
        self.assertTrue(dst.exists())
        self.assertGreater(dst.stat().st_size, src.stat().st_size)
        # the page count must be unchanged
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
        self.assertIn("out of range", str(cm.exception))

    def test_inserted_image_has_no_black_border(self):
        # opaque light-gray image on a white page: a black border (StaticStampStyle's
        # default border_width=3) would produce a frame of black pixels.
        src = make_pdf(self.p("p.pdf"), [(300, 400)])
        png = self.p("s.png")
        Image.new("RGB", (160, 80), (210, 210, 210)).save(png)
        dst = self.p("o.pdf")
        core.insert_image_one(src, dst, png, page_index=0, x=60, y=160)
        img = core.render_page_image(dst, 0, px_width=300)
        if img is None:
            self.skipTest("pdftoppm (poppler) unavailable")
        px = img.convert("RGB").load()
        W, H = img.size
        black = sum(1 for yy in range(H) for xx in range(W)
                    if sum(px[xx, yy]) < 90)
        self.assertLess(black, 30, f"{black} (near-)black pixels -> border present")


class BatchImageMode(TmpCase):
    """Shared CLI/GUI path: validation + image insertion end-to-end."""

    def test_process_batch_validates_then_inserts(self):
        tpl = make_pdf(self.p("tpl.pdf"), [(595, 842), (595, 842)])
        good = make_pdf(self.p("good.pdf"), [(595, 842), (595, 842)])
        bad = make_pdf(self.p("bad.pdf"), [(595, 842)])           # wrong page count
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
        self.assertIn("rejected", by_name["bad.pdf"].detail)
        self.assertTrue((outdir / "good_signe.pdf").exists())
        self.assertFalse((outdir / "bad_signe.pdf").exists())     # rejected → no output
        self.assertEqual(len(seen), 2)                            # per-doc progress

    def test_anchor_signs_count_mismatches_on_their_last_page(self):
        tpl = make_pdf(self.p("tpl.pdf"), [(595, 842), (595, 842)])
        short = make_pdf(self.p("short.pdf"), [(595, 842)])       # 1 page vs 2
        long_bad = make_pdf(self.p("long_bad.pdf"),               # last page differs
                            [(595, 842), (595, 842), (200, 200)])
        png = make_png(self.p("sig.png"))
        outdir = self.p("out")
        cfg = core.RunConfig(inputs=[short, long_bad], output=outdir, mode="image",
                             template=tpl, image_path=png,
                             page_anchor="last", x=10, y=10)
        results = core.process_batch(cfg)
        by_name = {r.path.name: r for r in results}
        self.assertTrue(by_name["short.pdf"].ok)
        self.assertIn("last page", by_name["short.pdf"].detail)
        self.assertFalse(by_name["long_bad.pdf"].ok)              # anchor page ≠ template's
        self.assertIn("rejected", by_name["long_bad.pdf"].detail)
        self.assertTrue((outdir / "short_signe.pdf").exists())
        self.assertFalse((outdir / "long_bad_signe.pdf").exists())


class BatchBeidWiring(TmpCase):
    """beid vignette placement wiring (no card: sign_one is mocked)."""

    def _run(self, cfg):
        calls = []
        saved = (core.open_eid_session, core.BEIDSigner, core.read_card_identity,
                 core.sign_one, core.build_signing_material, core.verify_signed_pdf)
        core.open_eid_session = lambda *a, **k: object()
        core.BEIDSigner = lambda *a, **k: object()
        core.read_card_identity = lambda *a, **k: core.CardIdentity("X Y", None)
        core.sign_one = lambda *a, **k: calls.append((a, k))
        # No network in tests: empty signing material, canned verification.
        core.build_signing_material = lambda cfg: core.SigningMaterial()
        core.verify_signed_pdf = lambda *a, **k: "PAdES-B-LTA, LTV ok"
        try:
            results = core.process_batch(cfg)
        finally:
            (core.open_eid_session, core.BEIDSigner, core.read_card_identity,
             core.sign_one, core.build_signing_material,
             core.verify_signed_pdf) = saved
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
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid")  # no x/y
        calls, _ = self._run(cfg)
        (_, kw) = calls[0]
        self.assertIsNone(kw.get("page_index"))            # -> default box on the last page
        self.assertIsNone(kw.get("pos"))

    def test_anchor_resolves_page_per_document(self):
        src = make_pdf(self.p("d.pdf"), [(595, 842), (595, 842)])
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid",
                             page_anchor="first", x=30, y=40)
        calls, results = self._run(cfg)
        self.assertTrue(results[0].ok)
        (_, kw) = calls[0]
        self.assertEqual(kw.get("page_index"), 0)          # "first" -> index 0
        self.assertEqual(kw.get("pos"), (30.0, 40.0))
        self.assertIn("first page", results[0].detail)

    def test_anchor_without_position_moves_default_vignette(self):
        src = make_pdf(self.p("d.pdf"), [(595, 842), (595, 842)])
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid",
                             page_anchor="first")          # no x/y
        calls, results = self._run(cfg)
        (_, kw) = calls[0]
        self.assertEqual(kw.get("page_index"), 0)          # corner box, first page
        self.assertIsNone(kw.get("pos"))
        self.assertIn("first page", results[0].detail)


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
        (out / "doc_signe.pdf").write_bytes(b"EXISTING")     # pre-existing collision
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
            self.assertAlmostEqual(w, page_w / 5)        # width = 1/5 of the page
            self.assertAlmostEqual(w / h, 3.0)           # 3:1 landscape ratio


class DefaultVignetteBox(TmpCase):
    def test_box_anchors_to_requested_page(self):
        # two pages of different sizes: the default bottom-right box must
        # follow the page it is asked for (anchor first/last support)
        pdf = make_pdf(self.p("d.pdf"), [(400, 600), (800, 900)])
        with open(pdf, "rb") as f:
            w = core.IncrementalPdfFileWriter(f, strict=False)
            first = core._default_vignette_box(w, 0)
            last = core._default_vignette_box(w, -1)
        self.assertAlmostEqual(first[2], 400 - core._STAMP_MARGIN)  # right edge
        self.assertAlmostEqual(last[2], 800 - core._STAMP_MARGIN)

    def test_anchor_page_index(self):
        self.assertEqual(core.anchor_page_index("first"), 0)
        self.assertEqual(core.anchor_page_index("last"), -1)


class PageRender(TmpCase):
    def test_render_page_image(self):
        pdf = make_pdf(self.p("two.pdf"), [(300, 400), (300, 400)])
        img = core.render_page_image(pdf, 0, px_width=120)
        if img is None:
            self.skipTest("pdftoppm (poppler) unavailable")
        self.assertEqual(img.size[0], 120)               # scaled to the width
        self.assertAlmostEqual(img.size[1] / img.size[0], 400 / 300, delta=0.05)


class PlacementMath(unittest.TestCase):
    def test_fit_frame_keeps_aspect(self):
        fw, fh = core.fit_frame(600, 800, 300, 400)
        self.assertAlmostEqual(fw / fh, 600 / 800)
        self.assertLessEqual(fw, 300)
        self.assertLessEqual(fh, 400)

    def test_click_corners(self):
        pw, ph, fw, fh = 600, 800, 300, 400
        # top-left of the frame -> (0, ph)
        self.assertEqual(core.frame_click_to_pdf_xy(pw, ph, fw, fh, 0, 0), (0.0, ph))
        # bottom-left -> (0, 0)
        self.assertEqual(core.frame_click_to_pdf_xy(pw, ph, fw, fh, 0, fh), (0.0, 0.0))
        # bottom-right -> (pw, 0)
        self.assertEqual(core.frame_click_to_pdf_xy(pw, ph, fw, fh, fw, fh), (pw, 0.0))

    def test_pdf_rect_round_trips_into_frame(self):
        pw, ph, fw, fh = 600, 800, 300, 400
        left, top, w, h = core.pdf_rect_to_frame_rect(pw, ph, fw, fh, 0, 0, 150, 100)
        self.assertEqual((left, w), (0.0, 75.0))            # 150 pt * 0.5 = 75 px
        self.assertAlmostEqual(top, fh - 50.0)              # image at the bottom of the frame
        self.assertAlmostEqual(h, 50.0)


class _Args:
    """Mimics the object returned by argparse to test resolve_config."""

    def __init__(self, **kw):
        defaults = dict(inputs=[], input=None, output=None, template=None, mode="beid",
                        image_path=None, page=1, x=0.0, y=0.0, lib=None, field="Signature",
                        pades=False, gui=False, pades_level=None, legacy_cms=False,
                        timestamp_url=None, trust_list_url=None, digest="sha256",
                        verify=True, refresh_trust_list=False)
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
        # old style: "inputs... output"
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
            core.resolve_config(_Args(input=[str(self.a)]))  # no --output nor positional

    def test_template_not_found_rejected(self):
        with self.assertRaises(ValueError):
            core.resolve_config(_Args(input=[str(self.a)], output=str(self.tmp),
                                      template=str(self.tmp / "nope.pdf")))

    def test_page_anchor_resolution(self):
        cfg = core.resolve_config(_Args(input=[str(self.a)], output=str(self.tmp),
                                        page="last"))
        self.assertIsNone(cfg.page)
        self.assertEqual(cfg.page_anchor, "last")

    def test_page_anchor_config_rules(self):
        with self.assertRaises(ValueError):    # page and anchor are exclusive
            core.validate_config(core.RunConfig(inputs=[self.a], output=self.tmp,
                                                page=2, page_anchor="last"))
        with self.assertRaises(ValueError):    # only first|last are anchors
            core.validate_config(core.RunConfig(inputs=[self.a], output=self.tmp,
                                                page_anchor="middle"))


class LevelMapping(unittest.TestCase):
    """Pure mapping pades_level -> PdfSignatureMetadata kwargs (R3-R6)."""

    def test_b_b_basic_pades_only(self):
        kw = core.signature_meta_kwargs("b-b", "sha256")
        self.assertEqual(kw, {"md_algorithm": "sha256",
                              "subfilter": core.SigSeedSubFilter.PADES})

    def test_b_t_same_metadata_timestamper_is_separate(self):
        # The RFC 3161 timestamper rides on PdfSigner, not on the metadata.
        kw = core.signature_meta_kwargs("b-t", "sha256")
        self.assertEqual(kw, {"md_algorithm": "sha256",
                              "subfilter": core.SigSeedSubFilter.PADES})
        self.assertTrue(core.level_needs_timestamp("b-t"))
        self.assertFalse(core.level_needs_ltv("b-t"))

    def test_b_lt_embeds_validation_info(self):
        sentinel = object()
        kw = core.signature_meta_kwargs("b-lt", "sha256", validation_context=sentinel)
        self.assertIs(kw["validation_context"], sentinel)
        self.assertTrue(kw["embed_validation_info"])
        self.assertNotIn("use_pades_lta", kw)

    def test_b_lta_adds_archival_timestamp(self):
        sentinel = object()
        kw = core.signature_meta_kwargs("b-lta", "sha384", validation_context=sentinel)
        self.assertEqual(kw["md_algorithm"], "sha384")  # --digest is pinned
        self.assertTrue(kw["embed_validation_info"])
        self.assertTrue(kw["use_pades_lta"])

    def test_legacy_cms_keeps_old_subfilter(self):
        kw = core.signature_meta_kwargs("b-b", "sha256", legacy_cms=True)
        self.assertEqual(kw, {"md_algorithm": "sha256",
                              "subfilter": core.SigSeedSubFilter.ADOBE_PKCS7_DETACHED})

    def test_unknown_level_rejected(self):
        with self.assertRaises(ValueError):
            core.signature_meta_kwargs("b-x", "sha256")

    def test_timestamp_and_ltv_thresholds(self):
        self.assertFalse(core.level_needs_timestamp("b-b"))
        self.assertEqual([core.level_needs_timestamp(l) for l in ("b-t", "b-lt", "b-lta")],
                         [True, True, True])
        self.assertEqual([core.level_needs_ltv(l) for l in core.PADES_LEVELS],
                         [False, False, True, True])

    def test_level_labels(self):
        self.assertEqual(core.signature_level_label("b-lta"), "PAdES-B-LTA")
        self.assertIn("adbe.pkcs7.detached", core.signature_level_label("b-b", True))


class NewFlagParsing(TmpCase):
    """CLI parsing of the PAdES-level flags through the real arg parser."""

    def setUp(self):
        super().setUp()
        self.a = make_pdf(self.p("a.pdf"), [(595, 842)])

    def parse(self, *extra):
        argv = ["--input", str(self.a), "--output", str(self.tmp), *extra]
        return core.resolve_config(core.build_arg_parser().parse_args(argv))

    def test_version_flag(self):
        import contextlib, io
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(buf):
            core.build_arg_parser().parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(buf.getvalue().strip(), f"Cachet {core.__version__}")

    def test_default_level_is_b_lta(self):
        cfg = self.parse()
        self.assertEqual(cfg.pades_level, "b-lta")
        self.assertEqual(cfg.digest, "sha256")
        self.assertTrue(cfg.verify)
        self.assertFalse(cfg.legacy_cms)
        self.assertFalse(cfg.refresh_trust_list)
        self.assertIsNone(cfg.timestamp_url)

    def test_explicit_level(self):
        self.assertEqual(self.parse("--pades-level", "b-t").pades_level, "b-t")

    def test_deprecated_pades_warns_and_is_noop(self):
        with self.assertWarns(FutureWarning):
            cfg = self.parse("--pades")
        self.assertEqual(cfg.pades_level, "b-lta")  # no effect on the level

    def test_legacy_cms_alone_maps_to_b_b(self):
        with self.assertWarns(FutureWarning):
            cfg = self.parse("--legacy-cms")
        self.assertTrue(cfg.legacy_cms)
        self.assertEqual(cfg.pades_level, "b-b")

    def test_legacy_cms_allows_explicit_b_b(self):
        with self.assertWarns(FutureWarning):
            cfg = self.parse("--legacy-cms", "--pades-level", "b-b")
        self.assertTrue(cfg.legacy_cms)

    def test_legacy_cms_excludes_higher_levels(self):
        for level in ("b-t", "b-lt", "b-lta"):
            with self.assertRaises(ValueError), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.parse("--legacy-cms", "--pades-level", level)

    def test_digest_flag(self):
        self.assertEqual(self.parse("--digest", "sha512").digest, "sha512")

    def test_page_flag_number_or_anchor(self):
        cfg = self.parse("--page", "3")
        self.assertEqual((cfg.page, cfg.page_anchor), (3, None))
        cfg = self.parse("--page", "first")
        self.assertEqual((cfg.page, cfg.page_anchor), (None, "first"))
        cfg = self.parse("--page", "LAST")                  # case-insensitive
        self.assertEqual((cfg.page, cfg.page_anchor), (None, "last"))

    def test_page_flag_rejects_garbage(self):
        import contextlib, io
        with self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            core.build_arg_parser().parse_args(["--page", "banana"])

    def test_no_verify_flag(self):
        self.assertFalse(self.parse("--no-verify").verify)

    def test_refresh_trust_list_flag(self):
        self.assertTrue(self.parse("--refresh-trust-list").refresh_trust_list)

    def test_urls_flow_into_config(self):
        cfg = self.parse("--timestamp-url", "http://tsa.example",
                         "--trust-list-url", "https://lotl.example/x.xml")
        self.assertEqual(cfg.timestamp_url, "http://tsa.example")
        self.assertEqual(cfg.trust_list_url, "https://lotl.example/x.xml")

    def test_tsa_url_precedence_flag_env_default(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {core.ENV_TSA_URL: "http://env.example"}):
            self.assertEqual(core.resolve_tsa_url("http://flag.example"),
                             "http://flag.example")
            self.assertEqual(core.resolve_tsa_url(None), "http://env.example")
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(core.ENV_TSA_URL, None)
            self.assertEqual(core.resolve_tsa_url(None), core.DEFAULT_TSA_URL)


class SigningMaterialWiring(unittest.TestCase):
    """build_signing_material: timestamper/context per level (offline-safe)."""

    def _cfg(self, **kw):
        kw.setdefault("inputs", [Path("x.pdf")])
        kw.setdefault("output", Path("out"))
        return core.RunConfig(**kw)

    def test_image_mode_and_b_b_and_legacy_build_nothing(self):
        for cfg in (self._cfg(mode="image", pades_level="b-lta"),
                    self._cfg(pades_level="b-b"),
                    self._cfg(pades_level="b-b", legacy_cms=True)):
            material = core.build_signing_material(cfg)
            self.assertIsNone(material.timestamper)
            self.assertIsNone(material.validation_context)

    def test_b_t_attaches_http_timestamper_only(self):
        # verify=False: plain b-t needs no trust anchors (and no network here)
        material = core.build_signing_material(
            self._cfg(pades_level="b-t", timestamp_url="http://tsa.example",
                      verify=False))
        self.assertIsInstance(material.timestamper, core.timestamps.HTTPTimeStamper)
        self.assertEqual(material.tsa_url, "http://tsa.example")
        self.assertIsNone(material.validation_context)  # no LTV below b-lt
        self.assertIsNone(material.trust_anchors)

    def test_b_t_with_verification_fetches_anchors(self):
        import trust
        from unittest import mock
        with mock.patch.object(trust, "get_trust_anchors", return_value=[]):
            material = core.build_signing_material(self._cfg(pades_level="b-t"))
        self.assertEqual(material.trust_anchors, [])  # fetched for R8
        self.assertIsNone(material.validation_context)  # still no LTV embed

    def test_b_lta_builds_fetching_context_from_trust_anchors(self):
        import trust
        from unittest import mock
        with mock.patch.object(trust, "get_trust_anchors", return_value=[]) as got:
            material = core.build_signing_material(
                self._cfg(pades_level="b-lta", trust_list_url="https://l.example",
                          refresh_trust_list=True))
        got.assert_called_once_with("https://l.example", refresh=True)
        self.assertIsInstance(material.timestamper, core.timestamps.HTTPTimeStamper)
        self.assertIsNotNone(material.validation_context)
        self.assertEqual(material.trust_anchors, [])


class SelfVerification(TmpCase):
    """R8 verify_signed_pdf on REAL pyHanko output, fully offline.

    Signs synthetic PDFs with an in-memory self-signed cert (SimpleSigner)
    and pyHanko's DummyTimeStamper instead of the eID card + network TSA:
    the level mapping and the verification logic are exercised end-to-end;
    only the PKCS#11 hardware path stays a manual acceptance test.
    """

    @classmethod
    def setUpClass(cls):
        import datetime
        from cryptography import x509 as cx509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, rsa
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from asn1crypto import x509 as ax509, keys as akeys, crl as acrl
        from pyhanko.sign import signers as psigners
        from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
        from pyhanko_certvalidator import ValidationContext
        from pyhanko_certvalidator.registry import SimpleCertificateStore

        def _key():
            return ec.generate_private_key(ec.SECP256R1())

        def _name(cn):
            return cx509.Name([cx509.NameAttribute(NameOID.COMMON_NAME, cn)])

        start = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        end = start + datetime.timedelta(days=3650)

        def _build(cn, key, *, ca=False, eku=None, issuer=None, issuer_key=None):
            b = (cx509.CertificateBuilder()
                 .subject_name(_name(cn))
                 .issuer_name(_name(issuer) if issuer else _name(cn))
                 .public_key(key.public_key())
                 .serial_number(cx509.random_serial_number())
                 .not_valid_before(start).not_valid_after(end)
                 .add_extension(cx509.BasicConstraints(ca=ca, path_length=None),
                                critical=True)
                 .add_extension(cx509.KeyUsage(
                     digital_signature=True, content_commitment=True,
                     key_encipherment=False, data_encipherment=False,
                     key_agreement=False, key_cert_sign=ca, crl_sign=ca,
                     encipher_only=False, decipher_only=False), critical=True))
            if eku:
                b = b.add_extension(cx509.ExtendedKeyUsage(eku), critical=False)
            return b.sign(issuer_key or key, hashes.SHA256())

        def _der_cert(c):
            return ax509.Certificate.load(
                c.public_bytes(serialization.Encoding.DER))

        def _der_key(k):
            return akeys.PrivateKeyInfo.load(k.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))

        # Self-signed CA that also signs documents (simplest trustable chain),
        # a separate self-signed TSA cert, and a fresh empty CRL from the CA
        # so b-lt/b-lta have revocation material to embed into the DSS.
        ca_key = _key()
        ca_cert = _build("Test Sign CA", ca_key, ca=True)
        tsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        tsa_cert = _build("Test TSA", tsa_key,  # DummyTimeStamper is RSA-only
                          eku=[ExtendedKeyUsageOID.TIME_STAMPING])
        crl = (cx509.CertificateRevocationListBuilder()
               .issuer_name(_name("Test Sign CA"))
               .last_update(start).next_update(end)
               .sign(ca_key, hashes.SHA256()))
        cls.root = _der_cert(ca_cert)
        cls.tsa_root = _der_cert(tsa_cert)
        cls.crl = acrl.CertificateList.load(
            crl.public_bytes(serialization.Encoding.DER))

        cls.signer = psigners.SimpleSigner(
            signing_cert=cls.root,
            signing_key=_der_key(ca_key),
            cert_registry=SimpleCertificateStore.from_certs([cls.root]),
        )
        cls.timestamper = DummyTimeStamper(
            tsa_cert=cls.tsa_root,
            tsa_key=_der_key(tsa_key),
            certs_to_embed=SimpleCertificateStore.from_certs([cls.tsa_root]),
        )
        # Signing-time context: offline, pre-loaded CRL, both roots trusted
        # (pyHanko validates the TSA chain against this same context).
        cls.signing_vc = ValidationContext(
            trust_roots=[cls.root, cls.tsa_root],
            crls=[cls.crl],
            allow_fetching=False,
            revocation_mode="soft-fail",
        )

    def _sign(self, level: str) -> Path:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers as psigners

        src = make_pdf(self.p(f"{level}.pdf"), [(595, 842)])
        dst = self.p(f"{level}_signed.pdf")
        meta = core.PdfSignatureMetadata(
            field_name="Sig1",
            **core.signature_meta_kwargs(level, "sha256",
                                         validation_context=self.signing_vc),
        )
        with src.open("rb") as inf:
            w = IncrementalPdfFileWriter(inf, strict=False)
            with dst.open("wb") as outf:
                psigners.sign_pdf(w, meta, signer=self.signer,
                                  timestamper=self.timestamper, output=outf)
        return dst

    def _verify(self, dst, expected):
        return core.verify_signed_pdf(dst, expected,
                                      trust_anchors=[self.root, self.tsa_root])

    def test_b_t_detected(self):
        self.assertEqual(self._verify(self._sign("b-t"), "b-t"), "PAdES-B-T")

    def test_b_lt_detected_with_ltv(self):
        self.assertEqual(self._verify(self._sign("b-lt"), "b-lt"),
                         "PAdES-B-LT, LTV ok")

    def test_b_lta_detected_with_ltv(self):
        self.assertEqual(self._verify(self._sign("b-lta"), "b-lta"),
                         "PAdES-B-LTA, LTV ok")

    def test_mismatch_fails_not_passes(self):
        dst = self._sign("b-t")  # only a timestamp, no LTV material
        with self.assertRaises(core.SelfVerificationError) as ctx:
            self._verify(dst, "b-lta")
        self.assertIn("B-LTA", str(ctx.exception))
        self.assertIn("B-T", str(ctx.exception))

    def test_legacy_subfilter_rejected_as_non_pades(self):
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign import signers as psigners

        src = make_pdf(self.p("legacy.pdf"), [(595, 842)])
        dst = self.p("legacy_signed.pdf")
        meta = core.PdfSignatureMetadata(
            field_name="Sig1",
            **core.signature_meta_kwargs("b-b", "sha256", legacy_cms=True),
        )
        with src.open("rb") as inf:
            w = IncrementalPdfFileWriter(inf, strict=False)
            with dst.open("wb") as outf:
                psigners.sign_pdf(w, meta, signer=self.signer, output=outf)
        with self.assertRaises(core.SelfVerificationError):
            self._verify(dst, "b-b")


class SummaryOutput(unittest.TestCase):
    def _capture(self, **kw):
        import contextlib, io
        buf = io.StringIO()
        results = [core.DocResult(Path("a.pdf"), Path("out/a_signe.pdf"), True,
                                  "signed (eID) — vignette — PAdES-B-LTA, LTV ok")]
        with contextlib.redirect_stdout(buf):
            core.print_summary(results, "out", **kw)
        return buf.getvalue()

    def test_rrn_note_in_beid_summary(self):
        self.assertIn("national register number", self._capture(rrn_note=True))

    def test_no_rrn_note_otherwise(self):
        self.assertNotIn("national register number", self._capture())


class _GuiTestBase(unittest.TestCase):
    """Shared display-guarded setup for the wizard tests."""

    def setUp(self):
        import types
        try:
            import tkinter
            tkinter.Tk().destroy()      # detects the absence of a display
            import gui                   # noqa: F401 - requires customtkinter+tk
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"tkinter/GUI unavailable: {exc}")
        self.tmp = Path(tempfile.mkdtemp())
        self.types = types

    def make_app(self):
        import contextlib
        import tkinter

        import gui
        app = gui.CachetApp(self.types.SimpleNamespace(lib=None))
        app.update()

        def _cleanup():
            # a root leaked by a failing test stays tkinter._default_root and
            # breaks every later canvas test ("pyimage… doesn't exist")
            with contextlib.suppress(tkinter.TclError):
                app.destroy()

        self.addCleanup(_cleanup)
        return app

    def wizard_with_state(self, app, tpl, out, inputs=None):
        """Start the wizard and inject valid template/files/output state,
        then run the validation step (it auto-validates on entry)."""
        app._start_wizard()
        app.template_path = tpl
        app.template_error = None
        app.template_dims = core.page_dimensions(tpl)
        app.input_paths = list(inputs) if inputs is not None else [tpl]
        app.output_dir = out
        app._goto_step(2)               # validation auto-runs on first entry
        app.update()


class GuiImageEndToEnd(_GuiTestBase):
    """Regression: wizard → worker thread → queue → report path (image mode).

    Since Tkinter is not thread-safe, the worker must NOT call `after()`;
    it pushes onto a queue drained by the main thread. Skipped if
    tkinter/display is unavailable (e.g. headless CI)."""

    def test_launch_populates_report_and_writes_output(self):
        import time
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842), (595, 842)])
        png = make_png(self.tmp / "s.png")
        out = self.tmp / "out"
        app = self.make_app()
        self.wizard_with_state(app, tpl, out)
        self.assertEqual(len(app.valid_paths), 1)
        app.mode_var.set("image")
        app.image_path = png
        app._goto_step(5)               # placement step builds the canvas
        app._draw_page()
        app.update()
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * 0.5, y=oy + fh * 0.5))
        app.update()
        app._goto_step(6)               # signing step
        app._launch()
        self.assertTrue(app._running)
        self.assertEqual(str(app.launch_btn.cget("state")), "disabled")  # locked during
        self.assertEqual(str(app._btn_next.cget("state")), "disabled")   # nav locked
        for _ in range(120):                       # pump the Tk loop (max ~6 s)
            app.update()
            time.sleep(0.05)
            if not app._running:
                break
        app.update()
        run_error = app.run_error
        results = app.run_results
        step = app.step_index
        n_rows = len(app.summary_table.get_children())
        app.destroy()
        self.assertIsNone(run_error)
        self.assertIsNotNone(results)              # not "Error" / not frozen
        self.assertEqual(step, 7)                  # auto-advanced to the report
        self.assertEqual(n_rows, 1)
        self.assertTrue((out / "t_signe.pdf").exists())

    def test_run_batch_reports_systemexit_without_hanging(self):
        # open_eid_session() raises SystemExit (no reader/card); the worker
        # must catch it and publish an error, not die silently.
        app = self.make_app()
        app._result_q = __import__("queue").Queue()

        def boom(*a, **k):
            raise SystemExit("No card reader detected.")

        orig, core.process_batch = core.process_batch, boom
        try:
            app._run_batch(core.RunConfig(inputs=[], output=self.tmp))  # synchronous here
        finally:
            core.process_batch = orig
        kind, payload = app._result_q.get_nowait()
        app.destroy()
        self.assertEqual(kind, "error")
        self.assertIn("reader", payload)


class GuiBeidPlacement(_GuiTestBase):
    """Placement step in beid mode: 3:1 placeholder (1/5 page), no image
    picker, click → target-page sync, canvas that follows the window."""

    def test_beid_placeholder_and_resize(self):
        tpl = make_pdf(self.tmp / "t.pdf", [(600, 800), (600, 800)])
        app = self.make_app()
        app.geometry("900x950")
        app.update()
        self.wizard_with_state(app, tpl, self.tmp)
        app.mode_var.set("beid")
        app._goto_step(5)               # placement step
        app.update()
        # beid mode: no image picker (row not handled by a manager)
        self.assertEqual(app.image_row.winfo_manager(), "")
        # placeholder = 3:1 vignette, width = page/5
        iw, ih = app._placeholder_size_pt()
        self.assertAlmostEqual(iw, 600 / 5)
        self.assertAlmostEqual(iw / ih, 3.0)
        # a click sets the position (in beid mode too, not just image) and
        # syncs the manual target-page field
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * 0.5, y=oy + fh * 0.5))
        self.assertIsNotNone(app.place_x)
        self.assertEqual(app.page_text, "1")
        big = app.canvas.winfo_reqwidth()
        # shrink the window -> the canvas shrinks, proportions preserved
        app.geometry("520x680")
        app.update()
        small = app.canvas.winfo_reqwidth()
        app.destroy()
        self.assertLess(small, big)


class GuiWizardChrome(_GuiTestBase):
    """Wizard shell: step gating, dynamic nav labels, stepper state colors,
    cancel-confirm reset, and localized chrome."""

    def test_gating_and_dynamic_labels(self):
        from i18n import tr
        app = self.make_app()
        app._start_wizard()
        app.update()
        # step 1: no template yet -> Previous and Next both disabled, and the
        # Next label names the target step
        self.assertEqual(str(app._btn_prev.cget("state")), "disabled")
        self.assertEqual(str(app._btn_next.cget("state")), "disabled")
        self.assertIn(tr("step.files.title"), app._btn_next.cget("text"))
        # steps beyond the first incomplete one are locked
        self.assertEqual(str(app._stepper_btns[4].cget("state")), "disabled")
        app._goto_step(4)
        self.assertEqual(app.step_index, 0)
        # completing step 1 unlocks Next
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        app.template_path = tpl
        app.template_dims = core.page_dimensions(tpl)
        app._refresh_chrome()
        self.assertEqual(str(app._btn_next.cget("state")), "normal")
        app.destroy()

    def test_validation_errors_color_the_stepper(self):
        import gui
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        bad = make_pdf(self.tmp / "bad.pdf", [(300, 300)])
        app = self.make_app()
        app._start_wizard()
        app.template_path = tpl
        app.template_error = None
        app.template_dims = core.page_dimensions(tpl)
        app.input_paths = [tpl, bad]
        app._goto_step(2)
        app.update()
        self.assertEqual(len(app.valid_paths), 1)
        # a rejected file marks the validation step red; passed steps green
        self.assertEqual(app._stepper_btns[2].cget("border_color"), gui._COL_ERROR)
        self.assertEqual(app._stepper_btns[0].cget("border_color"), gui._COL_DONE)
        app.destroy()

    def test_cancel_confirm_resets_to_landing(self):
        import customtkinter as ctk

        from i18n import tr
        app = self.make_app()
        app._start_wizard()
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        app.template_path = tpl
        app.template_dims = core.page_dimensions(tpl)
        app._refresh_chrome()
        app._cancel_wizard()
        app.update()
        tops = [w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel)]
        self.assertTrue(tops, "cancel confirmation modal missing")
        btns = [w for w in tops[0].winfo_children()[-1].winfo_children()
                if isinstance(w, ctk.CTkButton)]
        confirm = next(b for b in btns if b.cget("text") == tr("cancel.confirm"))
        confirm.invoke()
        app.update()
        self.assertIsNone(app.template_path)       # all data reset
        self.assertEqual(app._stepper_btns, [])    # back on the landing page
        app.destroy()

    def test_localized_wizard_chrome(self):
        import i18n
        app = self.make_app()
        try:
            i18n.set_language("fr")
            app._start_wizard()
            app.update()
            self.assertEqual(app._stepper_btns[0].cget("text"), "1. Modèle")
            self.assertEqual(app._btn_cancel.cget("text"), "Annuler")
        finally:
            i18n.set_language("en")
        app.destroy()


class GuiPageAnchor(_GuiTestBase):
    """Validation-step first/last selector: appears only when some files'
    page count differs from the template, locks the placement preview onto
    that template page (target-page field and Prev/Next disabled), and the
    batch signs those files on their own first/last page."""

    def test_selector_hidden_without_mismatch(self):
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        same = make_pdf(self.tmp / "same.pdf", [(595, 842)])
        app = self.make_app()
        self.wizard_with_state(app, tpl, self.tmp / "o", inputs=[same])
        manager = app.anchor_row.winfo_manager()
        anchor = app._page_anchor()
        app.destroy()
        self.assertEqual(manager, "")                 # selector not shown
        self.assertIsNone(anchor)

    def test_selector_locks_page_and_signs_short_file(self):
        import time
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842), (595, 842)])
        short = make_pdf(self.tmp / "short.pdf", [(595, 842)])    # 1 page vs 2
        png = make_png(self.tmp / "s.png")
        out = self.tmp / "out"
        app = self.make_app()
        self.wizard_with_state(app, tpl, out, inputs=[short])
        self.assertNotEqual(app.anchor_row.winfo_manager(), "")   # selector shown
        self.assertEqual(app.valid_paths, [short])                # accepted
        self.assertEqual(app._page_anchor(), "last")              # default anchor
        self.assertEqual(app.cur_page, 1)     # locked on the template's LAST page
        app._set_page_anchor_choice("first")      # switch (menu re-validates)
        self.assertEqual(app._page_anchor(), "first")
        self.assertEqual(app.cur_page, 0)
        # end-to-end in image mode: the 1-page file gets signed on page 1
        app.mode_var.set("image")
        app.image_path = png
        app._goto_step(5)                         # placement step, locked
        app.update()
        self.assertEqual(app.cur_page, 0)
        app._turn_page(1)                         # navigation is locked
        self.assertEqual(app.cur_page, 0)
        self.assertEqual(str(app.page_entry.cget("state")), "disabled")
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * 0.5, y=oy + fh * 0.5))
        app.update()
        app._goto_step(6)                         # signing step
        app._launch()
        for _ in range(120):                      # pump the Tk loop (max ~6 s)
            app.update()
            time.sleep(0.05)
            if not app._running:
                break
        app.update()
        results = app.run_results
        step = app.step_index
        n_rows = len(app.summary_table.get_children())
        app.destroy()
        self.assertIsNotNone(results)
        self.assertTrue(results[0].ok, results[0].detail)
        self.assertIn("first page", results[0].detail)
        self.assertEqual(step, 7)                 # auto-advanced to the report
        self.assertEqual(n_rows, 1)
        self.assertTrue((out / "short_signe.pdf").exists())


class OpenFolder(unittest.TestCase):
    """`open_in_file_manager`: per-platform command, errors propagate."""

    def setUp(self):
        self.out = tempfile.mkdtemp()

    def test_platform_commands(self):
        calls = []

        def popen(cmd, **kw):
            calls.append(cmd)

        core.open_in_file_manager(self.out, system="Linux", popen=popen)
        core.open_in_file_manager(Path(self.out), system="Darwin", popen=popen)
        self.assertEqual(calls, [["xdg-open", self.out], ["open", self.out]])
        started = []
        core.open_in_file_manager(self.out, system="Windows",
                                  popen=popen, startfile=started.append)
        self.assertEqual(started, [self.out])
        self.assertEqual(len(calls), 2)       # Windows never spawns a process

    def test_missing_tool_propagates(self):
        def popen(cmd, **kw):
            raise FileNotFoundError(cmd[0])

        with self.assertRaises(FileNotFoundError):
            core.open_in_file_manager(self.out, system="Linux", popen=popen)

    def test_missing_folder_raises_before_launching(self):
        calls = []
        with self.assertRaises(FileNotFoundError):
            core.open_in_file_manager(Path(self.out) / "nope", system="Linux",
                                      popen=lambda cmd, **kw: calls.append(cmd))
        self.assertEqual(calls, [])


class GuiWizardExtras(_GuiTestBase):
    """Language selector inside the wizard, bold help markup, localized docs
    popup with clickable sources, step-6 first/last mirror, step-7 eID card
    box, step-8 'open output folder'."""

    def test_language_switch_inside_wizard_keeps_state(self):
        import i18n
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        app = self.make_app()
        try:
            self.wizard_with_state(app, tpl, self.tmp / "o")   # lands on step 3
            self.assertEqual(app.step_index, 2)
            self.assertEqual(str(app._wizard_lang_menu.cget("state")), "normal")
            app._on_wizard_language_change("Français")
            app.update()
            self.assertEqual(i18n.get_language(), "fr")
            self.assertEqual(app._stepper_btns[0].cget("text"), "1. Modèle")
            self.assertEqual(app._wizard_lang_menu.get(), "Français")
            self.assertEqual(app.step_index, 2)                # same step
            self.assertEqual(app.template_path, tpl)           # state kept
            self.assertEqual(len(app.valid_paths), 1)
            self.assertEqual(len(app.valid_table.get_children()), 1)  # rebuilt
        finally:
            i18n.set_language("en")
            app.destroy()

    def test_language_switch_ignored_while_running(self):
        import i18n
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        app = self.make_app()
        try:
            self.wizard_with_state(app, tpl, self.tmp / "o")
            app._running = True                        # what _launch() sets
            app._refresh_chrome()
            self.assertEqual(str(app._wizard_lang_menu.cget("state")), "disabled")
            app._on_wizard_language_change("Français")
            app.update()
            self.assertEqual(i18n.get_language(), "en")
            self.assertEqual(app._stepper_btns[0].cget("text"), "1. Template")
            app._running = False
            app._refresh_chrome()
            self.assertEqual(str(app._wizard_lang_menu.cget("state")), "normal")
        finally:
            i18n.set_language("en")
            app.destroy()

    def test_help_panel_renders_bold_markup(self):
        from tkinter import font as tkfont

        from i18n import tr
        app = self.make_app()
        app._start_wizard()            # step-1 help carries a **bold** lead-in
        app.update()
        text = app._help_box.get("1.0", "end")
        self.assertNotIn("**", text)
        self.assertTrue(app._help_box.tag_ranges("bold"))
        self.assertIn(tr("step.template.help").replace("**", "")[:40], text)
        inner = app._help_box._textbox                 # the tag really is bold
        tag_font = inner.tag_cget("bold", "font")
        self.assertTrue(tag_font)
        self.assertEqual(tkfont.Font(font=tag_font).actual("weight"), "bold")
        body = tkfont.Font(font=inner.cget("font")).actual()
        self.assertEqual(tkfont.Font(font=tag_font).actual("size"), body["size"])
        app.destroy()

    def test_docs_popup_is_localized_with_links(self):
        import customtkinter as ctk

        import i18n
        from i18n import tr
        app = self.make_app()
        app._start_wizard()
        try:
            i18n.set_language("fr")
            app._show_docs_popup()
            app.update()
            win = app._docs_win
            self.assertTrue(win.winfo_exists())
            box = next(w for w in win.winfo_children()
                       if isinstance(w, ctk.CTkTextbox))
            text = box.get("1.0", "end")
            self.assertNotIn("**", text)
            self.assertIn(tr("docs.modes").replace("**", "")[:30], text)
            self.assertIn(tr("docs.src.eidas"), text)
            for i, (_, url) in enumerate(i18n.DOC_SOURCES):
                self.assertTrue(box.tag_ranges(f"src{i}"), url)
                self.assertIn(url, text)
            app._on_wizard_language_change("English")   # closes the popup
            app.update()
            self.assertFalse(win.winfo_exists())
        finally:
            i18n.set_language("en")
            app.destroy()

    def test_docs_source_click_opens_browser_and_popup_is_idempotent(self):
        import customtkinter as ctk

        import gui
        import i18n
        app = self.make_app()
        app._start_wizard()
        opened = []
        orig = gui.webbrowser.open
        gui.webbrowser.open = lambda url, *a, **k: opened.append(url)
        try:
            app._show_docs_popup()
            app.update()
            win = app._docs_win
            app._show_docs_popup()                     # second call: same window
            app.update()
            self.assertIs(app._docs_win, win)
            box = next(w for w in win.winfo_children()
                       if isinstance(w, ctk.CTkTextbox))
            inner = box._textbox
            start = box.tag_ranges("src0")[0]
            box.see(start)
            win.update()
            x, y, _w, h = inner.bbox(start)
            # tag bindings fire for the char under the Text's "current" mark,
            # which only pointer motion updates -> move there before clicking
            for seq in ("<Enter>", "<Motion>", "<Button-1>", "<ButtonRelease-1>"):
                inner.event_generate(seq, x=x + 2, y=y + h // 2)
                win.update()
            app.update()
            self.assertEqual(opened, [i18n.DOC_SOURCES[0][1]])
        finally:
            gui.webbrowser.open = orig
            app.destroy()

    def test_docs_popup_closed_on_finish_and_landing_language_change(self):
        import i18n
        app = self.make_app()
        try:
            app._start_wizard()
            app._show_docs_popup()
            app.update()
            win = app._docs_win
            app._finish()                              # back to the landing page
            app.update()
            self.assertFalse(win.winfo_exists())       # no orphan popup
            app._start_wizard()
            app._show_docs_popup()
            app.update()
            win = app._docs_win
            app._finish()
            app._on_language_change("Français")        # landing-page selector
            app.update()
            self.assertFalse(win.winfo_exists())
            app._start_wizard()
            app._show_docs_popup()                     # fresh, in French
            app.update()
            self.assertIsNot(app._docs_win, win)
        finally:
            i18n.set_language("en")
            app.destroy()

    def test_step6_mirrors_first_last_selector_only_on_mismatch(self):
        from i18n import tr
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842), (595, 842)])
        same = make_pdf(self.tmp / "same.pdf", [(595, 842), (595, 842)])
        short = make_pdf(self.tmp / "short.pdf", [(595, 842)])
        app = self.make_app()
        self.wizard_with_state(app, tpl, self.tmp / "o", inputs=[same])
        app._goto_step(5)
        app.update()
        self.assertIsNone(app.place_anchor_row)          # no mismatch: no row
        app.destroy()

        app = self.make_app()
        self.wizard_with_state(app, tpl, self.tmp / "o", inputs=[same, short])
        app._goto_step(5)
        app.update()
        self.assertTrue(app.place_anchor_row.winfo_manager())
        self.assertEqual(app.place_anchor_menu.get(), tr("anchor.opt_last"))
        self.assertIn("2/2", app.place_anchor_status.cget("text"))
        self.assertEqual(app.cur_page, 1)                # locked on last page
        app._on_anchor_menu(tr("anchor.opt_first"))      # the step-6 menu
        app.update()
        self.assertEqual(app._page_anchor(), "first")
        self.assertEqual(app.cur_page, 0)                # preview follows
        self.assertEqual(app.place_anchor_menu.get(), tr("anchor.opt_first"))
        self.assertEqual(len(app.valid_paths), 2)
        app.destroy()

    def test_step6_anchor_change_rejecting_everything_locks_next(self):
        import gui

        from i18n import tr
        # 1-page file: matches the template's LAST page only -> accepted on
        # "last", rejected on "first" -> 0 valid documents -> Next locks.
        tpl = make_pdf(self.tmp / "t.pdf", [(300, 300), (595, 842)])
        short = make_pdf(self.tmp / "short.pdf", [(595, 842)])
        app = self.make_app()
        self.wizard_with_state(app, tpl, self.tmp / "o", inputs=[short])
        app._goto_step(5)                  # beid: placement complete w/o click
        app.update()
        self.assertEqual(str(app._btn_next.cget("state")), "normal")
        app._on_anchor_menu(tr("anchor.opt_first"))
        app.update()
        self.assertEqual(app.valid_paths, [])
        self.assertIn("0/1", app.place_anchor_status.cget("text"))
        self.assertEqual(str(app._btn_next.cget("state")), "disabled")
        self.assertEqual(app._stepper_btns[2].cget("border_color"), gui._COL_ERROR)
        # the user is not stranded: the current chip stays enabled and
        # Previous still goes back (step 6 -> 5), even though step 3 is broken
        self.assertEqual(str(app._stepper_btns[5].cget("state")), "normal")
        self.assertEqual(str(app._btn_prev.cget("state")), "normal")
        app._nav_prev()
        app.update()
        self.assertEqual(app.step_index, 4)
        app._goto_step(6)                          # forward stays locked
        self.assertEqual(app.step_index, 4)
        app.destroy()

    def test_card_box_only_in_beid_mode(self):
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        app = self.make_app()
        self.wizard_with_state(app, tpl, self.tmp / "o")
        app.mode_var.set("beid")
        app._goto_step(6)
        app.update()
        self.assertTrue(app.card_box.winfo_manager())
        # image mode: step 6 needs an image + a position to be complete
        app.mode_var.set("image")
        app.image_path = make_png(self.tmp / "s.png")
        app.place_page, app.place_x, app.place_y = 1, 10.0, 10.0
        app._goto_step(6)
        app.update()
        self.assertEqual(app.step_index, 6)
        self.assertFalse(app.card_box.winfo_manager())
        # azure: no card involved, never shown
        app.mode_var.set("azure")
        app.azure_anchors_path = tpl               # any file satisfies _mode_error
        app._goto_step(6)
        app.update()
        self.assertEqual(app.step_index, 6)
        self.assertFalse(app.card_box.winfo_manager())
        # beid, coming back after a finished batch: not shown either
        app.mode_var.set("beid")
        app.run_results = [core.DocResult(tpl, self.tmp / "o" / "t_signe.pdf", True, "ok")]
        app._goto_step(6)
        app.update()
        self.assertFalse(app.card_box.winfo_manager())
        app.destroy()

    def test_card_box_hidden_on_launch_and_back_after_failure(self):
        import time
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        app = self.make_app()
        self.wizard_with_state(app, tpl, self.tmp / "o")
        app.mode_var.set("beid")
        app._goto_step(6)
        app.update()

        def boom(*a, **k):
            raise SystemExit("No card reader detected.")

        orig, core.process_batch = core.process_batch, boom
        try:
            app._launch()
            self.assertTrue(app._running)
            self.assertFalse(app.card_box.winfo_manager())   # hidden while running
            for _ in range(60):
                app.update()
                time.sleep(0.05)
                if not app._running:
                    break
            app.update()
        finally:
            core.process_batch = orig
        self.assertIn("reader", app.run_error)
        self.assertTrue(app.card_box.winfo_manager())        # back for a retry
        slaves = app.card_box.master.pack_slaves()          # …and above Start
        self.assertLess(slaves.index(app.card_box), slaves.index(app.launch_btn))
        app.destroy()

    def test_open_folder_button_uses_core_helper(self):
        tpl = make_pdf(self.tmp / "t.pdf", [(595, 842)])
        out = self.tmp / "o"
        app = self.make_app()
        self.wizard_with_state(app, tpl, out)
        app.run_results = [core.DocResult(tpl, out / "t_signe.pdf", True, "ok")]
        app._goto_step(7)
        app.update()
        self.assertEqual(app.step_index, 7)
        opened = []
        orig = core.open_in_file_manager
        core.open_in_file_manager = lambda p, **k: opened.append(Path(p))
        try:
            app.open_folder_btn.invoke()
            self.assertEqual(opened, [out])
            self.assertEqual(app.open_folder_lbl.cget("text"), "")

            def boom(p, **k):
                raise FileNotFoundError("xdg-open")

            core.open_in_file_manager = boom
            app.open_folder_btn.invoke()
            self.assertIn("xdg-open", app.open_folder_lbl.cget("text"))
            core.open_in_file_manager = lambda p, **k: opened.append(Path(p))
            app.open_folder_btn.invoke()               # retry succeeds -> cleared
            self.assertEqual(app.open_folder_lbl.cget("text"), "")
            app.output_dir = None                      # defensive guard
            opened.clear()
            app._open_output_folder()
            self.assertEqual(opened, [])
        finally:
            core.open_in_file_manager = orig
        app.destroy()


class HeadlessImport(unittest.TestCase):
    def test_core_imports_without_tkinter(self):
        code = "import sign_pdfs_beid, sys; print('tkinter' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(Path(__file__).parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
