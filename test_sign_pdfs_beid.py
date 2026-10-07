#!/usr/bin/env python3
"""Headless tests (no card, no tkinter) for Cachet.

Covers the added business logic: page dimension extraction, validation against
the template, image insertion, placement math, and CLI argument resolution.
Run with:  ./venv/bin/python -m unittest -v
"""

import dataclasses
import datetime
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from PIL import Image

import sign_pdfs_beid as core
import stamps as stamplib
from stamps import Stamp, StampError


def write_pdf_objects(path, objs) -> Path:
    """Writes a PDF (correct xref) from the bodies of its objects: `objs[0]`
    is object 1 and must be the catalog."""
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


def pdf_stream(data: bytes) -> bytes:
    """Body of a stream object holding `data`."""
    return b"<</Length %d>>\nstream\n" % len(data) + data + b"\nendstream"


def make_pdf(path, page_sizes, origin=(0, 0)) -> Path:
    """Writes a minimal but valid PDF (correct xref) with the given pages,
    each described by a (width, height) in points. Each page has a (empty)
    /Contents stream — required by pyHanko in order to stamp. `origin` is the
    lower-left corner of every MediaBox."""
    n = len(page_sizes)
    ox, oy = origin
    objs = [b"<</Type/Catalog/Pages 2 0 R>>"]                      # obj 1
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(f"<</Type/Pages/Kids[{kids}]/Count {n}>>".encode())  # obj 2
    for i, (w, h) in enumerate(page_sizes):
        objs.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[{ox} {oy} {ox + w} {oy + h}]"
            f"/Contents {4 + 2 * i} 0 R>>".encode()
        )
        objs.append(pdf_stream(b"q Q\n"))
    return write_pdf_objects(path, objs)


def make_png(path, size=(300, 120)) -> Path:
    Image.new("RGBA", size, (255, 0, 0, 128)).save(path)
    return Path(path)


def xobject_counts(pdf_path) -> list[int]:
    """Number of XObjects referenced by each page of a PDF: one per visual
    signature stamped on it (the signature vignette is an annotation, not a
    page XObject)."""
    # Lazy: test_stamps imports this module at import time.
    from test_stamps import page_xobjects, pdf_pages
    return [len(page_xobjects(page)) for page in pdf_pages(pdf_path)]


def text_stamp(text="Jane Doe", **kwargs) -> Stamp:
    """A text stamp; placed on page 1 at (50, 50) unless told otherwise."""
    if not {"page", "page_anchor", "all_pages"} & kwargs.keys():
        kwargs["page"] = 1
    kwargs.setdefault("x", 50.0)
    kwargs.setdefault("y", 50.0)
    return Stamp(kind="text", text=text, **kwargs)


_PROFILE_SANDBOX = None


def setUpModule():
    """Safety net: no test of this module can reach the real user profile,
    even one that builds `gui.CachetApp` without `_GuiTestBase` (whose
    per-test directories sit on top of this)."""
    global _PROFILE_SANDBOX
    root = tempfile.mkdtemp(prefix="cachet-tests-")
    _PROFILE_SANDBOX = mock.patch.dict(os.environ, {
        "CACHET_CONFIG_DIR": os.path.join(root, "cfg"),
        "CACHET_DATA_DIR": os.path.join(root, "data")})
    _PROFILE_SANDBOX.start()


def tearDownModule():
    _PROFILE_SANDBOX.stop()


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

    def test_two_anchors_both_checked(self):
        dims = core.page_dimensions(self.tpl)
        both = ("first", "last")
        # first page matches, last page differs -> rejected, naming the last page
        odd_last = make_pdf(self.p("odd_last.pdf"),
                            [(595, 842), (595, 842), (200, 200)])
        r = core.validate_against_template(dims, odd_last, anchors=both)
        self.assertFalse(r.ok)
        self.assertIn("last page", r.reason)
        # ... and the other way round names the first page
        odd_first = make_pdf(self.p("odd_first.pdf"),
                             [(200, 200), (595, 842), (595, 842)])
        r = core.validate_against_template(dims, odd_first, anchors=both)
        self.assertFalse(r.ok)
        self.assertIn("first page", r.reason)
        # both anchor pages match -> accepted
        more = make_pdf(self.p("more.pdf"), [(595, 842), (100, 100), (595, 842)])
        r = core.validate_against_template(dims, more, anchors=both)
        self.assertTrue(r.ok, r.reason)
        self.assertIn("first and last pages", r.reason)
        # the order of the collection does not matter
        self.assertEqual(
            core.validate_against_template(dims, more, anchors={"last", "first"}).reason,
            r.reason)

    def test_blocker_rejects_with_reason(self):
        dims = core.page_dimensions(self.tpl)
        fewer = make_pdf(self.p("fewer.pdf"), [(595, 842)])
        blocker = "signature #1 targets page 1"
        # even with a matching anchor: ONE non-anchored element is enough
        r = core.validate_against_template(dims, fewer, anchors=("last",), blocker=blocker)
        self.assertFalse(r.ok)
        self.assertIn("1 page(s), the template has 2", r.reason)
        self.assertIn(blocker, r.reason)
        self.assertIn("only first/last", r.reason)
        # a blocker never rejects a file with the template's page count
        same = make_pdf(self.p("same.pdf"), [(595, 842), (595, 842)])
        r = core.validate_against_template(dims, same, anchors=(), blocker=blocker)
        self.assertEqual((r.ok, r.reason), (True, ""))

    def test_single_anchor_reason_unchanged(self):
        dims = core.page_dimensions(self.tpl)
        fewer = make_pdf(self.p("fewer.pdf"), [(595, 842)])
        odd_last = make_pdf(self.p("odd_last.pdf"),
                            [(595, 842), (595, 842), (200, 200)])
        accepted = "1 page(s), the template has 2 — signed on the last page"
        rejected = ("3 page(s), the template has 2; last page 200.00×200.00 pt "
                    "≠ template 595.00×842.00 pt")
        # historical keyword and its generalisation give the very same strings
        for kwargs in ({"page_anchor": "last"}, {"anchors": ("last",)}):
            r = core.validate_against_template(dims, fewer, **kwargs)
            self.assertEqual((r.ok, r.reason), (True, accepted))
            r = core.validate_against_template(dims, odd_last, **kwargs)
            self.assertEqual((r.ok, r.reason), (False, rejected))
        # no anchor at all: the bare count message, as before
        r = core.validate_against_template(dims, fewer)
        self.assertEqual((r.ok, r.reason), (False, "1 page(s), the template has 2"))

    def test_anchors_keyword_overrides_page_anchor(self):
        dims = core.page_dimensions(self.tpl)
        odd_last = make_pdf(self.p("odd_last.pdf"),
                            [(595, 842), (595, 842), (200, 200)])
        # page_anchor alone would accept ("first" matches) ...
        self.assertTrue(
            core.validate_against_template(dims, odd_last, page_anchor="first").ok)
        # ... but anchors replaces it
        r = core.validate_against_template(dims, odd_last, page_anchor="first",
                                           anchors=("last",))
        self.assertFalse(r.ok)
        self.assertIn("last page", r.reason)
        # an EMPTY collection also replaces it: no anchored element at all
        r = core.validate_against_template(dims, odd_last, page_anchor="first", anchors=())
        self.assertEqual((r.ok, r.reason), (False, "3 page(s), the template has 2"))

    def test_one_page_document_must_match_both_anchor_pages(self):
        # its only page is the first AND the last one
        one = make_pdf(self.p("one.pdf"), [(595, 842)])
        both = ("first", "last")
        mixed = make_pdf(self.p("mixed.pdf"), [(595, 842), (300, 300)])
        r = core.validate_against_template(core.page_dimensions(mixed), one, anchors=both)
        self.assertFalse(r.ok)
        self.assertIn("last page", r.reason)
        self.assertTrue(core.validate_against_template(
            core.page_dimensions(mixed), one, anchors=("first",)).ok)
        r = core.validate_against_template(core.page_dimensions(self.tpl), one, anchors=both)
        self.assertTrue(r.ok, r.reason)
        self.assertIn("first and last pages", r.reason)


class ImageInsertion(TmpCase):
    @staticmethod
    def insert(src, dst, png, **placement) -> bool:
        """ONE image stamp through the image-mode path."""
        return core.apply_stamps_one(
            src, dst, [Stamp(kind="image", image_path=png, **placement)])

    @staticmethod
    def size_pt(image, **kwargs) -> tuple[float, float]:
        return stamplib.stamp_content(Stamp(kind="image", image_path=image, **kwargs))[1]

    def test_insert_preserves_pages_and_writes_output(self):
        src = make_pdf(self.p("src.pdf"), [(400, 600), (400, 600)])
        png = make_png(self.p("sig.png"))
        dst = self.p("out.pdf")
        self.insert(src, dst, png, page=2, x=50, y=60)
        self.assertTrue(dst.exists())
        self.assertGreater(dst.stat().st_size, src.stat().st_size)
        # the page count must be unchanged
        self.assertEqual(len(core.page_dimensions(dst)), 2)

    def test_image_size_keeps_aspect(self):
        png = make_png(self.p("s.png"), size=(300, 150))
        w, h = self.size_pt(png)
        self.assertEqual(w, stamplib.DEFAULT_IMAGE_WIDTH_PT)
        self.assertAlmostEqual(h, stamplib.DEFAULT_IMAGE_WIDTH_PT * 0.5)

    def test_page_out_of_range_clear_error(self):
        src = make_pdf(self.p("src.pdf"), [(400, 600), (400, 600)])
        png = make_png(self.p("sig.png"))
        with self.assertRaises(ValueError) as cm:
            self.insert(src, self.p("o.pdf"), png, page=6, x=10, y=10)
        self.assertIn("out of range", str(cm.exception))

    def test_inserted_image_has_no_black_border(self):
        # opaque light-gray image on a white page: a black border (StaticStampStyle's
        # default border_width=3) would produce a frame of black pixels.
        src = make_pdf(self.p("p.pdf"), [(300, 400)])
        png = self.p("s.png")
        Image.new("RGB", (160, 80), (210, 210, 210)).save(png)
        dst = self.p("o.pdf")
        self.insert(src, dst, png, page=1, x=60, y=160)
        img = core.render_page_image(dst, 0, px_width=300)
        if img is None:
            self.skipTest("pdftoppm (poppler) unavailable")
        px = img.convert("RGB").load()
        W, H = img.size
        black = sum(1 for yy in range(H) for xx in range(W)
                    if sum(px[xx, yy]) < 90)
        self.assertLess(black, 30, f"{black} (near-)black pixels -> border present")

    def test_page_target_selects_the_stamped_page(self):
        src = make_pdf(self.p("src.pdf"), [(400, 600)] * 3)
        png = make_png(self.p("sig.png"))
        for name, target, expected in (("last", {"page_anchor": "last"}, [0, 0, 1]),
                                       ("first", {"page_anchor": "first"}, [1, 0, 0]),
                                       ("2", {"page": 2}, [0, 1, 0])):
            dst = self.p(f"out{name}.pdf")
            self.assertIs(self.insert(src, dst, png, x=50.5, y=60, **target), False)
            self.assertEqual(xobject_counts(dst), expected)
        with self.assertRaises(ValueError) as cm:
            self.insert(src, self.p("o.pdf"), png, page=4, x=10, y=10)
        self.assertEqual(str(cm.exception),
                         "page 4 out of range (the document has 3 page(s))")
        self.assertFalse(self.p("o.pdf").exists())

    def test_image_size_follows_width_and_orientation(self):
        png = make_png(self.p("s.png"), size=(300, 150))
        self.assertEqual(self.size_pt(png, width_pt=80), (80, 40.0))
        # EXIF orientation is applied first: the size is the one that gets stamped
        jpg = self.p("rot.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6                                   # rotate 90° to display
        Image.new("RGB", (40, 20), (200, 0, 0)).save(jpg, exif=exif)
        self.assertEqual(self.size_pt(jpg), (stamplib.DEFAULT_IMAGE_WIDTH_PT,
                                             stamplib.DEFAULT_IMAGE_WIDTH_PT * 2))


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


class EffectiveStamps(TmpCase):
    """Pure helpers: the visual signatures of a run and what they require
    from template validation."""

    def _cfg(self, **kw):
        kw.setdefault("inputs", [Path("x.pdf")])
        kw.setdefault("output", Path("out"))
        return core.RunConfig(**kw)

    def test_legacy_image_becomes_one_stamp(self):
        png = self.p("s.png")
        cfg = self._cfg(mode="image", image_path=png, page=2, x=10, y=20)
        self.assertEqual(core.effective_stamps(cfg),
                         [Stamp(kind="image", image_path=png, page=2, x=10, y=20)])
        # unspecified page / position: page 1, (0, 0) — as before
        (s,) = core.effective_stamps(self._cfg(mode="image", image_path=png))
        self.assertEqual((s.page, s.page_anchor, s.all_pages, s.x, s.y),
                         (1, None, False, 0.0, 0.0))
        self.assertEqual(s.width_pt, stamplib.DEFAULT_IMAGE_WIDTH_PT)
        # an anchor replaces the page number
        (s,) = core.effective_stamps(
            self._cfg(mode="image", image_path=png, page_anchor="last", x=5, y=6))
        self.assertEqual((s.page, s.page_anchor, s.x, s.y), (None, "last", 5, 6))
        self.assertTrue(s.is_placed)

    def test_legacy_image_then_cfg_stamps_in_order(self):
        png = self.p("s.png")
        extra = [text_stamp("A"), text_stamp("B", all_pages=True)]
        cfg = self._cfg(mode="image", image_path=png, page=1, x=1, y=2, stamps=extra)
        got = core.effective_stamps(cfg)
        self.assertEqual([s.kind for s in got], ["image", "text", "text"])
        self.assertEqual(got[1:], extra)
        self.assertEqual(cfg.stamps, extra)                 # cfg is not mutated
        # without the legacy image: cfg.stamps alone (a copy, not the list itself)
        cfg = self._cfg(mode="image", stamps=extra)
        self.assertEqual(core.effective_stamps(cfg), extra)
        self.assertIsNot(core.effective_stamps(cfg), cfg.stamps)

    def test_beid_ignores_image_path(self):
        png = self.p("s.png")
        for mode in ("beid", "azure"):
            self.assertEqual(
                core.effective_stamps(self._cfg(mode=mode, image_path=png, page=1, x=1, y=2)),
                [])
            extra = [text_stamp()]
            self.assertEqual(
                core.effective_stamps(self._cfg(mode=mode, image_path=png, stamps=extra)),
                extra)

    def test_anchor_requirements_table(self):
        png = self.p("s.png")
        first = text_stamp(page_anchor="first")
        last = text_stamp(page_anchor="last")
        numbered = text_stamp(page=3)
        every = text_stamp(all_pages=True)
        rows = [
            ("all anchored", dict(mode="image", stamps=[last, first, last]),
             (("first", "last"), None)),
            ("legacy image anchored", dict(mode="image", image_path=png, page_anchor="last"),
             (("last",), None)),
            ("numbered stamp", dict(mode="image", stamps=[last, numbered]),
             (("last",), "signature #2 targets page 3")),
            ("legacy numbered image", dict(mode="image", image_path=png),
             ((), "signature #1 targets page 1")),
            ("all-pages stamp", dict(mode="image", stamps=[every, last]),
             (("last",), "signature #1 targets every page")),
            ("first blocker wins", dict(mode="image", stamps=[numbered, every]),
             ((), "signature #1 targets page 3")),
            ("no element", dict(mode="image"), ((), None)),
            ("beid default vignette", dict(mode="beid"),
             ((), "the vignette has no first/last page target")),
            ("beid default vignette + anchored stamp", dict(mode="beid", stamps=[last]),
             (("last",), "the vignette has no first/last page target")),
            ("beid numbered vignette", dict(mode="beid", page=2, x=1, y=2),
             ((), "the vignette targets page 2")),
            ("beid placed vignette, default page", dict(mode="azure", x=1, y=2),
             ((), "the vignette targets page 1")),
            ("beid anchored default vignette", dict(mode="beid", page_anchor="first"),
             (("first",), None)),
            ("beid page_anchor + anchored stamps",
             dict(mode="beid", page_anchor="last", x=1, y=2, stamps=[first, last]),
             (("first", "last"), None)),
            ("stamp blocker wins over the vignette",
             dict(mode="azure", stamps=[every]),
             ((), "signature #1 targets every page")),
        ]
        for label, kwargs, expected in rows:
            with self.subTest(label):
                self.assertEqual(core.anchor_requirements(self._cfg(**kwargs)), expected)


class BatchVisualStamps(TmpCase):
    """Image mode = visual signatures only: text and image stamps through the
    real process_batch (no card)."""

    def setUp(self):
        super().setUp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.p("out")

    def _cfg(self, inputs, stamp_list=(), **kw):
        return core.RunConfig(inputs=list(inputs), output=self.out, mode="image",
                              stamps=list(stamp_list), **kw)

    def test_text_image_and_all_pages(self):
        src = make_pdf(self.p("doc.pdf"), [(595, 842)] * 3)
        png = make_png(self.p("sig.png"))
        (res,) = core.process_batch(self._cfg([src], [
            text_stamp(page=1, x=360, y=120),
            Stamp(kind="image", image_path=png, page_anchor="last", x=60, y=700),
            text_stamp("JD", font="caveat", font_size=14, all_pages=True, x=540, y=20),
        ]))
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(res.output, self.out / "doc_signe.pdf")
        self.assertEqual(xobject_counts(res.output), [2, 1, 2])
        self.assertEqual(xobject_counts(src), [0, 0, 0])     # the input is untouched
        self.assertEqual(len(core.page_dimensions(res.output)), 3)

    def test_detail_wording(self):
        src = make_pdf(self.p("doc.pdf"), [(595, 842)] * 2)
        png = make_png(self.p("sig.png"))
        (res,) = core.process_batch(core.RunConfig(
            inputs=[src], output=self.out, mode="image",
            image_path=png, page=1, x=360, y=150))
        self.assertEqual(res.detail,
                         "1 visual signature(s) applied — image page 1 @ (360, 150)")
        (res,) = core.process_batch(self._cfg([src], [
            text_stamp(page=1, x=360, y=120),
            text_stamp("JD", all_pages=True, x=540, y=20),
        ]))
        self.assertIn("2 visual signature(s) applied", res.detail)
        self.assertEqual(
            res.detail,
            "2 visual signature(s) applied — text page 1 @ (360, 120); "
            "text all pages @ (540, 20)")

    def test_count_mismatch_numbered_rejected_with_reason(self):
        tpl = make_pdf(self.p("tpl.pdf"), [(595, 842), (595, 842)])
        short = make_pdf(self.p("short.pdf"), [(595, 842)])
        results = core.process_batch(
            self._cfg([short, tpl], [text_stamp(page=1)], template=tpl))
        by_name = {r.path.name: r for r in results}
        self.assertFalse(by_name["short.pdf"].ok)
        self.assertEqual(
            by_name["short.pdf"].detail,
            "rejected — 1 page(s), the template has 2; signature #1 targets page 1 "
            "(only first/last page targets accept a different page count)")
        self.assertFalse((self.out / "short_signe.pdf").exists())
        self.assertTrue(by_name["tpl.pdf"].ok)               # same page count: signed

    def test_count_mismatch_all_anchored_accepted(self):
        tpl = make_pdf(self.p("tpl.pdf"), [(595, 842), (595, 842)])
        short = make_pdf(self.p("short.pdf"), [(595, 842)])
        long_bad = make_pdf(self.p("long_bad.pdf"),            # first page differs
                            [(200, 200), (595, 842), (595, 842)])
        results = core.process_batch(self._cfg(
            [short, long_bad],
            [text_stamp(page_anchor="last"), text_stamp("JD", page_anchor="first", x=300)],
            template=tpl))
        by_name = {r.path.name: r for r in results}
        self.assertTrue(by_name["short.pdf"].ok, by_name["short.pdf"].detail)
        self.assertIn("last page", by_name["short.pdf"].detail)
        self.assertEqual(xobject_counts(self.out / "short_signe.pdf"), [2])
        self.assertFalse(by_name["long_bad.pdf"].ok)
        self.assertIn("first page", by_name["long_bad.pdf"].detail)
        self.assertFalse((self.out / "long_bad_signe.pdf").exists())

    def test_all_pages_rejects_count_mismatch(self):
        tpl = make_pdf(self.p("tpl.pdf"), [(595, 842), (595, 842)])
        longer = make_pdf(self.p("longer.pdf"), [(595, 842)] * 3)
        (res,) = core.process_batch(self._cfg(
            [longer], [text_stamp(page_anchor="last"), text_stamp(all_pages=True)],
            template=tpl))
        self.assertFalse(res.ok)
        self.assertIn("rejected", res.detail)
        self.assertIn("signature #2 targets every page", res.detail)
        self.assertFalse((self.out / "longer_signe.pdf").exists())
        # without a template there is nothing to reject: every page is stamped
        (res,) = core.process_batch(self._cfg([longer], [text_stamp(all_pages=True)]))
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(xobject_counts(res.output), [1, 1, 1])

    def test_placeholders_resolved_per_document(self):
        a = make_pdf(self.p("a.pdf"), [(595, 842)])
        b = make_pdf(self.p("b.pdf"), [(595, 842)])
        rendered = []
        real = stamplib.render_text_image

        def spy(text, *args, **kwargs):
            rendered.append(text)
            return real(text, *args, **kwargs)

        with mock.patch.object(stamplib, "render_text_image", spy):
            results = core.process_batch(
                self._cfg([a, b], [text_stamp("{filename} {date}"),
                                   text_stamp("static", y=200)]),
                today=datetime.date(2026, 10, 7))
        self.assertTrue(all(r.ok for r in results), [r.detail for r in results])
        self.assertEqual([t for t in rendered if t != "static"],
                         ["a 07/10/2026", "b 07/10/2026"])
        # static content is rendered ONCE for the whole batch
        self.assertEqual(rendered.count("static"), 1)
        # the run date is the injected one, whatever the day the suite runs
        del rendered[:]
        with mock.patch.object(stamplib, "render_text_image", spy):
            core.process_batch(self._cfg([a], [text_stamp("{date}")]),
                               today=datetime.date(2001, 2, 3))
        self.assertEqual(rendered, ["03/02/2001"])

    def test_unreadable_image_fails_document_not_batch(self):
        a = make_pdf(self.p("a.pdf"), [(595, 842)])
        b = make_pdf(self.p("b.pdf"), [(595, 842)])
        fake = self.p("fake.png")
        fake.write_text("not an image", encoding="utf-8")
        seen = []
        # a readable stamp FIRST: nothing of it may reach an output either
        results = core.process_batch(
            self._cfg([a, b], [text_stamp(),
                               Stamp(kind="image", image_path=fake, page=1, x=10, y=10)]),
            on_progress=seen.append)
        self.assertEqual(len(results), 2)                    # the batch went on
        self.assertEqual(seen, results)
        for res in results:
            self.assertFalse(res.ok)
            self.assertIsNone(res.output)
            self.assertTrue(res.detail.startswith("failed — cannot read image"), res.detail)
        self.assertEqual(list(self.out.glob("*.pdf")), [])   # no partial output

    def test_page_out_of_range_detail(self):
        one = make_pdf(self.p("one.pdf"), [(595, 842)])
        three = make_pdf(self.p("three.pdf"), [(595, 842)] * 3)
        results = core.process_batch(self._cfg([one, three], [text_stamp(page=3)]))
        self.assertFalse(results[0].ok)
        self.assertEqual(results[0].detail,
                         "failed — page 3 out of range (the document has 1 page(s))")
        self.assertFalse((self.out / "one_signe.pdf").exists())
        self.assertTrue(results[1].ok, results[1].detail)    # the next one is processed
        self.assertEqual(xobject_counts(results[1].output), [0, 0, 1])

    def test_never_overwrites(self):
        src = make_pdf(self.p("doc.pdf"), [(595, 842)])
        cfg = self._cfg([src], [text_stamp()])
        (first,) = core.process_batch(cfg)
        kept = first.output.read_bytes()
        (second,) = core.process_batch(cfg)
        self.assertEqual(first.output.name, "doc_signe.pdf")
        self.assertEqual(second.output.name, "doc_signe - 1.pdf")
        self.assertEqual(first.output.read_bytes(), kept)    # intact
        self.assertTrue(second.output.exists())

    def test_image_mode_without_stamp_fails_document(self):
        # programmatic misuse (validate_config was skipped): never a silent no-op
        a = make_pdf(self.p("a.pdf"), [(595, 842)])
        b = make_pdf(self.p("b.pdf"), [(595, 842)])
        results = core.process_batch(
            core.RunConfig(inputs=[a, b], output=self.out, mode="image"))
        self.assertEqual([(r.ok, r.output, r.detail) for r in results],
                         [(False, None, "failed — no visual signature to apply")] * 2)
        self.assertEqual(list(self.out.glob("*.pdf")), [])

    def test_blank_page_is_stamped(self):
        from test_stamps import strip_page_contents      # lazy: circular at import time
        src = strip_page_contents(make_pdf(self.p("doc.pdf"), [(595, 842)] * 2), 1)
        (res,) = core.process_batch(self._cfg([src], [text_stamp(all_pages=True)]))
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(xobject_counts(res.output), [1, 1])

    def test_malformed_form_is_still_stamped(self):
        # /AcroForm that is not a dictionary: the "already signed?" probe cannot
        # walk it. Image mode stamped such a file before and still does.
        from pyhanko.pdf_utils import generic
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        plain = make_pdf(self.p("plain.pdf"), [(595, 842)])
        src = self.p("odd_form.pdf")
        with plain.open("rb") as f:
            writer = IncrementalPdfFileWriter(f, strict=False)
            writer.root[generic.pdf_name("/AcroForm")] = generic.ArrayObject([])
            writer.update_root()
            with src.open("wb") as out:
                writer.write(out)
        (res,) = core.process_batch(self._cfg([src], [text_stamp()]))
        self.assertTrue(res.ok, res.detail)
        self.assertNotIn("WARNING", res.detail)
        self.assertEqual(xobject_counts(res.output), [1])


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

    def test_count_mismatch_needs_every_element_on_the_anchor(self):
        # a file whose page count differs from the template is signed only
        # when the vignette AND every visual signature target first/last
        tpl = make_pdf(self.p("t.pdf"), [(595, 842), (595, 842)])
        short = make_pdf(self.p("short.pdf"), [(595, 842)])
        base = dict(inputs=[short], output=self.p("o"), mode="beid", template=tpl,
                    stamps=[text_stamp(page_anchor="last")])
        calls, results = self._run(core.RunConfig(page_anchor="last", **base))
        self.assertTrue(results[0].ok, results[0].detail)
        ((_, kw),) = calls
        self.assertEqual(kw.get("page_index"), -1)         # the vignette on ITS last page
        self.assertEqual(kw["stamps"], base["stamps"])
        calls, results = self._run(core.RunConfig(**base))  # default vignette: not anchored
        self.assertFalse(results[0].ok)
        self.assertIn("the vignette has no first/last page target", results[0].detail)
        self.assertEqual(calls, [])                        # never signed

    def test_stamps_forwarded_to_sign_one(self):
        srcs = [make_pdf(self.p(f"d{i}.pdf"), [(595, 842)]) for i in range(2)]
        extra = [text_stamp("{filename}"), text_stamp("JD", all_pages=True)]
        cfg = core.RunConfig(inputs=srcs, output=self.p("o"), mode="beid", stamps=extra)
        calls, results = self._run(cfg)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(len(calls), 2)
        for _, kw in calls:
            self.assertEqual(kw["stamps"], core.effective_stamps(cfg))
            self.assertEqual(kw["stamps"], extra)
            self.assertIsInstance(kw["stamp_date"], datetime.date)
            self.assertIsInstance(kw["stamp_cache"], dict)
            self.assertIsNone(kw.get("pos"))               # the vignette is untouched
        # ONE cache and ONE date for the whole batch
        self.assertIs(calls[0][1]["stamp_cache"], calls[1][1]["stamp_cache"])
        self.assertEqual(calls[0][1]["stamp_date"], calls[1][1]["stamp_date"])

    def test_detail_mentions_visual_signatures(self):
        src = make_pdf(self.p("d.pdf"), [(595, 842), (595, 842)])
        extra = [text_stamp(), text_stamp("JD", all_pages=True)]
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid",
                             page=2, x=100, y=50, stamps=extra)
        _, results = self._run(cfg)
        self.assertIn("+ 2 visual signature(s)", results[0].detail)
        self.assertEqual(
            results[0].detail,
            "signed (eID) — vignette page 2 @ (100, 50) + 2 visual signature(s) "
            "— PAdES-B-LTA, LTV ok")
        # default vignette: the count follows the vignette phrase as well
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid",
                             stamps=extra[:1])
        _, results = self._run(cfg)
        self.assertEqual(
            results[0].detail,
            "signed (eID) — vignette + 1 visual signature(s) — PAdES-B-LTA, LTV ok")

    def test_no_stamps_detail_unchanged(self):
        src = make_pdf(self.p("d.pdf"), [(595, 842)])
        png = make_png(self.p("s.png"))
        # image_path is ignored in beid mode (as before): no stamp at all
        cfg = core.RunConfig(inputs=[src], output=self.p("o"), mode="beid",
                             image_path=png)
        calls, results = self._run(cfg)
        self.assertEqual(results[0].detail,
                         "signed (eID) — vignette — PAdES-B-LTA, LTV ok")
        self.assertEqual(calls[0][1]["stamps"], [])


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

    def test_default_vignette_rect(self):
        # pure geometry behind the default box, relative to the page corner
        self.assertEqual(
            core.default_vignette_rect(595, 842),
            (595 - core._STAMP_MARGIN - core._STAMP_W, core._STAMP_MARGIN,
             core._STAMP_W, core._STAMP_H))
        # a page too small for the box: clamped inside it, 4 pt from the edges
        x, y, w, h = core.default_vignette_rect(100, 30)
        self.assertEqual((x, y), (4, core._STAMP_MARGIN))
        self.assertEqual((x + w, y + h), (100 - core._STAMP_MARGIN, 30 - 4))

    def test_box_follows_mediabox_origin(self):
        # non-zero MediaBox origin: the page-relative rect, shifted by the origin
        with mock.patch.object(core, "page_mediabox",
                               lambda writer, page_index: [100.0, 200.0, 695.0, 1042.0]):
            box = core._default_vignette_box(None, -1)
        x, y, w, h = core.default_vignette_rect(595, 842)
        self.assertEqual(box, (100 + x, 200 + y, 100 + x + w, 200 + y + h))


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

    def test_image_mode_requires_a_signature(self):
        with self.assertRaises(ValueError) as cm:
            core.validate_config(core.RunConfig(inputs=[self.a], output=self.tmp,
                                                mode="image"))
        self.assertIn("--signatures", str(cm.exception))
        self.assertIn("--text", str(cm.exception))
        # the historical checks of the legacy image keep their messages
        with self.assertRaises(ValueError) as cm:
            core.validate_config(core.RunConfig(
                inputs=[self.a], output=self.tmp, mode="image",
                image_path=self.p("missing.png")))
        self.assertTrue(str(cm.exception).startswith("Image not found:"))
        with self.assertRaises(ValueError) as cm:
            core.validate_config(core.RunConfig(
                inputs=[self.a], output=self.tmp, mode="image",
                image_path=make_png(self.p("s.png")), page=0))
        self.assertEqual(str(cm.exception), "--page must be >= 1.")

    def test_bad_stamp_reported_with_index(self):
        good = text_stamp()
        for bad, fragment in (
            (text_stamp("   "), "text is empty"),
            (text_stamp(color="blue"), "bad color"),
            (text_stamp(font="no-such-font"), "unknown font"),
            (Stamp(kind="text", text="x", page=1), "x and y are required"),
            (Stamp(kind="image", image_path=self.p("nope.png"), page=1, x=0, y=0),
             "image not found"),
        ):
            with self.subTest(fragment):
                with self.assertRaises(ValueError) as cm:
                    core.validate_config(core.RunConfig(
                        inputs=[self.a], output=self.tmp, mode="image", stamps=[bad]))
                self.assertTrue(str(cm.exception).startswith("Signature #1: "),
                                cm.exception)
                self.assertIn(fragment, str(cm.exception))
        # the index is the position in effective_stamps (1-based)
        with self.assertRaises(ValueError) as cm:
            core.validate_config(core.RunConfig(
                inputs=[self.a], output=self.tmp, mode="image",
                stamps=[good, text_stamp("")]))
        self.assertEqual(str(cm.exception), "Signature #2: text is empty.")
        with self.assertRaises(ValueError) as cm:      # legacy image = #1, cfg.stamps follow
            core.validate_config(core.RunConfig(
                inputs=[self.a], output=self.tmp, mode="image",
                image_path=make_png(self.p("s.png")), stamps=[text_stamp("")]))
        self.assertEqual(str(cm.exception), "Signature #2: text is empty.")

    def test_oversized_text_rejected_by_validate_config(self):
        huge = text_stamp("W" * 300, font_size=400)
        with self.assertRaises(ValueError) as cm:
            core.validate_config(core.RunConfig(
                inputs=[self.a], output=self.tmp, mode="image", stamps=[huge]))
        self.assertTrue(
            str(cm.exception).startswith("Signature #1: text is too large to render"),
            cm.exception)

    def test_beid_accepts_stamps(self):
        for mode, extra in (("beid", {}), ("azure", {"azure_vault_url": "https://v.example"})):
            # stamps are optional in the cryptographic modes ...
            core.validate_config(core.RunConfig(
                inputs=[self.a], output=self.tmp, mode=mode, pades_level="b-b", **extra))
            # ... accepted when valid ...
            core.validate_config(core.RunConfig(
                inputs=[self.a], output=self.tmp, mode=mode, pades_level="b-b",
                stamps=[text_stamp(), text_stamp(all_pages=True)], **extra))
            # ... and validated like anywhere else
            with self.assertRaises(ValueError) as cm:
                core.validate_config(core.RunConfig(
                    inputs=[self.a], output=self.tmp, mode=mode, pades_level="b-b",
                    stamps=[text_stamp(x=None)], **extra))
            self.assertEqual(str(cm.exception), "Signature #1: x and y are required.")


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


class SignaturesCli(TmpCase):
    """CLI flags of the visual signatures (--text…, --signatures) through the
    real arg parser, and who owns --page/--x/--y."""

    def setUp(self):
        super().setUp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.a = make_pdf(self.p("a.pdf"), [(595, 842)])
        self.png = make_png(self.p("sig.png"))
        self.entries = [
            {"kind": "text", "text": "Jane Doe\nRead and approved, {date}",
             "font": "great-vibes", "color": "#1A2B8C", "font_size": 24,
             "page_anchor": "last", "x": 360, "y": 120},
            {"kind": "image", "image_path": "sig.png", "width_pt": 120,
             "page": 1, "x": 60, "y": 700},
        ]
        self.sigs = self.p("sigs.json")
        self.sigs.write_text(json.dumps(self.entries), encoding="utf-8")

    def parse(self, *extra):
        argv = ["--input", str(self.a), "--output", str(self.p("out")), *extra]
        return core.resolve_config(core.build_arg_parser().parse_args(argv))

    def assert_rejected(self, *extra) -> str:
        with self.assertRaises(ValueError) as cm:
            self.parse(*extra)
        return str(cm.exception)

    def test_text_flags_build_one_text_stamp(self):
        cfg = self.parse("--mode", "image", "--text", "Jane Doe", "--font", "caveat",
                         "--color", "#FF0000", "--font-size", "30",
                         "--page", "2", "--x", "10", "--y", "20.5")
        self.assertEqual(cfg.stamps, [Stamp(
            kind="text", text="Jane Doe", font="caveat", color="#FF0000",
            font_size=30.0, page=2, x=10.0, y=20.5)])
        self.assertEqual(core.effective_stamps(cfg), cfg.stamps)
        self.assertIsNone(cfg.image_path)

    def test_explicit_zero_font_size_rejected(self):
        # "is not None", not "or": 0 must not silently become the default size
        self.assertIn("font_size must be greater than 0", self.assert_rejected(
            "--mode", "image", "--text", "JD", "--font-size", "0"))

    def test_text_defaults(self):
        (s,) = self.parse("--mode", "image", "--text", "Jane Doe").stamps
        self.assertEqual(s, Stamp(
            kind="text", text="Jane Doe", font=stamplib.DEFAULT_FONT,
            color=stamplib.DEFAULT_COLOR, font_size=24.0, page=1, x=0.0, y=0.0))

    def test_text_newline_escape(self):
        # the two characters backslash + n, as typed in any shell
        (s,) = self.parse("--mode", "image", "--text", r"Jane Doe\nRead and approved").stamps
        self.assertEqual(s.text, "Jane Doe\nRead and approved")
        (s,) = self.parse("--mode", "image", "--text", "{date} {x} {}").stamps
        self.assertEqual(s.text, "{date} {x} {}")           # placeholders: not at parse time

    def test_text_with_page_last(self):
        cfg = self.parse("--mode", "image", "--text", "JD", "--page", "last",
                         "--x", "5", "--y", "6")
        (s,) = cfg.stamps
        self.assertEqual((s.page, s.page_anchor, s.x, s.y), (None, "last", 5.0, 6.0))
        self.assertEqual((cfg.page, cfg.page_anchor), (None, "last"))
        self.assertEqual(core.anchor_requirements(cfg), (("last",), None))

    def test_font_without_text_rejected(self):
        for flags in (("--font", "caveat"), ("--color", "#FF0000"), ("--font-size", "12")):
            for mode in (("--mode", "image", "--image-path", str(self.png)),
                         ("--mode", "image", "--signatures", str(self.sigs)),
                         ("--mode", "beid", "--pades-level", "b-b")):
                with self.subTest(flags=flags, mode=mode):
                    self.assertEqual(self.assert_rejected(*mode, *flags),
                                     "--font, --color and --font-size need --text.")

    def test_text_with_image_path_rejected(self):
        msg = self.assert_rejected("--mode", "image", "--text", "JD",
                                   "--image-path", str(self.png))
        self.assertIn("mutually exclusive", msg)
        self.assertIn("--signatures", msg)

    def test_text_in_beid_rejected(self):
        for mode in (("--mode", "beid"),
                     ("--mode", "azure", "--azure-vault-url", "https://v.example")):
            with self.subTest(mode=mode):
                msg = self.assert_rejected(*mode, "--pades-level", "b-b", "--text", "JD")
                self.assertIn("--text only applies to --mode image", msg)
                self.assertIn("--signatures", msg)

    def test_signatures_file_loaded(self):
        cfg = self.parse("--mode", "image", "--signatures", str(self.sigs))
        self.assertEqual(cfg.stamps, [
            Stamp(kind="text", text="Jane Doe\nRead and approved, {date}",
                  font="great-vibes", color="#1A2B8C", font_size=24.0,
                  page_anchor="last", x=360.0, y=120.0),
            # relative image_path: against the directory of the JSON file
            Stamp(kind="image", image_path=self.sigs.resolve().parent / "sig.png",
                  width_pt=120.0, page=1, x=60.0, y=700.0),
        ])
        self.assertIsNone(cfg.image_path)
        self.assertEqual((cfg.page, cfg.page_anchor, cfg.x, cfg.y), (None, None, None, None))
        # an empty list is not "a signature": image mode still needs one
        empty = self.p("empty.json")
        empty.write_text("[]", encoding="utf-8")
        self.assertIn("at least one visual signature",
                      self.assert_rejected("--mode", "image", "--signatures", str(empty)))

    def test_signatures_plus_text_convenience_first(self):
        cfg = self.parse("--mode", "image", "--signatures", str(self.sigs),
                         "--text", "JD", "--page", "2", "--x", "1", "--y", "2")
        self.assertEqual([s.kind for s in cfg.stamps], ["text", "text", "image"])
        self.assertEqual((cfg.stamps[0].text, cfg.stamps[0].page, cfg.stamps[0].x,
                          cfg.stamps[0].y), ("JD", 2, 1.0, 2.0))
        # --page/--x/--y went to the convenience stamp only
        self.assertEqual(cfg.stamps[1].page_anchor, "last")
        self.assertEqual((cfg.stamps[2].page, cfg.stamps[2].x), (1, 60.0))

    def test_signatures_plus_image_path_additive(self):
        cfg = self.parse("--mode", "image", "--signatures", str(self.sigs),
                         "--image-path", str(self.png), "--page", "last",
                         "--x", "7", "--y", "8")
        self.assertEqual(len(cfg.stamps), 2)                 # the file's entries
        got = core.effective_stamps(cfg)
        self.assertEqual([s.kind for s in got], ["image", "text", "image"])
        self.assertEqual((got[0].image_path, got[0].page_anchor, got[0].x, got[0].y,
                          got[0].width_pt), (self.png, "last", 7.0, 8.0, 150.0))
        self.assertEqual(got[1:], cfg.stamps)

    def test_page_xy_without_convenience_stamp_rejected(self):
        for flags in (("--page", "2"), ("--page", "last"), ("--x", "5"), ("--y", "0"),
                      ("--page", "1", "--x", "5", "--y", "6")):
            with self.subTest(flags=flags):
                msg = self.assert_rejected("--mode", "image", "--signatures",
                                           str(self.sigs), *flags)
                self.assertIn("--page/--x/--y need --text or --image-path", msg)
        # and with no signature at all the missing signature is what is reported
        self.assertIn("at least one visual signature",
                      self.assert_rejected("--mode", "image", "--page", "2"))

    def test_legacy_image_invocation_fields_unchanged(self):
        cfg = self.parse("--mode", "image", "--image-path", str(self.png),
                         "--page", "1", "--x", "360", "--y", "150")
        self.assertEqual((cfg.mode, cfg.image_path, cfg.page, cfg.page_anchor, cfg.x, cfg.y),
                         ("image", self.png, 1, None, 360.0, 150.0))
        self.assertEqual(cfg.stamps, [])
        self.assertEqual(core.effective_stamps(cfg), [Stamp(
            kind="image", image_path=self.png, page=1, x=360.0, y=150.0)])
        self.assertEqual(core.describe_stamps(cfg),
                         ["image page 1 @ (360, 150) — sig.png"])

    def test_beid_page_xy_stay_vignette_with_signatures(self):
        for mode in (("--mode", "beid"),
                     ("--mode", "azure", "--azure-vault-url", "https://v.example")):
            with self.subTest(mode=mode):
                cfg = self.parse(*mode, "--pades-level", "b-b", "--signatures",
                                 str(self.sigs), "--page", "2", "--x", "100", "--y", "50")
                self.assertEqual((cfg.page, cfg.page_anchor, cfg.x, cfg.y),
                                 (2, None, 100.0, 50.0))
                self.assertEqual(core.describe_placement(cfg),
                                 "vignette page 2 @ (100, 50)")
                # the file's entries, with their OWN placement
                self.assertEqual(core.effective_stamps(cfg), cfg.stamps)
                self.assertEqual([(s.kind, s.target_label()) for s in cfg.stamps],
                                 [("text", "last page"), ("image", "page 1")])
                # --image-path stays ignored there (as before)
                cfg = self.parse(*mode, "--pades-level", "b-b",
                                 "--image-path", str(self.png))
                self.assertEqual(core.effective_stamps(cfg), [])

    def test_bad_signatures_file_is_value_error(self):
        bad_json = self.p("bad.json")
        bad_json.write_text("[{", encoding="utf-8")
        not_a_list = self.p("obj.json")
        not_a_list.write_text('{"kind": "text"}', encoding="utf-8")
        bad_entry = self.p("entry.json")
        bad_entry.write_text(json.dumps([self.entries[0], {"kind": "text", "text": ""}]),
                             encoding="utf-8")
        for path, fragment in ((bad_json, "invalid JSON"),
                               (not_a_list, "expected a JSON list"),
                               (self.p("missing.json"), "cannot read signatures file"),
                               (bad_entry, "entry.json: signature #2: text is empty")):
            for mode in (("--mode", "image"), ("--mode", "beid", "--pades-level", "b-b")):
                with self.subTest(path=path.name, mode=mode):
                    with self.assertRaises(ValueError) as cm:   # what main() turns into exit 2
                        self.parse(*mode, "--signatures", str(path))
                    self.assertIsInstance(cm.exception, StampError)
                    self.assertIn(fragment, str(cm.exception))

    def test_profile_is_never_read(self):
        # A GUI profile full of signatures in the user folder: a headless run
        # must not pick any of it up (deterministic CLI).
        import profile_store
        cfg_dir, data_dir = self.p("cfg"), self.p("data")
        store = profile_store.ProfileStore(config_dir=cfg_dir, data_dir=data_dir)
        store.save(profile_store.Profile(signatures=[profile_store.Signature(
            id=profile_store.new_id(), label="Jane", stamp=text_stamp(all_pages=True),
            placed_on=(595.0, 842.0))]))
        self.assertEqual(len(store.load().signatures), 1)    # the profile is real
        before = store.profile_path.read_bytes()
        env = {profile_store.ENV_CONFIG_DIR: str(cfg_dir),
               profile_store.ENV_DATA_DIR: str(data_dir)}
        with mock.patch.dict(os.environ, env):
            beid = self.parse("--pades-level", "b-b")
            image = self.parse("--mode", "image", "--image-path", str(self.png))
            self.assertEqual(beid.stamps, [])
            self.assertEqual(core.effective_stamps(beid), [])
            self.assertEqual(image.stamps, [])
            self.assertIn("at least one visual signature",
                          self.assert_rejected("--mode", "image"))
        self.assertEqual(store.profile_path.read_bytes(), before)


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

    # --- visual signatures + cryptographic signature -------------------------
    # Real core.sign_one with the in-memory signer of this class: only the
    # PKCS#11 hardware is replaced, the stamping + signing + validation path
    # is the production one.

    def _sign_one(self, src, dst, *, field="Signature1", level="b-b", stamps=()) -> Path:
        networked = level != "b-b"
        core.sign_one(self.signer, src, dst, field, level,
                      core.CardIdentity("Jane Doe", None),
                      timestamper=self.timestamper if networked else None,
                      validation_context=self.signing_vc if networked else None,
                      stamps=stamps)
        return dst

    def _statuses(self, pdf) -> list:
        """pyHanko validation status of EVERY signature of `pdf`, in order
        (verify_signed_pdf only looks at the last one)."""
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext

        with Path(pdf).open("rb") as f:
            reader = PdfFileReader(f, strict=False)
            return [validate_pdf_signature(
                        emb, ValidationContext(trust_roots=[self.root]))
                    for emb in reader.embedded_regular_signatures]

    def assert_covers_whole_file(self, status):
        from pyhanko.sign.validation.status import (
            ModificationLevel, SignatureCoverageLevel)
        self.assertTrue(status.bottom_line, status.summary())
        self.assertEqual(status.coverage, SignatureCoverageLevel.ENTIRE_FILE)
        self.assertEqual(status.modification_level, ModificationLevel.NONE)

    def _batch_beid(self, cfg):
        """process_batch in beid mode with the card replaced by this class's
        signer: ONLY the session, the signer factory and the identity read are
        mocked — sign_one, the stamps and the verification are real."""
        with mock.patch.object(core, "open_eid_session", lambda *a, **k: object()), \
             mock.patch.object(core, "BEIDSigner", lambda session: self.signer), \
             mock.patch.object(core, "read_card_identity",
                               lambda session: core.CardIdentity("Jane Doe", None)):
            return core.process_batch(cfg)

    def test_stamps_then_signature_validates_unmodified(self):
        from test_stamps import pdf_pages            # lazy: circular at import time
        src = make_pdf(self.p("doc.pdf"), [(595, 842)] * 3)
        png = make_png(self.p("sig.png"))
        dst = self._sign_one(src, self.p("doc_signed.pdf"), stamps=[
            text_stamp("JD {date}", all_pages=True, x=540, y=20),
            Stamp(kind="image", image_path=png, page=1, x=60, y=700),
        ])
        self.assertEqual(core.verify_signed_pdf(dst, "b-b", trust_anchors=[self.root]),
                         "PAdES-B-B")
        (status,) = self._statuses(dst)
        self.assert_covers_whole_file(status)        # the signature covers the stamps
        self.assertEqual(xobject_counts(dst), [2, 1, 1])
        pages = pdf_pages(dst)
        self.assertEqual(len(pages[-1]["/Annots"]), 1)   # the vignette, last page only
        self.assertNotIn("/Annots", pages[0])
        self.assertNotIn("/Annots", pages[1])

    def test_stamps_then_b_lta(self):
        src = make_pdf(self.p("doc.pdf"), [(595, 842)] * 2)
        dst = self._sign_one(src, self.p("doc_signed.pdf"), level="b-lta", stamps=[
            text_stamp(all_pages=True), text_stamp("JD", page_anchor="last", y=200)])
        self.assertEqual(self._verify(dst, "b-lta"), "PAdES-B-LTA, LTV ok")
        self.assertEqual(xobject_counts(dst), [1, 2])

    def test_stamp_after_signature_is_flagged(self):
        # Negative control: a stamp added AFTER the signature is an
        # illegal modification — which is why sign_one stamps first.
        signed = self._sign_one(make_pdf(self.p("doc.pdf"), [(595, 842)]),
                                self.p("signed.pdf"))
        self.assertEqual(core.verify_signed_pdf(signed, "b-b", trust_anchors=[self.root]),
                         "PAdES-B-B")
        out = self.p("restamped.pdf")
        self.assertIs(core.apply_stamps_one(signed, out, [text_stamp()]), True)
        self.assertTrue(out.exists())
        # (pyHanko logs the offending objects of its diff analysis: captured here)
        with self.assertLogs("pyhanko", level="WARNING"), \
                self.assertRaises(core.SelfVerificationError):
            core.verify_signed_pdf(out, "b-b", trust_anchors=[self.root])
        # an unsigned input is not reported as signed
        plain = make_pdf(self.p("plain.pdf"), [(595, 842)])
        self.assertIs(core.apply_stamps_one(plain, self.p("plain_out.pdf"), [text_stamp()]),
                      False)

    def test_existing_signature_count(self):
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter

        def count(pdf) -> int:
            with Path(pdf).open("rb") as f:
                return core.existing_signature_count(
                    IncrementalPdfFileWriter(f, strict=False))

        plain = make_pdf(self.p("plain.pdf"), [(595, 842)])
        self.assertEqual(count(plain), 0)
        self.assertEqual(count(self._sign_one(plain, self.p("bb.pdf"))), 1)
        # b-lta = the signature + its archival document timestamp
        self.assertEqual(count(self._sign_one(plain, self.p("lta.pdf"), level="b-lta")), 2)

    def test_stamps_on_already_signed_input_refused(self):
        first = self._sign_one(make_pdf(self.p("doc.pdf"), [(595, 842)]),
                               self.p("first.pdf"), field="Other1")
        dst = self.p("second.pdf")
        signer = mock.Mock()                         # must never be asked to sign
        with self.assertRaises(StampError) as cm:
            core.sign_one(signer, first, dst, "Signature1", "b-b",
                          core.CardIdentity("Jane Doe", None), stamps=[text_stamp()])
        self.assertIn("already signed", str(cm.exception))
        self.assertEqual(signer.mock_calls, [])
        self.assertFalse(dst.exists())
        # the same holds for an input that only carries a document timestamp chain
        lta = self._sign_one(make_pdf(self.p("doc2.pdf"), [(595, 842)]),
                             self.p("lta.pdf"), field="Other1", level="b-lta")
        with self.assertRaises(StampError):
            self._sign_one(lta, dst, stamps=[text_stamp()])
        self.assertFalse(dst.exists())
        (status,) = self._statuses(first)            # the input is intact
        self.assert_covers_whole_file(status)

    def test_countersign_without_stamps_keeps_first_signature_valid(self):
        from pyhanko.sign.validation.status import ModificationLevel
        first = self._sign_one(make_pdf(self.p("doc.pdf"), [(595, 842)]),
                               self.p("first.pdf"), field="Other1")
        both = self._sign_one(first, self.p("both.pdf"), stamps=())
        earlier, ours = self._statuses(both)
        self.assertTrue(earlier.bottom_line, earlier.summary())
        self.assertEqual(earlier.modification_level, ModificationLevel.FORM_FILLING)
        self.assert_covers_whole_file(ours)

    def test_image_mode_on_signed_input_warns(self):
        signed = self._sign_one(make_pdf(self.p("doc.pdf"), [(595, 842)]),
                                self.p("signed.pdf"))
        plain = make_pdf(self.p("plain.pdf"), [(595, 842)])
        out = self.p("out")
        results = core.process_batch(core.RunConfig(
            inputs=[signed, plain], output=out, mode="image", stamps=[text_stamp()]))
        by_name = {r.path.name: r for r in results}
        # historical outcome kept: the output is written, the document is "ok"
        self.assertTrue(by_name["signed.pdf"].ok)
        self.assertTrue((out / "signed_signe.pdf").exists())
        self.assertIn("WARNING: the document was already signed",
                      by_name["signed.pdf"].detail)
        self.assertTrue(by_name["signed.pdf"].detail.startswith(
            "1 visual signature(s) applied — text page 1 @ (50, 50) — WARNING"))
        self.assertTrue(by_name["plain.pdf"].ok)
        self.assertNotIn("WARNING", by_name["plain.pdf"].detail)

    def test_batch_beid_refuses_stamps_on_signed_input(self):
        signed = self._sign_one(make_pdf(self.p("doc.pdf"), [(595, 842)]),
                                self.p("signed.pdf"), field="Other1")
        plain = make_pdf(self.p("plain.pdf"), [(595, 842)])
        out = self.p("out")
        results = self._batch_beid(core.RunConfig(
            inputs=[signed, plain], output=out, mode="beid", pades_level="b-b",
            stamps=[text_stamp()]))
        by_name = {r.path.name: r for r in results}
        refused = by_name["signed.pdf"]
        self.assertFalse(refused.ok)
        self.assertIsNone(refused.output)
        self.assertTrue(
            refused.detail.startswith("failed — the document is already signed"),
            refused.detail)
        self.assertFalse((out / "signed_signe.pdf").exists())
        # the batch went on: the unsigned input is stamped and signed
        self.assertTrue(by_name["plain.pdf"].ok, by_name["plain.pdf"].detail)
        (status,) = self._statuses(out / "plain_signe.pdf")
        self.assert_covers_whole_file(status)
        # without stamps the very same input is countersigned, as before
        (res,) = self._batch_beid(core.RunConfig(
            inputs=[signed], output=out, mode="beid", pades_level="b-b"))
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(len(self._statuses(res.output)), 2)

    def test_blank_page_stamped_then_signed(self):
        from test_stamps import strip_page_contents  # lazy: circular at import time
        src = strip_page_contents(make_pdf(self.p("doc.pdf"), [(595, 842)] * 2), 1)
        dst = self._sign_one(src, self.p("doc_signed.pdf"),
                             stamps=[text_stamp(all_pages=True)])
        (status,) = self._statuses(dst)
        self.assert_covers_whole_file(status)
        self.assertEqual(xobject_counts(dst), [1, 1])

    def test_process_batch_beid_with_stamps_real_signature(self):
        src = make_pdf(self.p("doc.pdf"), [(595, 842)] * 2)
        png = make_png(self.p("sig.png"))
        out = self.p("out")
        (res,) = self._batch_beid(core.RunConfig(
            inputs=[src], output=out, mode="beid", pades_level="b-b",
            page=2, x=100, y=50,
            stamps=[text_stamp("{filename}", all_pages=True, x=540, y=20),
                    Stamp(kind="image", image_path=png, page_anchor="first", x=60, y=700)]))
        self.assertTrue(res.ok, res.detail)
        self.assertEqual(
            res.detail,
            "signed (eID) — vignette page 2 @ (100, 50) + 2 visual signature(s) — PAdES-B-B")
        self.assertEqual(res.output, out / "doc_signe.pdf")
        self.assertEqual(core.verify_signed_pdf(res.output, "b-b", trust_anchors=[self.root]),
                         "PAdES-B-B")
        (status,) = self._statuses(res.output)
        self.assert_covers_whole_file(status)
        self.assertEqual(xobject_counts(res.output), [2, 1])

    def test_failing_stamp_leaves_no_signed_output(self):
        one = make_pdf(self.p("one.pdf"), [(595, 842)])
        out = self.p("out")
        signer = mock.Mock()                         # must never be asked to sign
        with mock.patch.object(core, "open_eid_session", lambda *a, **k: object()), \
             mock.patch.object(core, "BEIDSigner", lambda session: signer), \
             mock.patch.object(core, "read_card_identity",
                               lambda session: core.CardIdentity("Jane Doe", None)):
            (res,) = core.process_batch(core.RunConfig(
                inputs=[one], output=out, mode="beid", pades_level="b-b",
                stamps=[text_stamp(), text_stamp(page=9)]))
        self.assertFalse(res.ok)
        self.assertIsNone(res.output)
        self.assertEqual(res.detail,
                         "failed — page 9 out of range (the document has 1 page(s))")
        self.assertEqual(signer.mock_calls, [])
        self.assertEqual(list(out.glob("*")), [])    # no _signe.pdf, nothing partial

    def test_sign_one_resolves_placeholders(self):
        # {filename} is the source STEM, {date} the run date handed to sign_one
        src = make_pdf(self.p("contract.pdf"), [(595, 842)])
        rendered = []
        real = stamplib.render_text_image

        def spy(text, *args, **kwargs):
            rendered.append(text)
            return real(text, *args, **kwargs)

        with mock.patch.object(stamplib, "render_text_image", spy):
            core.sign_one(self.signer, src, self.p("out.pdf"), "Signature1", "b-b",
                          core.CardIdentity("Jane Doe", None),
                          stamps=[text_stamp("{filename} {date}")],
                          stamp_date=datetime.date(2001, 2, 3))
        self.assertEqual(rendered, ["contract 03/02/2001"])


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


class CliEndToEnd(TmpCase):
    """The real CLI in a subprocess: visual signatures end to end (image
    mode: no card, no network), exit codes included."""

    def setUp(self):
        super().setUp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.doc = make_pdf(self.p("doc.pdf"), [(595, 842)] * 2)
        self.png = make_png(self.p("sig.png"))
        self.out = self.p("out")

    def run_cli(self, *argv, encoding="utf-8"):
        # The CLI never reads the GUI profile; the directories are pinned to
        # temp ones anyway so that nothing can reach the real user folder.
        env = dict(os.environ, PYTHONIOENCODING=encoding,
                   CACHET_CONFIG_DIR=str(self.p("cfg")),
                   CACHET_DATA_DIR=str(self.p("data")))
        proc = subprocess.run(
            [sys.executable, "sign_pdfs_beid.py", *argv],
            capture_output=True, text=True, encoding=encoding, env=env,
            cwd=str(Path(__file__).parent), timeout=120)
        self.assertFalse(self.p("cfg").exists())             # no profile was created
        self.assertFalse(self.p("data").exists())
        return proc

    def test_text_mode_exit_0_and_output(self):
        proc = self.run_cli(
            "--mode", "image", "--input", str(self.doc), "--output", str(self.out),
            "--text", r"Jane Doe\nSigned on {date}", "--font", "caveat",
            "--color", "#0A6B3C", "--font-size", "18",
            "--page", "last", "--x", "360", "--y", "120")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(xobject_counts(self.out / "doc_signe.pdf"), [0, 1])
        self.assertIn("Mode: visual signatures — 1 element(s):", proc.stdout)
        self.assertIn('  1. text last page @ (360, 120) — "Jane Doe"', proc.stdout)
        self.assertIn("1 visual signature(s) applied — text last page @ (360, 120)",
                      proc.stdout)
        self.assertIn("1/1 document(s) processed successfully", proc.stdout)

    def test_text_outside_the_stdout_encoding(self):
        # redirected output on Windows is the ANSI code page, strict: a name
        # it cannot encode must not kill the run before any document
        proc = self.run_cli(
            "--mode", "image", "--input", str(self.doc), "--output", str(self.out),
            "--text", "Ayşe Yılmaz", "--font", "lato", encoding="cp1252")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(xobject_counts(self.out / "doc_signe.pdf"), [1, 0])
        self.assertIn(r'"Ay\u015fe Y\u0131lmaz"', proc.stdout)      # escaped, not fatal
        self.assertIn("1/1 document(s) processed successfully", proc.stdout)

    def test_signatures_file_exit_0(self):
        sigs = self.p("sigs.json")
        sigs.write_text(json.dumps([
            {"kind": "text", "text": "JD", "font": "caveat", "font_size": 14,
             "all_pages": True, "x": 540, "y": 20},
            {"kind": "image", "image_path": "sig.png", "width_pt": 120,
             "page": 1, "x": 60, "y": 700},
            {"kind": "text", "text": "skipped", "enabled": False,
             "page": 1, "x": 0, "y": 0},
        ]), encoding="utf-8")
        proc = self.run_cli(
            "--mode", "image", "--input", str(self.doc), "--output", str(self.out),
            "--signatures", str(sigs))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(xobject_counts(self.out / "doc_signe.pdf"), [2, 1])
        self.assertIn("Mode: visual signatures — 2 element(s):", proc.stdout)
        self.assertIn("2 visual signature(s) applied", proc.stdout)

    def test_legacy_image_invocation_exit_0(self):
        # the invocation documented since 1.x, template validation included
        other = make_pdf(self.p("other.pdf"), [(595, 842)] * 2)
        proc = self.run_cli(
            "--mode", "image", "--template", str(self.doc),
            "--input", str(self.doc), str(other), "--output", str(self.out),
            "--image-path", str(self.png), "--page", "1", "--x", "360", "--y", "150")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for name in ("doc_signe.pdf", "other_signe.pdf"):
            self.assertEqual(xobject_counts(self.out / name), [1, 0])
        self.assertIn("1 visual signature(s) applied — image page 1 @ (360, 150)",
                      proc.stdout)
        self.assertIn("2/2 document(s) processed successfully", proc.stdout)

    def test_bad_signatures_file_exit_2(self):
        sigs = self.p("sigs.json")
        sigs.write_text(json.dumps([
            {"kind": "text", "text": "ok", "page": 1, "x": 0, "y": 0},
            {"kind": "text", "text": "no position", "page": 1},
        ]), encoding="utf-8")
        proc = self.run_cli(
            "--mode", "image", "--input", str(self.doc), "--output", str(self.out),
            "--signatures", str(sigs))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)   # argparse error
        self.assertIn("error: sigs.json: signature #2: x and y are required", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertFalse(self.out.exists())                  # nothing was started

    def test_rejected_document_exit_2(self):
        short = make_pdf(self.p("short.pdf"), [(595, 842)])
        proc = self.run_cli(
            "--mode", "image", "--template", str(self.doc),
            "--input", str(self.doc), str(short), "--output", str(self.out),
            "--text", "JD", "--page", "1", "--x", "10", "--y", "10")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("rejected — 1 page(s), the template has 2; signature #1 targets "
                      "page 1 (only first/last page targets accept a different page count)",
                      proc.stdout)
        self.assertTrue((self.out / "doc_signe.pdf").exists())      # the valid one is done
        self.assertFalse((self.out / "short_signe.pdf").exists())
        self.assertIn("1/2 document(s) processed successfully", proc.stdout)

    def test_template_note_only_when_every_element_is_anchored(self):
        longer = make_pdf(self.p("longer.pdf"), [(595, 842)] * 3)
        common = ("--mode", "image", "--template", str(self.doc),
                  "--input", str(longer), "--output", str(self.out))
        last = {"kind": "text", "text": "JD", "page_anchor": "last", "x": 10, "y": 10}
        first = dict(last, page_anchor="first")
        numbered = {"kind": "text", "text": "JD", "page": 1, "x": 10, "y": 10}

        def run_with(*entries):
            sigs = self.p("sigs.json")
            sigs.write_text(json.dumps(list(entries)), encoding="utf-8")
            return self.run_cli(*common, "--signatures", str(sigs))

        proc = self.run_cli(*common, "--text", "JD", "--page", "last",
                            "--x", "10", "--y", "10")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("accepted and signed on their last page.", proc.stdout)
        self.assertEqual(xobject_counts(self.out / "longer_signe.pdf"), [0, 0, 1])
        proc = run_with(last, first)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("accepted and signed on their first and last pages.", proc.stdout)
        self.assertEqual(xobject_counts(self.out / "longer_signe - 1.pdf"), [1, 0, 1])
        # ONE numbered target blocks page-count mismatches: no such promise
        proc = run_with(last, numbered)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertNotIn("accepted and signed", proc.stdout)
        self.assertIn("signature #2 targets page 1", proc.stdout)

    def test_beid_banner_lists_visual_signatures(self):
        # No card is faked: the run stops at the PKCS#11 library load (a dummy
        # file), AFTER the banner that announces what would be applied.
        sigs = self.p("sigs.json")
        sigs.write_text(json.dumps([
            {"kind": "text", "text": "JD", "all_pages": True, "x": 540, "y": 20},
        ]), encoding="utf-8")
        lib = self.p("dummy.so")
        lib.write_bytes(b"")
        proc = self.run_cli(
            "--mode", "beid", "--pades-level", "b-b", "--lib", str(lib),
            "--input", str(self.doc), "--output", str(self.out),
            "--signatures", str(sigs))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("Cannot load the PKCS#11 library", proc.stderr)
        self.assertIn("Mode: eID — vignette bottom-right, last page "
                      "+ 1 visual signature(s). PKCS#11 lib:", proc.stdout)
        self.assertIn('  1. text all pages @ (540, 20) — "JD"', proc.stdout)
        self.assertFalse(self.out.exists())                  # nothing was signed


class _GuiTestBase(unittest.TestCase):
    """Shared display-guarded setup for the wizard tests."""

    def setUp(self):
        import types
        try:
            import tkinter
            tkinter.Tk().destroy()      # detects the absence of a display
            import gui                   # noqa: F401 - requires customtkinter+tk
        except Exception as exc:  # noqa: BLE001
            # A run that is SUPPOSED to exercise the GUI must not turn a
            # broken gui.py or a missing display into a green "all skipped".
            if os.environ.get("CACHET_REQUIRE_GUI") == "1":
                self.fail("GUI tests are required (CACHET_REQUIRE_GUI=1) but "
                          f"unavailable: {exc!r}")
            self.skipTest(f"tkinter/GUI unavailable: {exc}")
        # Cyclic GC stays off while a GUI test runs. Step widgets are rebuilt
        # constantly, which leaves CTkFont objects in reference cycles; when
        # the collector happens to run in the batch WORKER thread, each
        # Font.__del__ is a Tk call from a non-main thread that waits a full
        # second for a mainloop these tests never enter (they pump update()),
        # and the batch then outlives the test's wait loop. Which thread
        # collects depends on allocation counts, i.e. on unrelated code.
        gc.disable()
        self.addCleanup(gc.enable)
        self.tmp = Path(tempfile.mkdtemp())
        # The GUI loads and auto-saves a user profile: each test gets its own
        # (never the real one), so tests stay independent of each other.
        env = mock.patch.dict(os.environ, {
            "CACHET_CONFIG_DIR": str(self.tmp / "cfg"),
            "CACHET_DATA_DIR": str(self.tmp / "data")})
        env.start()
        self.addCleanup(env.stop)
        self.types = types

    def make_app(self, store=None):
        import contextlib
        import tkinter

        import gui
        app = gui.CachetApp(self.types.SimpleNamespace(lib=None), store=store)
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
        app._add_image_signature(png)   # before step 6 is built; it is selected
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
    """Placement step in beid mode: the vignette heads the element list, a
    3:1 box (1/5 page); click → target-page sync, canvas that follows the
    window."""

    def test_beid_placeholder_and_resize(self):
        import gui
        tpl = make_pdf(self.tmp / "t.pdf", [(600, 800), (600, 800)])
        app = self.make_app()
        app.geometry("1500x950")
        app.update()
        self.wizard_with_state(app, tpl, self.tmp)
        app.mode_var.set("beid")
        app._goto_step(5)               # placement step
        app.update()
        # beid mode: the vignette is the first element of the list
        self.assertIn(gui.VIGNETTE_ID, app._element_rows)
        # placeholder = 3:1 vignette, width = page/5
        iw, ih = app._element_size_pt(gui.VIGNETTE_ID)
        self.assertAlmostEqual(iw, 600 / 5)
        self.assertAlmostEqual(iw / ih, 3.0)
        # a click sets the position (in beid mode too, not just image) and
        # syncs the manual target-page field
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * 0.5, y=oy + fh * 0.5))
        self.assertIsNotNone(app.profile.settings.vignette.x)
        self.assertEqual(app.page_text, "1")
        big = app.canvas.winfo_reqwidth()
        # shrink the window -> the canvas shrinks, proportions preserved
        # (both sizes are above the width where the canvas hits its floor)
        app.geometry("1000x680")
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
        app._add_image_signature(png)
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
        # image mode: step 6 needs a placed visual signature to be complete
        app.mode_var.set("image")
        sig = app._add_image_signature(make_png(self.tmp / "s.png"))
        sig.stamp = dataclasses.replace(sig.stamp, page=1, x=10.0, y=10.0)
        sig.placed_on = app.template_dims[0]
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


class GuiTopBar(_GuiTestBase):
    """Top bar shared by landing + wizard: brand (logo + name) on the left,
    support link (Stripe payment page) right of the language selector."""

    def test_brand_and_support_link_on_both_screens(self):
        import gui
        from i18n import tr
        app = self.make_app()                      # landing page
        opened = []
        orig = gui.webbrowser.open
        gui.webbrowser.open = lambda u, *a, **k: opened.append(u)
        try:
            self.assertEqual(app._brand_name_lbl.cget("text"), "Cachet")
            self.assertIsNotNone(app._brand_logo_lbl)   # logo.png in checkout
            self.assertEqual(app._support_btn.cget("text"), tr("support.button"))
            app._support_btn.invoke()
            self.assertEqual(
                opened, ["https://donate.stripe.com/4gM8wJ6qbgfO7U342n6oo02"])
            app._start_wizard()                    # wizard top bar too
            app.update()
            self.assertEqual(app._brand_name_lbl.cget("text"), "Cachet")
            app._support_btn.invoke()
            self.assertEqual(len(opened), 2)
        finally:
            gui.webbrowser.open = orig
        app.destroy()

    def test_logo_loads_and_degrades_gracefully(self):
        import gui
        logo = gui._load_logo()
        self.assertIsNotNone(logo)
        self.assertEqual(logo.cget("size")[1], gui._LOGO_SIZE)
        self.assertIsNot(gui._load_logo(), logo)   # fresh per call (root-bound)
        # missing file -> no logo, no crash (the brand shows the name alone)
        orig_cache, gui._logo_pil = gui._logo_pil, None
        orig_path = gui._asset_path
        gui._asset_path = lambda name: self.tmp / "missing" / name
        try:
            self.assertIsNone(gui._load_logo())
            app = self.make_app()
            self.assertIsNone(app._brand_logo_lbl)
            self.assertEqual(app._brand_name_lbl.cget("text"), "Cachet")
            app.destroy()
        finally:
            gui._asset_path = orig_path
            gui._logo_pil = orig_cache


class _GuiEditorBase(_GuiTestBase):
    """Shared fixtures of the step-6 editor and persistence tests: a 2-page
    template, an app parked on the placement step, synthetic clicks. Native
    pickers are always patched and dialogs are driven through their handlers
    — nothing here blocks."""

    def setUp(self):
        super().setUp()
        import gui
        import i18n
        self.gui, self.i18n, self.tr = gui, i18n, i18n.tr
        self.addCleanup(i18n.set_language, "en")
        self.tpl = make_pdf(self.tmp / "t.pdf", [(595, 842), (595, 842)])
        self.out = self.tmp / "out"

    def app_at_place(self, mode="image", inputs=None):
        """A fresh app on the placement step (step 6) in `mode`."""
        app = self.make_app()
        self.wizard_with_state(app, self.tpl, self.out, inputs=inputs)
        app.mode_var.set(mode)
        app._goto_step(5)
        app.update()
        self.assertEqual(app.step_index, 5)
        return app

    def pump(self, app, until=lambda: False, rounds=120):
        """Pump the Tk loop (these tests never enter mainloop) until
        `until()` holds, for at most ~rounds × 50 ms."""
        import time
        for _ in range(rounds):
            app.update()
            time.sleep(0.05)
            if until():
                break
        app.update()

    def click(self, app, fx=0.5, fy=0.5):
        """Click on the page preview, at a fraction of the page frame."""
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * fx, y=oy + fh * fy))
        app.update()

    def add_text(self, app, text="Jane", label=None, **stamp_kw):
        """An (unplaced) text signature straight into the library."""
        return app._add_signature(Stamp(kind="text", text=text, **stamp_kw),
                                  label or text)

    @staticmethod
    def fill(entry, value):
        entry.delete(0, "end")
        entry.insert(0, value)

    @staticmethod
    def type_text(app, text):
        """Replace the text of the text dialog, as typing does (each key
        release refreshes the preview)."""
        app.dlg_text_box.delete("1.0", "end")
        app.dlg_text_box.insert("1.0", text)
        app._on_text_dialog_edit()

    def draw(self, app, *strokes):
        """Freehand strokes on the draw dialog, through its mouse handlers."""
        event = self.types.SimpleNamespace
        for points in strokes:
            x, y = points[0]
            app._draw_start(event(x=x, y=y))
            for x, y in points[1:]:
                app._draw_move(event(x=x, y=y))

    @staticmethod
    def items(app, element_id, kind=None):
        """Canvas items of one element (optionally of one canvas type)."""
        return [i for i in app.canvas.find_withtag(f"el:{element_id}")
                if kind is None or app.canvas.type(i) == kind]

    @staticmethod
    def saved(app) -> dict:
        """The profile as it is on disk right now ({} if never saved)."""
        path = app.store.profile_path
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @staticmethod
    def modal_button(app, text):
        """A button of the open confirmation modal (its last child row)."""
        import customtkinter as ctk
        top = [w for w in app.winfo_children() if isinstance(w, ctk.CTkToplevel)][-1]
        return next(b for b in top.winfo_children()[-1].winfo_children()
                    if isinstance(b, ctk.CTkButton) and b.cget("text") == text)

    def launch_capturing_config(self, app) -> core.RunConfig:
        """Start the batch from the signing step with `process_batch`
        replaced: returns the RunConfig the GUI built."""
        seen = []
        with mock.patch.object(core, "process_batch",
                               side_effect=lambda cfg, **_k: seen.append(cfg) or []):
            app._launch()
            self.pump(app, lambda: not app._running, rounds=60)
        (cfg,) = seen
        return cfg


class GuiSignatureEditor(_GuiEditorBase):
    """Step 6 as a multi-element editor: the element list (vignette + the
    library of visual signatures), the three dialogs (text, drawing, image),
    click-to-place of the SELECTED element, the to-scale preview of every
    element, the step predicates, the anchor lock and the launched config."""

    def test_vignette_row_only_in_crypto_modes(self):
        app = self.app_at_place("beid")
        self.assertIn(self.gui.VIGNETTE_ID, app._element_rows)
        self.assertEqual(app.selected_id, self.gui.VIGNETTE_ID)
        app.mode_var.set("image")
        app._goto_step(5)
        self.assertNotIn(self.gui.VIGNETTE_ID, app._element_rows)
        self.assertIsNone(app.selected_id)         # empty library: nothing to select
        app.mode_var.set("azure")
        app.azure_anchors_path = self.tpl          # completes step 5 in azure mode
        app._goto_step(5)
        self.assertIn(self.gui.VIGNETTE_ID, app._element_rows)

    # ---------------------------------------------------------- text dialog
    def test_add_text_dialog_creates_selected_signature(self):
        app = self.app_at_place()
        app.add_text_btn.invoke()
        app.update()
        dialog = app._dialog
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(dialog.title(), self.tr("sig.text_title_add"))
        self.type_text(app, "Jane Doe\nRead and approved")
        app.dlg_save_btn.invoke()
        app.update()
        (sig,) = app.profile.signatures
        # default font, colour and size; not placed yet
        self.assertEqual(sig.stamp, Stamp(kind="text", text="Jane Doe\nRead and approved"))
        self.assertEqual(sig.label, "Jane Doe")    # first line of the text
        self.assertEqual(app.selected_id, sig.id)
        self.assertIn(sig.id, app._element_rows)
        self.assertIsNone(app._dialog)
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual([s["text"] for s in self.saved(app)["signatures"]],
                         ["Jane Doe\nRead and approved"])
        self.assertEqual(app._place_error(), self.tr("place.unplaced", name="Jane Doe"))
        # a second one, with an explicit name
        app._open_text_dialog()
        self.fill(app.dlg_name_entry, "  Initials ")
        self.type_text(app, "JD")
        app._apply_text_dialog()
        self.assertEqual([s.label for s in app.profile.signatures],
                         ["Jane Doe", "Initials"])
        self.assertEqual(len(self.saved(app)["signatures"]), 2)

    def test_text_dialog_validation_errors(self):
        app = self.app_at_place()
        app._open_text_dialog()
        app.update()
        blank = app._blank_ctk_image()
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")   # untouched: no error yet
        app._apply_text_dialog()                   # no text at all
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_text_empty"))
        app._update_text_preview()                 # a refresh keeps it after a failed save
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_text_empty"))
        self.type_text(app, " \n ")                # blank text
        app._apply_text_dialog()
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_text_empty"))
        self.type_text(app, "Jane")
        for bad in ("abc", "0", "-3", "401", "nan", ""):
            with self.subTest(size=bad):
                self.fill(app.dlg_size_entry, bad)
                app._apply_text_dialog()
                self.assertEqual(app.dlg_error_lbl.cget("text"),
                                 self.tr("sig.err_size", max=400))
                self.assertTrue(app._dialog.winfo_exists())    # stays open
        self.assertEqual(app.profile.signatures, [])           # nothing was added
        # A valid preview followed by an error: the previous picture must go
        # (CTkLabel.configure(image=None) would leave it on screen) ...
        self.fill(app.dlg_size_entry, "24")
        app._update_text_preview()
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")
        self.assertIsNot(app.dlg_preview_lbl._cachet_img, blank)
        self.fill(app.dlg_size_entry, "abc")
        app._update_text_preview()
        self.assertIs(app.dlg_preview_lbl._cachet_img, blank)
        self.assertIs(app.dlg_preview_lbl.cget("image"), blank)
        # ... and the next valid keystroke renders again, also once the
        # dropped picture has really been freed (TclError "image doesn't
        # exist" otherwise).
        gc.collect()
        app.update()
        self.fill(app.dlg_size_entry, "30")
        app._update_text_preview()
        app.update()
        self.assertIsNot(app.dlg_preview_lbl._cachet_img, blank)
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")

    def test_oversized_text_refused_and_not_persisted(self):
        import time
        app = self.app_at_place()
        app._open_text_dialog()
        self.fill(app.dlg_size_entry, "400")
        start = time.monotonic()
        self.type_text(app, "W" * 300)             # ~500 MP at 300 dpi
        self.assertLess(time.monotonic() - start, 2.0)     # refused by measuring alone
        refusal = app.dlg_error_lbl.cget("text")
        self.assertTrue(refusal.startswith(self.tr("sig.err_render", error="")), refusal)
        self.assertIn("too large", refusal)
        self.assertIs(app.dlg_preview_lbl.cget("image"), app._blank_ctk_image())
        app._apply_text_dialog()
        self.assertTrue(app._dialog.winfo_exists())        # kept open
        self.assertEqual(app.dlg_error_lbl.cget("text"), refusal)
        self.assertEqual(app.profile.signatures, [])
        self.assertEqual(self.saved(app).get("signatures", []), [])

    def test_text_box_does_not_wrap(self):
        # The stamp breaks lines only at real newlines, so the box must not
        # wrap a long line visually: it scrolls sideways instead. (CTkTextbox
        # 5.2.2 has no cget("wrap"): the behaviour itself is asserted.)
        app = self.app_at_place()
        app._open_text_dialog()
        self.type_text(app, "A sentence typed without ever pressing Enter. " * 8)
        self.pump(app, rounds=3)
        first, last = app.dlg_text_box.xview()
        self.assertEqual(first, 0.0)
        self.assertLess(last, 1.0)                 # part of the line is off to the right

    def test_preview_shows_size_on_page(self):
        app = self.app_at_place()
        app._open_text_dialog()
        self.type_text(app, "Jane Doe")
        at_24 = app.dlg_size_lbl.cget("text")
        self.assertRegex(at_24, r"\d+ × \d+ pt")
        self.fill(app.dlg_size_entry, "48")
        app._update_text_preview()
        # the size ON THE PAGE (what the stamping code gives), not the
        # size of the scaled-down preview
        _img, (w_pt, h_pt) = stamplib.stamp_content(
            Stamp(kind="text", text="Jane Doe", font_size=48.0))
        self.assertEqual(app.dlg_size_lbl.cget("text"),
                         self.tr("sig.preview_size", w=round(w_pt), h=round(h_pt)))
        self.assertNotEqual(app.dlg_size_lbl.cget("text"), at_24)
        # a line wider than the page: the preview is scaled down to its box,
        # the size line still tells the truth
        wide = "A sentence typed without ever pressing Enter. " * 3
        self.type_text(app, wide)
        _img, (w_pt, h_pt) = stamplib.stamp_content(
            Stamp(kind="text", text=wide, font_size=48.0))
        self.assertGreater(w_pt, 595)
        width, height = app.dlg_preview_lbl.cget("image").cget("size")
        self.assertTrue(width <= 500 and height <= 120, (width, height))
        self.assertEqual(app.dlg_size_lbl.cget("text"),
                         self.tr("sig.preview_size", w=round(w_pt), h=round(h_pt)))
        self.fill(app.dlg_size_entry, "abc")
        app._update_text_preview()
        self.assertEqual(app.dlg_size_lbl.cget("text"), "")

    def test_picker_after_dialog_closed_is_harmless(self):
        # the dialog can be closed while a native picker is open
        app = self.app_at_place()
        font = Path(shutil.copy(stamplib.resolve_font("caveat"), self.tmp / "hand.ttf"))

        def closing_picker(value):
            def picker(*_a, **_k):
                app._close_dialog()
                return value
            return picker

        app._open_text_dialog()
        with mock.patch.object(self.gui.filedialog, "askopenfilename",
                               side_effect=closing_picker(str(font))):
            app._on_dialog_font(self.tr("sig.font_custom"))
        self.assertIsNone(app._dialog)
        for opener in (app._open_text_dialog, app._open_draw_dialog):
            opener()
            with mock.patch.object(self.gui.colorchooser, "askcolor",
                                   side_effect=closing_picker(((255, 0, 0), "#ff0000"))):
                app._pick_dialog_color()
            self.assertIsNone(app._dialog)
        app.update()
        self.assertEqual(app.profile.signatures, [])

    def test_text_dialog_colour_and_custom_font(self):
        app = self.app_at_place()
        font = Path(shutil.copy(stamplib.resolve_font("caveat"), self.tmp / "My Hand.ttf"))
        custom = self.tr("sig.font_custom")
        app._open_text_dialog()
        self.type_text(app, "Jane")
        self.assertEqual(app.dlg_font_menu.cget("values"),
                         [name for _id, name in stamplib.font_choices()] + [custom])
        with mock.patch.object(self.gui.colorchooser, "askcolor",
                               return_value=((255, 0, 0), "#ff0000")) as ask:
            app.dlg_color_btn.invoke()
        self.assertIs(ask.call_args.kwargs["parent"], app._dialog)   # not behind the grab
        self.assertEqual(app.dlg_color_btn.cget("text"), "#FF0000")
        self.assertEqual(app.dlg_color_btn.cget("fg_color"), "#FF0000")
        with mock.patch.object(self.gui.colorchooser, "askcolor",
                               return_value=(None, None)):            # cancelled
            app._pick_dialog_color()
        self.assertEqual(app._dialog_state["color"], "#FF0000")
        with mock.patch.object(self.gui.filedialog, "askopenfilename",
                               return_value=str(font)) as ask:
            app._on_dialog_font(custom)
        self.assertIs(ask.call_args.kwargs["parent"], app._dialog)
        self.assertEqual(app.dlg_font_menu.get(), "My Hand.ttf")
        with mock.patch.object(self.gui.filedialog, "askopenfilename", return_value=""):
            app.dlg_font_menu.set(custom)          # what picking the entry shows first
            app._on_dialog_font(custom)            # cancelled: back to the font in use
        self.assertEqual(app.dlg_font_menu.get(), "My Hand.ttf")
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")
        app._apply_text_dialog()
        (sig,) = app.profile.signatures
        self.assertEqual((sig.stamp.font, sig.stamp.color), (str(font), "#FF0000"))
        thumb, _size = app._content(sig)           # rendered with that colour
        self.assertEqual([c for _n, c in thumb.convert("RGB").getcolors()], [(255, 0, 0)])
        # editing it shows the custom font's file name; the menu goes back
        # to a bundled font
        app._edit_selected()
        self.assertEqual(app.dlg_font_menu.get(), "My Hand.ttf")
        self.assertEqual(app.dlg_color_btn.cget("text"), "#FF0000")
        app._on_dialog_font("Lato")
        app._apply_text_dialog()
        self.assertEqual(sig.stamp.font, "lato")

    def test_text_preview_renders(self):
        app = self.app_at_place()
        app._open_text_dialog()
        app.update()
        blank = app._blank_ctk_image()
        self.assertIs(app.dlg_preview_lbl.cget("image"), blank)    # nothing typed yet
        # ... and no reproach for it: not before the user typed or tried to save
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")
        app._on_dialog_font("Lato")                # another refresh, still untouched
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")
        self.type_text(app, "")                    # typed, then emptied: now it is said
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_text_empty"))
        app._on_dialog_font("Great Vibes")
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_text_empty"))
        self.type_text(app, "Jane Doe\n{date} {filename}")
        app.update()
        shown = app.dlg_preview_lbl.cget("image")
        self.assertIsNot(shown, blank)
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")
        width, height = shown.cget("size")
        self.assertTrue(0 < width <= 500 and 0 < height <= 120, (width, height))
        # the preview IS the stamping code's output, for a sample document
        with mock.patch.object(self.gui.stamplib, "stamp_content",
                               wraps=stamplib.stamp_content) as spy:
            self.type_text(app, "JD")
        self.assertEqual(spy.call_args.args[0],
                         Stamp(kind="text", text="JD"))
        self.assertEqual(spy.call_args.kwargs["filename"], "t")
        self.assertIsNot(app.dlg_preview_lbl.cget("image"), shown)  # a fresh picture
        # whatever the renderer raises becomes the dialog's message
        with mock.patch.object(self.gui.stamplib, "stamp_content",
                               side_effect=MemoryError()):
            self.type_text(app, "Jane")
        self.assertEqual(app.dlg_error_lbl.cget("text"),
                         self.tr("sig.err_render", error="MemoryError"))
        self.assertIs(app.dlg_preview_lbl.cget("image"), blank)

    def test_edit_text_keeps_placement(self):
        app = self.app_at_place()
        sig = self.add_text(app, "Jane", label="My signature")
        app._turn_page(1)
        self.click(app, 0.3, 0.6)
        placement = (sig.stamp.page, sig.stamp.x, sig.stamp.y, sig.placed_on)
        size_before = app._element_size_pt(sig.id)
        app.edit_btn.invoke()
        app.update()
        self.assertEqual(app._dialog.title(), self.tr("sig.text_title_edit"))
        self.assertEqual(app.dlg_name_entry.get(), "My signature")     # seeded
        self.assertEqual(app.dlg_text_box.get("1.0", "end-1c"), "Jane")
        self.assertEqual(app.dlg_size_entry.get(), "24")
        self.assertEqual(app.dlg_font_menu.get(), "Great Vibes")
        self.type_text(app, "Jane Doe")
        self.fill(app.dlg_size_entry, "36,5")      # decimal comma
        app._on_dialog_font("Caveat")
        app._apply_text_dialog()
        self.assertIsNone(app._dialog)
        self.assertEqual([s.id for s in app.profile.signatures], [sig.id])  # in place
        self.assertEqual((sig.stamp.text, sig.stamp.font, sig.stamp.font_size),
                         ("Jane Doe", "caveat", 36.5))
        self.assertEqual((sig.stamp.page, sig.stamp.x, sig.stamp.y, sig.placed_on),
                         placement)
        self.assertEqual(placement[0], 2)
        self.assertTrue(app._sig_placed(sig))
        self.assertEqual(sig.label, "My signature")
        self.assertNotEqual(app._element_size_pt(sig.id), size_before)  # re-rendered
        saved = self.saved(app)["signatures"][0]
        self.assertEqual((saved["text"], saved["font_size"], saved["page"]),
                         ("Jane Doe", 36.5, 2))
        # an emptied name falls back on the first non-empty line (40 chars)
        app._edit_selected()
        self.fill(app.dlg_name_entry, "")
        self.type_text(app, "\n  " + "x" * 50 + "\nsecond line")
        app._apply_text_dialog()
        self.assertEqual(sig.label, "x" * 40)

    # ------------------------------------------------------- image signatures
    def test_add_image_copies_into_store(self):
        app = self.app_at_place()
        png = make_png(self.tmp / "s.png")
        with mock.patch.object(self.gui.filedialog, "askopenfilename",
                               return_value=str(png)):
            app.add_image_btn.invoke()
        (sig,) = app.profile.signatures
        self.assertEqual(sig.label, "s")
        self.assertEqual(app.selected_id, sig.id)
        self.assertTrue(app.store.is_store_file(sig.stamp.image_path))
        self.assertTrue(str(sig.stamp.image_path).startswith(str(self.tmp)))
        self.assertEqual(self.saved(app)["signatures"][0]["image_path"],
                         sig.stamp.image_path.name)
        # the stored COPY is used: the original may go away
        png.unlink()
        app._preview_cache.clear()
        app._scaled_cache.clear()
        self.assertIsNotNone(app._content(sig))
        self.click(app)
        self.assertEqual(len(self.items(app, sig.id, "image")), 1)
        with mock.patch.object(self.gui.filedialog, "askopenfilename", return_value=""):
            app._pick_image_signature()            # cancelled picker: nothing happens
        self.assertEqual(len(app.profile.signatures), 1)

    def test_unreadable_image_reports_error(self):
        app = self.make_app()
        self.wizard_with_state(app, self.tpl, self.out)
        app.mode_var.set("image")
        bad = self.tmp / "bad.png"
        bad.write_text("not an image")
        self.assertIsNone(app._add_image_signature(bad))   # step 6 is not built yet
        self.assertEqual(app.profile.signatures, [])
        notice = app._element_notice
        self.assertTrue(notice.startswith(self.tr("sig.err_image", error="")), notice)
        app._goto_step(5)
        app._update_place_labels()
        self.assertEqual(app.place_warn_lbl.cget("text"), notice)
        app._save_profile()                        # a successful save does not erase it
        app._update_place_labels()
        self.assertEqual(app.place_warn_lbl.cget("text"), notice)
        sig = self.add_text(app)                   # (a new operation clears it ...)
        app._element_notice = notice
        app._select(sig.id)                        # ... and so does selecting
        self.assertIsNone(app._element_notice)
        self.assertNotEqual(app.place_warn_lbl.cget("text"), notice)

    def test_image_store_failure_adds_nothing(self):
        # no fallback to the original file when the copy cannot be made
        app = self.app_at_place()
        with mock.patch.object(app.store, "import_image", side_effect=OSError("disk full")):
            self.assertIsNone(app._add_image_signature(make_png(self.tmp / "s.png")))
        self.assertEqual(app.profile.signatures, [])
        self.assertEqual(app._element_notice, self.tr("sig.err_store", error="disk full"))
        self.assertIsNone(app._profile_error)      # not a profile-save failure
        self.assertEqual(app.place_warn_lbl.cget("text"), app._element_notice)

    def test_edit_image_width(self):
        app = self.app_at_place()
        sig = app._add_image_signature(make_png(self.tmp / "s.png"))   # 300×120 px
        self.click(app)
        placement = (sig.stamp.page, sig.stamp.x, sig.stamp.y, sig.placed_on)
        app.edit_btn.invoke()
        app.update()
        self.assertEqual(app._dialog.title(), self.tr("sig.image_title"))
        self.assertEqual((app.dlg_name_entry.get(), app.dlg_width_entry.get()),
                         ("s", "150"))
        self.assertIsNot(app.dlg_preview_lbl.cget("image"), app._blank_ctk_image())
        for bad in ("abc", "0", "5001", ""):
            with self.subTest(width=bad):
                self.fill(app.dlg_width_entry, bad)
                app._apply_image_dialog()
                self.assertEqual(app.dlg_error_lbl.cget("text"),
                                 self.tr("sig.err_width", max=5000))
                self.assertTrue(app._dialog.winfo_exists())
        self.assertEqual(sig.stamp.width_pt, 150.0)
        self.fill(app.dlg_width_entry, "60,5")
        self.fill(app.dlg_name_entry, "Company stamp")
        app.dlg_save_btn.invoke()
        self.assertIsNone(app._dialog)
        self.assertEqual((sig.stamp.width_pt, sig.label), (60.5, "Company stamp"))
        self.assertEqual((sig.stamp.page, sig.stamp.x, sig.stamp.y, sig.placed_on),
                         placement)
        width, height = app._element_size_pt(sig.id)       # the preview follows
        self.assertAlmostEqual(width, 60.5)
        self.assertAlmostEqual(height, 60.5 * 120 / 300)
        self.assertEqual(self.saved(app)["signatures"][0]["width_pt"], 60.5)
        # stored file gone: no preview, the reason, and the dialog still opens
        sig.stamp.image_path.unlink()
        app._preview_cache.clear()
        app._scaled_cache.clear()
        app._edit_selected()
        self.assertIs(app.dlg_preview_lbl.cget("image"), app._blank_ctk_image())
        self.assertEqual(app.dlg_error_lbl.cget("text"),
                         self.tr("sig.err_image", error=app._sig_error(sig)))

    # ----------------------------------------------------------- draw dialog
    def test_draw_dialog_creates_transparent_png(self):
        app = self.app_at_place()
        app.draw_btn.invoke()
        app.update()
        self.assertEqual(app._dialog.title(), self.tr("sig.draw_title"))
        self.draw(app, [(100, 100), (200, 60), (400, 140)], [(250, 200)])  # a line, a dot
        self.assertEqual(len(app._dialog_state["strokes"]), 2)
        self.assertEqual(len(app.dlg_draw_canvas.find_all()), 4)   # the on-screen echo
        with mock.patch.object(self.gui.colorchooser, "askcolor",
                               return_value=((0, 128, 0), "#008000")):
            app.dlg_color_btn.invoke()
        for item in app.dlg_draw_canvas.find_all():                # recoloured
            self.assertEqual(app.dlg_draw_canvas.itemcget(item, "fill"), "#008000")
        app.dlg_save_btn.invoke()
        app.update()
        self.assertIsNone(app._dialog)
        (sig,) = app.profile.signatures
        self.assertEqual((sig.stamp.kind, sig.label), ("image", self.tr("sig.drawn_label")))
        self.assertEqual(app.selected_id, sig.id)
        self.assertTrue(app.store.is_store_file(sig.stamp.image_path))
        self.assertEqual(sig.stamp.image_path.suffix, ".png")
        with Image.open(sig.stamp.image_path) as png:
            self.assertEqual(png.mode, "RGBA")
            self.assertEqual(png.getchannel("A").getextrema(), (0, 255))
            self.assertEqual([c for _n, c in png.convert("RGB").getcolors()],
                             [(0, 128, 0)])
        # 0.4 pt per canvas px: 300 px of strokes (+ the pen) -> ~121 pt
        self.assertAlmostEqual(sig.stamp.width_pt, 303 * 0.4, delta=2)
        self.assertEqual(self.saved(app)["signatures"][0]["image_path"],
                         sig.stamp.image_path.name)
        self.click(app)                            # it is placed like any image
        self.assertEqual(len(self.items(app, sig.id, "image")), 1)

    def test_draw_points_are_clamped_to_the_canvas(self):
        # a drag goes on outside the canvas: far-away points must not turn
        # the drawing into a gigapixel raster
        app = self.app_at_place()
        app._open_draw_dialog()
        self.draw(app, [(10, 10), (99999, -99999)])
        self.assertEqual(app._dialog_state["strokes"], [[(10, 10), (620, 0)]])
        app._apply_draw_dialog()
        (sig,) = app.profile.signatures
        self.assertEqual(sig.stamp.width_pt, 240.0)        # widest allowed
        with Image.open(sig.stamp.image_path) as png:
            self.assertLessEqual(png.width, (620 + 6) * 4)

    def test_draw_dialog_empty_is_refused(self):
        app = self.app_at_place()
        app._open_draw_dialog()
        app._apply_draw_dialog()
        self.assertTrue(app._dialog.winfo_exists())
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_draw_empty"))
        self.draw(app, [(10, 10), (50, 50)])
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")   # no stale message
        app._dialog_error("x")
        app.dlg_clear_btn.invoke()                 # empties the model and its echo
        self.assertEqual(app._dialog_state["strokes"], [])
        self.assertEqual(app.dlg_draw_canvas.find_all(), ())
        self.assertEqual(app.dlg_error_lbl.cget("text"), "")
        app._apply_draw_dialog()
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_draw_empty"))
        self.assertEqual(app.profile.signatures, [])
        self.assertFalse(app.store.signatures_dir.exists())    # nothing was stored
        # a drawing over the pixel budget says so (not "draw something first")
        self.draw(app, [(10, 10), (50, 50)])
        with mock.patch.object(stamplib, "MAX_TEXT_PIXELS", 10):
            app._apply_draw_dialog()
        self.assertEqual(app.dlg_error_lbl.cget("text"), self.tr("sig.err_draw_too_large"))
        self.assertTrue(app._dialog.winfo_exists())
        self.assertEqual(app.profile.signatures, [])
        # a drawing that cannot be stored is not added either
        with mock.patch.object(app.store, "store_image", side_effect=OSError("disk full")):
            app._apply_draw_dialog()
        self.assertEqual(app.dlg_error_lbl.cget("text"),
                         self.tr("sig.err_store", error="disk full"))
        self.assertTrue(app._dialog.winfo_exists())
        self.assertEqual(app.profile.signatures, [])

    def test_dialog_cancel_and_window_close_add_nothing(self):
        app = self.app_at_place()
        app._open_text_dialog()
        self.type_text(app, "Jane")
        app.dlg_cancel_btn.invoke()
        self.assertIsNone(app._dialog)
        app._open_draw_dialog()
        app.update()
        dialog = app._dialog
        self.draw(app, [(10, 10), (50, 50)])
        app.tk.call(dialog.protocol("WM_DELETE_WINDOW"))   # the window's close button
        self.assertFalse(dialog.winfo_exists())
        self.assertIsNone(app._dialog)
        self.assertEqual(app._dialog_state, {})
        self.assertEqual(app.profile.signatures, [])
        self.assertFalse(app.store.signatures_dir.exists())
        # only one dialog at a time: opening another closes the previous one
        app._open_text_dialog()
        first = app._dialog
        app._open_draw_dialog()
        self.assertFalse(first.winfo_exists())
        self.assertEqual(app._dialog_state["kind"], "draw")

    # ------------------------------------------- delete, enable, every page
    def test_delete_removes_signature_and_file(self):
        app = self.app_at_place()
        sig = app._add_image_signature(make_png(self.tmp / "s.png"))
        stored = sig.stamp.image_path
        self.assertTrue(stored.exists())
        app.delete_btn.invoke()
        app.update()
        self.modal_button(app, self.tr("sig.delete_keep")).invoke()    # changed my mind
        app.update()
        self.assertEqual(app.profile.signatures, [sig])
        app._delete_selected()
        app.update()
        self.modal_button(app, self.tr("sig.delete_confirm")).invoke()
        app.update()
        self.assertEqual(app.profile.signatures, [])
        self.assertFalse(stored.exists())
        self.assertIsNone(app.selected_id)
        self.assertEqual(self.saved(app)["signatures"], [])
        self.assertEqual(str(app.delete_btn.cget("state")), "disabled")
        self.assertEqual(str(app.edit_btn.cget("state")), "disabled")

    def test_enable_toggle_persists_and_ungates(self):
        app = self.app_at_place("beid")
        sig = self.add_text(app)
        self.assertEqual(app._place_error(), self.tr("place.unplaced", name="Jane"))
        self.assertEqual(str(app._btn_next.cget("state")), "disabled")
        app._element_checks[sig.id].toggle()       # what a click on the box does
        app.update()
        self.assertFalse(sig.enabled)
        self.assertIsNone(app._place_error())      # an unticked signature never blocks
        self.assertEqual(str(app._btn_next.cget("state")), "normal")
        self.assertFalse(self.saved(app)["signatures"][0]["enabled"])
        self.assertEqual(app._element_checks[sig.id].get(), 0)
        app._toggle_enabled(sig.id)
        self.assertTrue(sig.enabled)
        self.assertEqual(app._element_checks[sig.id].get(), 1)
        self.assertTrue(self.saved(app)["signatures"][0]["enabled"])

    def test_toggle_all_pages_flips_model(self):
        # direct calls: the direction comes from the model, not the checkbox
        app = self.app_at_place()
        sig = self.add_text(app)
        app._toggle_all_pages()
        self.assertTrue(sig.stamp.all_pages)
        self.assertEqual(app.all_pages_chk.get(), 1)       # the widget follows
        self.assertIs(self.saved(app)["signatures"][0]["all_pages"], True)
        app._toggle_all_pages()                    # off, no position yet
        self.assertFalse(sig.stamp.all_pages)
        self.assertIsNone(sig.stamp.page)
        self.assertEqual(app.all_pages_chk.get(), 0)
        app._toggle_all_pages()
        self.click(app)
        self.assertTrue(sig.stamp.all_pages)
        self.assertIsNone(sig.stamp.page)
        self.assertEqual(app.page_text, "")        # no single target page
        self.assertTrue(app._sig_placed(sig))
        app._turn_page(1)
        app._toggle_all_pages()                    # off, with a position
        self.assertEqual(sig.stamp.page, app.cur_page + 1)
        self.assertEqual((sig.stamp.page, app.page_text), (2, "2"))
        self.assertEqual(app.all_pages_chk.get(), 0)
        app.all_pages_chk.toggle()                 # a real click flips too
        app.update()
        self.assertTrue(sig.stamp.all_pages)
        self.assertEqual(app.all_pages_chk.get(), 1)

    def test_untick_all_pages_keeps_a_stale_placement_unplaced(self):
        app = self.app_at_place()
        sig = self.add_text(app)
        app._toggle_all_pages()
        self.click(app, 0.5, 0.05)                 # near the top of an A4 page
        self.assertTrue(app._sig_placed(sig))
        before = (sig.stamp.x, sig.stamp.y, tuple(sig.placed_on))
        app.template_dims = [(612.0, 792.0)] * 2   # as with another template: Letter
        app._refresh_place()
        self.assertFalse(app._sig_placed(sig))     # saved for another page size
        app._toggle_all_pages()                    # untick: must NOT place it by itself
        self.assertFalse(sig.stamp.all_pages)
        self.assertIsNone(sig.stamp.page)
        self.assertEqual((sig.stamp.x, sig.stamp.y, tuple(sig.placed_on)), before)   # kept
        self.assertFalse(app._sig_placed(sig))
        self.assertIsNotNone(app._place_error())   # still blocks until a click
        self.click(app)
        self.assertTrue(app._sig_placed(sig))
        self.assertEqual((sig.stamp.page, tuple(sig.placed_on)), (1, (612.0, 792.0)))

    def test_toggle_all_pages_ignored_for_vignette_and_anchor(self):
        app = self.app_at_place("beid")
        app._toggle_all_pages()                    # the vignette is selected
        self.assertEqual(str(app.all_pages_chk.cget("state")), "disabled")
        self.assertEqual(app.all_pages_chk.get(), 0)
        app.destroy()
        short = make_pdf(self.tmp / "short.pdf", [(595, 842)])
        app = self.app_at_place("image", inputs=[short])
        sig = self.add_text(app)
        self.assertEqual(app._page_anchor(), "last")
        app._toggle_all_pages()                    # every element goes on the anchor page
        self.assertFalse(sig.stamp.all_pages)
        self.assertEqual(str(app.all_pages_chk.cget("state")), "disabled")

    # ------------------------------------------------- selection and placing
    def test_click_places_only_the_selected_element(self):
        app = self.app_at_place("beid")
        a = self.add_text(app, "A")
        b = self.add_text(app, "B")
        app._select(a.id)
        self.click(app, 0.3, 0.3)
        self.assertTrue(app._sig_placed(a))
        self.assertFalse(app._sig_placed(b))
        self.assertIsNone(app.profile.settings.vignette)
        self.assertEqual((a.stamp.page, a.placed_on), (1, (595.0, 842.0)))
        self.assertEqual(app.page_text, "1")
        app._select(self.gui.VIGNETTE_ID)
        self.click(app, 0.6, 0.6)
        vignette = app.profile.settings.vignette
        self.assertEqual((vignette.page, vignette.placed_on), (1, (595.0, 842.0)))
        self.assertFalse(app._sig_placed(b))
        saved = self.saved(app)
        self.assertIn("vignette", saved["settings"])
        self.assertIn("x", saved["signatures"][0])
        self.assertNotIn("x", saved["signatures"][1])
        # "Reset position" also acts on the selected element only
        app.reset_btn.invoke()
        self.assertIsNone(app.profile.settings.vignette)
        self.assertEqual(app.page_text, "")
        self.assertTrue(app._sig_placed(a))
        app._select(a.id)
        app._reset_selected_position()
        self.assertFalse(app._sig_placed(a))
        self.assertIsNone(a.placed_on)

    def test_disabled_selected_signature_is_outlined(self):
        app = self.app_at_place()
        a = self.add_text(app, "A")
        b = self.add_text(app, "B")
        app._toggle_enabled(a.id)                  # untick A, select it, click
        app._select(a.id)
        self.click(app)
        self.assertIsNotNone(a.stamp.x)            # the position is stored
        self.assertEqual(self.items(app, a.id, "image"), [])       # outline only
        (rect,) = self.items(app, a.id, "rectangle")
        self.assertEqual(app.canvas.itemcget(rect, "outline"), "#777")
        self.assertTrue(app.canvas.itemcget(rect, "dash"))
        app._select(b.id)                          # no longer selected: not drawn
        self.assertEqual(self.items(app, a.id), [])

    def test_page_field_follows_selection(self):
        app = self.app_at_place()
        a = self.add_text(app, "A")
        b = self.add_text(app, "B")
        app._select(a.id)
        app._turn_page(1)
        self.click(app)
        self.assertEqual((a.stamp.page, app.page_text), (2, "2"))
        app._turn_page(-1)
        app._select(b.id)                          # unplaced: no page
        self.assertEqual((app.page_text, app.page_entry.get()), ("", ""))
        app._select(a.id)                          # back on its own page
        self.assertEqual((app.page_text, app.cur_page, app.page_entry.get()),
                         ("2", 1, "2"))
        app._delete_signature(a.id)                # the field must not keep "2"
        self.assertEqual(app.page_text, "")
        # a new, shorter template: a stale page number would block the step
        one_page = make_pdf(self.tmp / "one.pdf", [(595, 842)])
        app.page_text = "2"
        with mock.patch.object(self.gui.filedialog, "askopenfilename",
                               return_value=str(one_page)):
            app._pick_template()
        self.assertEqual(app.page_text, "")
        self.assertIsNone(app._page_entry_error())

    def test_page_entry_retargets_selected_element(self):
        app = self.app_at_place("beid")
        a = self.add_text(app, "A")
        self.click(app)                            # A on page 1
        self.fill(app.page_entry, "2")
        app._on_page_entry_change()
        self.assertEqual((a.stamp.page, app.cur_page), (2, 1))
        self.assertEqual(self.saved(app)["signatures"][0]["page"], 2)
        app._select(self.gui.VIGNETTE_ID)
        self.click(app)                            # vignette on the previewed page 2
        self.assertEqual(app.profile.settings.vignette.page, 2)
        self.fill(app.page_entry, "1")
        app._on_page_entry_change()
        self.assertEqual(app.profile.settings.vignette.page, 1)
        self.assertEqual(a.stamp.page, 2)          # only the selected element moved

    def test_page_entry_retarget_follows_the_page_size(self):
        # pages of different sizes: an element moved through the page field
        # is "placed on" its NEW page, so the placement stays in force
        self.tpl = make_pdf(self.tmp / "mixed.pdf", [(595, 842), (842, 595)])
        app = self.app_at_place("beid")
        a = self.add_text(app, "A")
        self.click(app)                            # A on page 1 (portrait)
        self.assertEqual(tuple(a.placed_on), (595.0, 842.0))
        self.fill(app.page_entry, "2")
        app._on_page_entry_change()
        self.assertEqual((a.stamp.page, tuple(a.placed_on)), (2, (842.0, 595.0)))
        self.assertTrue(app._sig_placed(a))
        self.assertIsNone(app._place_error())
        app._select(self.gui.VIGNETTE_ID)
        self.click(app)                            # vignette on page 2 (landscape)
        self.fill(app.page_entry, "1")
        app._on_page_entry_change()
        vignette = app.profile.settings.vignette
        self.assertEqual((vignette.page, tuple(vignette.placed_on)), (1, (595.0, 842.0)))
        self.assertTrue(app._vignette_placed())

    def test_page_beyond_template_blocks(self):
        app = self.app_at_place("beid")
        self.assertIsNone(app._place_error())
        self.fill(app.page_entry, "7")
        app._on_page_entry_change()
        self.assertEqual(app._place_error(),
                         self.tr("place.page_beyond", page=7, total=2))
        self.assertEqual(str(app._btn_next.cget("state")), "disabled")   # blocking
        self.assertEqual(app.place_warn_lbl.cget("text"), app._place_error())
        # "²" (one key on an AZERTY keyboard) is a digit for str.isdigit()
        # but not a number for int(): invalid, not a ValueError in every
        # navigation callback; so is a pasted number too long for int()
        for bad in ("x", "²", "1²", "9" * 5000):
            with self.subTest(page=bad):
                self.fill(app.page_entry, bad)
                app._on_page_entry_change()
                self.assertEqual(app._place_error(), self.tr("place.page_invalid"))
                app._refresh_chrome()
                self.assertEqual(str(app._btn_next.cget("state")), "disabled")
        self.fill(app.page_entry, "")
        app._on_page_entry_change()
        self.assertIsNone(app._place_error())
        self.assertEqual(str(app._btn_next.cget("state")), "normal")

    # ----------------------------------------------------------------- layout
    def test_empty_list_has_hint_and_no_gap(self):
        import customtkinter as ctk
        app = self.app_at_place()                  # image mode, empty library

        def assert_only_the_hint():
            app.update()
            (hint,) = app.elements_list.winfo_children()
            self.assertIsInstance(hint, ctk.CTkLabel)
            self.assertEqual(hint.cget("text"), self.tr("place.list_empty"))
            # an empty CTkFrame would keep its 200×200 default size
            self.assertLess(app.elements_list.winfo_reqheight(), 80)

        assert_only_the_hint()
        sig = self.add_text(app)
        app.update()
        app._delete_signature(sig.id)
        assert_only_the_hint()
        self.assertEqual(app.pos_lbl.cget("text"), self.tr("place.select_hint"))
        self.click(app)                            # nothing to place: no crash
        self.assertEqual(app.pos_lbl.cget("text"), self.tr("place.select_hint"))

    def test_long_label_does_not_widen_panel(self):
        app = self.app_at_place("beid")
        sig = self.add_text(app, label="W" * 60)
        app.update()
        self.assertLessEqual(app.elements_panel.winfo_reqwidth(),
                             self.gui._ELEMENTS_PANEL_W + 12)
        shown = app._element_labels[sig.id][0].cget("text")
        self.assertTrue(shown.endswith("…") and len(shown) < 60, shown)
        self.assertEqual(sig.label, "W" * 60)      # only the display is elided

    def test_layout_fits_default_window(self):
        # the layout contract at the default window size, in the two
        # languages with the longest step-6 labels
        for lang in ("pt", "nl"):
            with self.subTest(lang=lang):
                self.i18n.set_language(lang)
                app = self.make_app()
                app.geometry("1180x950")
                app.update()
                self.wizard_with_state(app, self.tpl, self.out)
                app.mode_var.set("beid")
                self.add_text(app, "A")
                self.add_text(app, "B")
                app._goto_step(5)
                self.pump(app, rounds=5)
                self.assertEqual(app.winfo_width(), 1180)
                self.assertLessEqual(app.elements_panel.winfo_reqwidth(),
                                     self.gui._ELEMENTS_PANEL_W + 12)
                visible_right = (app._content_left.winfo_rootx()
                                 + app._content_left.winfo_width())
                for widget in (app.page_next_btn, app.canvas):
                    self.assertLessEqual(
                        widget.winfo_rootx() + widget.winfo_width(), visible_right,
                        widget)
                app.destroy()

    # ---------------------------------------------------------------- preview
    def test_content_failure_never_reaches_chrome(self):
        app = self.app_at_place()
        with mock.patch.object(self.gui.stamplib, "stamp_content",
                               side_effect=MemoryError()) as spy:
            sig = self.add_text(app)
            app._refresh_chrome()                  # none of these may raise
            app._goto_step(4)
            app._goto_step(5)
            app._refresh_chrome()
            self.click(app)
            self.assertEqual(app._place_error(), self.tr(
                "place.el_broken", name="Jane", error="MemoryError"))
            self.assertEqual(spy.call_count, 1)    # never rendered twice
            self.assertEqual(app._element_labels[sig.id][1].cget("text"),
                             self.tr("place.st_error"))
            self.assertEqual(len(self.items(app, sig.id, "text")), 1)   # the ⚠ sign
            self.assertEqual(self.items(app, sig.id, "image"), [])

    def test_all_elements_drawn_with_live_image_refs(self):
        app = self.app_at_place()
        a = self.add_text(app, "A")
        self.click(app, 0.3, 0.3)
        b = app._add_image_signature(make_png(self.tmp / "s.png"))
        self.click(app, 0.6, 0.6)

        def element_images():
            return [i for i in app.canvas.find_withtag("element")
                    if app.canvas.type(i) == "image"]

        self.assertEqual(len(app._canvas_imgs), 2)
        self.assertEqual(len(element_images()), 2)
        # the selected element is drawn last (on top), in red; the other in blue
        (rect_a,) = self.items(app, a.id, "rectangle")
        (rect_b,) = self.items(app, b.id, "rectangle")
        self.assertEqual(app.canvas.find_withtag("element")[-1], rect_b)
        self.assertEqual(app.canvas.itemcget(rect_b, "outline"), "#c00")
        self.assertEqual(app.canvas.itemcget(rect_a, "outline"), "#3B8ED0")
        app.geometry("1500x1000")                  # a resize redraws everything
        self.pump(app, rounds=4)
        self.assertEqual(len(app._canvas_imgs), 2)
        images = element_images()
        self.assertEqual(len(images), 2)
        for item in images:                        # a freed PhotoImage has no size
            x0, _y0, x1, _y1 = app.canvas.bbox(item)
            self.assertGreater(x1 - x0, 3)

    def test_scaled_preview_cached_across_redraws(self):
        app = self.app_at_place()
        self.add_text(app, "A")
        self.click(app, 0.3, 0.3)
        app._add_image_signature(make_png(self.tmp / "s.png"))
        self.click(app, 0.6, 0.6)
        app._preview_cache.clear()
        app._scaled_cache.clear()
        with mock.patch.object(self.gui.stamplib, "stamp_content",
                               wraps=stamplib.stamp_content) as spy:
            for _ in range(4):
                app._draw_page()
            self.assertEqual(spy.call_count, 2)    # once per signature
            scaled = dict(app._scaled_cache)
            self.assertEqual(len(scaled), 2)
            app._draw_page()
            for key, bitmap in scaled.items():     # the very same bitmaps are reused
                self.assertIs(app._scaled_cache[key], bitmap)
            app.geometry("1500x1000")              # a resize rescales the cached
            self.pump(app, rounds=4)               # content, it never re-renders
            app._refresh_chrome()
            self.assertEqual(spy.call_count, 2)
            self.assertGreater(len(app._scaled_cache), 2)

    def test_all_pages_shown_on_every_preview_page(self):
        app = self.app_at_place()
        sig = self.add_text(app)
        app._toggle_all_pages()
        self.click(app)
        self.assertEqual(len(self.items(app, sig.id, "image")), 1)
        app._turn_page(1)
        self.assertEqual(len(self.items(app, sig.id, "image")), 1)
        self.assertEqual(app._element_labels[sig.id][1].cget("text"),
                         self.tr("place.st_all"))
        self.assertEqual(app.pos_lbl.cget("text"), self.tr(
            "place.pos_all", x=f"{sig.stamp.x:.0f}", y=f"{sig.stamp.y:.0f}"))
        app._toggle_all_pages()                    # now on page 2 only
        app._turn_page(-1)
        self.assertEqual(self.items(app, sig.id), [])

    def test_default_vignette_drawn_on_last_page_only(self):
        vignette = self.gui.VIGNETTE_ID
        app = self.app_at_place("beid")
        self.assertEqual(self.items(app, vignette), [])    # page 1 of 2
        app._turn_page(1)
        (rect,) = self.items(app, vignette, "rectangle")
        self.assertTrue(app.canvas.itemcget(rect, "dash"))          # dashed: default
        x0, y0, _x1, _y1 = app.canvas.coords(rect)
        fw, fh, ox, oy = app._frame_geom
        self.assertGreater(x0, ox + fw / 2)                # bottom-right corner
        self.assertGreater(y0, oy + fh / 2)
        self.click(app, 0.2, 0.2)                  # placed on page 2
        (rect,) = self.items(app, vignette, "rectangle")
        self.assertFalse(app.canvas.itemcget(rect, "dash"))         # solid: placed
        self.assertEqual(app._element_labels[vignette][1].cget("text"),
                         self.tr("place.st_page", page=2))
        app._turn_page(-1)
        self.assertEqual(self.items(app, vignette), [])

    # ------------------------------------------------- predicates and the run
    def test_step_completion_rules(self):
        app = self.app_at_place()                  # image mode
        self.assertEqual(app._place_error(), self.tr("place.need_signature"))
        sig = self.add_text(app)
        self.assertEqual(app._place_error(), self.tr("place.unplaced", name="Jane"))
        self.assertFalse(app._step_complete(5))
        app._toggle_enabled(sig.id)                # disabled: ignored, so none is left
        self.assertEqual(app._place_error(), self.tr("place.need_signature"))
        app._toggle_enabled(sig.id)
        self.click(app)
        self.assertIsNone(app._place_error())
        self.assertTrue(app._step_complete(5))
        app.mode_var.set("beid")                   # beid: complete without any signature
        app._toggle_enabled(sig.id)
        self.assertIsNone(app._place_error())
        app._delete_signature(sig.id)
        self.assertIsNone(app._place_error())

    def test_anchor_lock_forces_every_element(self):
        short = make_pdf(self.tmp / "short.pdf", [(595, 842)])     # 1 page vs 2
        app = self.app_at_place("image", inputs=[short])
        self.assertEqual(app._page_anchor(), "last")
        a = self.add_text(app, "A")
        a.stamp = dataclasses.replace(a.stamp, page=1, x=10.0, y=10.0)
        a.placed_on = app.template_dims[0]
        b = self.add_text(app, "B", all_pages=True, x=5.0, y=5.0)
        b.placed_on = app.template_dims[0]
        app._refresh_place()
        self.assertFalse(app._sig_placed(a))       # page 1, but the anchor is "last"
        self.assertTrue(app._sig_placed(b))        # forced onto the anchor page
        self.assertEqual(app._element_labels[a.id][1].cget("text"),
                         self.tr("place.st_unplaced"))
        self.assertEqual(app._element_labels[b.id][1].cget("text"),
                         self.tr("anchor.last_page"))
        self.assertEqual(str(app.all_pages_chk.cget("state")), "disabled")
        self.assertEqual(str(app.page_entry.cget("state")), "disabled")
        app._set_page_anchor_choice("first")       # nothing was lost
        app.update()
        self.assertTrue(app._sig_placed(a))
        app._set_page_anchor_choice("last")
        app.update()
        app._select(a.id)
        self.click(app)                            # places it on the locked page
        self.assertTrue(app._sig_placed(a))
        self.assertEqual((a.stamp.page, app.page_text), (2, ""))
        self.assertTrue(b.stamp.all_pages)
        app._goto_step(6)
        app.update()
        self.assertEqual(app.step_index, 6)
        cfg = self.launch_capturing_config(app)
        self.assertEqual(len(cfg.stamps), 2)
        for stamp in cfg.stamps:                   # resolved per document
            self.assertEqual((stamp.page_anchor, stamp.page, stamp.all_pages),
                             ("last", None, False))
        self.assertEqual((cfg.page, cfg.page_anchor, cfg.x, cfg.y),
                         (None, None, None, None))             # image mode: no vignette
        self.assertTrue(b.stamp.all_pages)         # the library itself is untouched

    def test_run_summary_lists_elements(self):
        import customtkinter as ctk
        app = self.app_at_place("beid")
        a = self.add_text(app, "A", label="Alpha")
        self.click(app)
        b = self.add_text(app, "B", label="Beta")
        app._toggle_all_pages()
        self.click(app)
        c = self.add_text(app, "C", label="Gamma")
        app._toggle_enabled(c.id)                  # unticked: not part of the run
        app._goto_step(6)
        app.update()
        self.assertEqual(app.step_index, 6)

        def label_texts(widget):
            for child in widget.winfo_children():
                if isinstance(child, ctk.CTkLabel):
                    yield child.cget("text")
                yield from label_texts(child)

        summary = next(t for t in label_texts(app._content_left)
                       if self.tr("run.summary_stamps", count=2) in t)
        self.assertIn(self.tr("run.summary_place", place=self.tr("run.place_default")),
                      summary)
        self.assertIn(self.tr("run.stamp_line", name="Alpha", place=self.tr(
            "run.place_custom", page=1, x=f"{a.stamp.x:.0f}", y=f"{a.stamp.y:.0f}")),
            summary)
        self.assertIn(self.tr("run.stamp_line", name="Beta", place=self.tr(
            "run.place_all", x=f"{b.stamp.x:.0f}", y=f"{b.stamp.y:.0f}")), summary)
        self.assertNotIn("Gamma", summary)

    def test_end_to_end_two_elements_image_mode(self):
        app = self.app_at_place()
        self.add_text(app, "Jane Doe\n{date} {filename}")
        self.click(app, 0.3, 0.3)                  # page 1
        app._add_image_signature(make_png(self.tmp / "s.png"))
        app._toggle_all_pages()
        self.click(app, 0.6, 0.6)                  # every page
        app._goto_step(6)
        app._launch()                              # the real worker thread
        self.assertTrue(app._running)
        self.pump(app, lambda: not app._running, rounds=160)
        self.assertIsNone(app.run_error)
        (result,) = app.run_results
        self.assertTrue(result.ok, result.detail)
        self.assertIn("2 visual signature(s) applied", result.detail)
        self.assertEqual(app.step_index, 7)        # auto-advanced to the report
        self.assertEqual(xobject_counts(self.out / "t_signe.pdf"), [2, 1])
        # The report never truncates the detail: its column is at least as
        # wide as the text and takes the width the other two leave, whatever
        # the window size (a scrollbar reaches what the window cannot show).
        from tkinter import font as tkfont, ttk
        table = app.summary_table
        doc, status, detail = table["columns"]
        text_px = tkfont.Font(font=ttk.Style().lookup("Treeview", "font")).measure(
            result.detail)
        self.assertGreater(text_px, 360)           # wider than the column used to be
        for geometry in ("1180x950", "1700x950"):
            app.geometry(geometry)
            self.pump(app, rounds=5)
            widths = [table.column(c, "width") for c in (doc, status, detail)]
            self.assertEqual(widths[:2], [240, 130], geometry)
            self.assertGreaterEqual(widths[2], text_px, geometry)
            self.assertGreaterEqual(sum(widths) + 4, table.winfo_width(), geometry)
            if sum(widths) > table.winfo_width():  # e.g. at the default size
                self.assertLess(table.xview()[1], 1, geometry)

    def test_beid_launch_passes_stamps_and_vignette(self):
        app = self.app_at_place("beid")
        a = self.add_text(app, "A")
        self.click(app, 0.3, 0.3)
        b = self.add_text(app, "B")
        app._toggle_enabled(b.id)                  # unticked: left out
        app._select(self.gui.VIGNETTE_ID)
        app._turn_page(1)
        self.click(app, 0.5, 0.5)                  # vignette on page 2
        app._goto_step(6)
        app.update()
        cfg = self.launch_capturing_config(app)
        self.assertEqual(cfg.mode, "beid")
        self.assertEqual(cfg.stamps, [a.stamp])
        vignette = app.profile.settings.vignette
        # the legacy fields carry the VIGNETTE placement only
        self.assertEqual((cfg.page, cfg.x, cfg.y, cfg.page_anchor),
                         (2, vignette.x, vignette.y, None))
        self.assertIsNone(cfg.image_path)

    def test_beid_anchored_launch_targets_the_anchor_page(self):
        # a short file accepted at the validation step: the batch must get
        # the SAME anchor for the vignette and every signature, or it would
        # reject at run time what the wizard showed as accepted
        short = make_pdf(self.tmp / "short.pdf", [(595, 842)])     # 1 page vs 2
        app = self.app_at_place("beid", inputs=[short])
        self.assertEqual(app._page_anchor(), "last")
        self.add_text(app, "A")
        self.click(app, 0.3, 0.3)
        app._select(self.gui.VIGNETTE_ID)
        self.click(app, 0.5, 0.5)
        app._goto_step(6)
        app.update()
        self.assertEqual(app.step_index, 6)
        cfg = self.launch_capturing_config(app)
        vignette = app.profile.settings.vignette
        self.assertIsNotNone(vignette.x)
        self.assertEqual((cfg.page, cfg.page_anchor, cfg.x, cfg.y),
                         (None, "last", vignette.x, vignette.y))
        self.assertEqual([(s.page_anchor, s.page, s.all_pages) for s in cfg.stamps],
                         [("last", None, False)])
        self.assertEqual(core.anchor_requirements(cfg), (("last",), None))
        self.assertEqual(cfg.inputs, [short])      # accepted by the wizard too

    def test_language_switch_closes_dialog_and_keeps_library(self):
        app = self.app_at_place("beid")
        sig = self.add_text(app, "A", label="Alpha")
        self.click(app)
        app._open_text_dialog()                    # its texts are language-bound
        app.update()
        dialog = app._dialog
        app._on_wizard_language_change("Français")
        app.update()
        self.assertFalse(dialog.winfo_exists())
        self.assertIsNone(app._dialog)
        self.assertEqual(self.i18n.get_language(), "fr")
        self.assertEqual(app.step_index, 5)
        self.assertEqual(app.selected_id, sig.id)
        self.assertEqual(app._element_labels[sig.id][0].cget("text"), "Alpha")  # user data
        self.assertEqual(app.add_text_btn.cget("text"), "Ajouter un texte…")
        self.assertTrue(app._sig_placed(sig))
        self.assertEqual(self.saved(app)["settings"]["language"], "fr")
        # leaving the wizard closes it too
        app._open_text_dialog()
        dialog = app._dialog
        app._finish()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual([s.label for s in app.profile.signatures], ["Alpha"])


class GuiPersistence(_GuiEditorBase):
    """The user profile behind the GUI: library, placements and wizard
    settings are saved on every change and reloaded at start; Cancel and
    Finish keep them; nothing here ever touches the real user folders."""

    def test_profile_lives_in_the_temp_dirs(self):
        app = self.make_app()
        self.assertTrue(str(app.store.profile_path).startswith(str(self.tmp)))
        self.assertTrue(str(app.store.signatures_dir).startswith(str(self.tmp)))

    def test_library_and_settings_survive_restart(self):
        app = self.app_at_place()                  # image mode
        app.pades_level_var.set("b-b")
        sig = self.add_text(app, color="#FF0000")
        self.click(app)
        stamp = sig.stamp
        app.destroy()
        app = self.make_app()                      # "restart" on the same folders
        self.assertEqual((app.mode_var.get(), app.pades_level_var.get()),
                         ("image", "b-b"))
        (restored,) = app.profile.signatures
        self.assertEqual((restored.id, restored.label, restored.stamp, restored.enabled),
                         (sig.id, "Jane", stamp, True))
        # same template: placed, and step 6 is complete without a click
        self.wizard_with_state(app, self.tpl, self.out)
        self.assertEqual(app.mode_var.get(), "image")      # (Start reloads, never wipes)
        self.assertTrue(app._sig_placed(app.profile.signatures[0]))
        self.assertTrue(app._step_complete(5))
        app._goto_step(6)
        app.update()
        self.assertEqual(app.step_index, 6)

    def test_restored_placement_shown_on_its_page(self):
        app = self.app_at_place()
        self.add_text(app)
        app._turn_page(1)
        self.click(app)                            # placed on page 2 of 2
        app.destroy()
        app = self.make_app()
        self.wizard_with_state(app, self.tpl, self.out)
        app._goto_step(5)
        app.update()
        self.assertEqual((app.cur_page, app.page_text, app.page_entry.get()),
                         (1, "2", "2"))
        self.assertIn("2/2", app.page_lbl.cget("text"))

    def test_placement_not_reapplied_on_other_page_size(self):
        app = self.app_at_place()
        sig = self.add_text(app)
        self.click(app)
        other = make_pdf(self.tmp / "other.pdf", [(300, 300)])
        with mock.patch.object(self.gui.filedialog, "askopenfilename",
                               return_value=str(other)):
            app._pick_template()
        self.assertFalse(app._sig_placed(sig))     # not in force on that page size
        self.assertIsNotNone(sig.stamp.x)          # ... but nothing was destroyed
        self.assertEqual(app._element_status(sig.id), self.tr("place.st_unplaced"))
        with mock.patch.object(self.gui.filedialog, "askopenfilename",
                               return_value=str(self.tpl)):
            app._pick_template()
        self.assertTrue(app._sig_placed(sig))      # back with the original template

    def test_vignette_placement_survives_restart(self):
        app = self.app_at_place("beid")
        app._turn_page(1)
        self.click(app, 0.4, 0.4)
        placed = dataclasses.replace(app.profile.settings.vignette)
        app.destroy()
        app = self.make_app()
        self.wizard_with_state(app, self.tpl, self.out)
        self.assertEqual(app.profile.settings.vignette, placed)
        self.assertTrue(app._vignette_placed())
        app._goto_step(5)
        app.update()
        self.assertEqual((app.cur_page, app.page_text), (1, "2"))

    def test_cancel_and_finish_keep_library(self):
        app = self.app_at_place()
        self.add_text(app)
        self.click(app)
        app._cancel_wizard()
        app.update()
        self.modal_button(app, self.tr("cancel.confirm")).invoke()
        app.update()
        self.assertIsNone(app.template_path)       # the session is reset ...
        self.assertEqual(app._stepper_btns, [])
        self.assertEqual([s.label for s in app.profile.signatures], ["Jane"])  # ... not the library
        self.assertEqual(app.mode_var.get(), "image")
        self.wizard_with_state(app, self.tpl, self.out)
        self.assertTrue(app._sig_placed(app.profile.signatures[0]))
        app._finish()
        app.update()
        self.assertIsNone(app.template_path)
        self.assertEqual([s.label for s in app.profile.signatures], ["Jane"])
        self.assertEqual(len(self.saved(app)["signatures"]), 1)

    def test_reset_does_not_overwrite_saved_settings(self):
        # _reset_state sets the tk variables, whose traces save the settings:
        # it must not write the defaults back over the user's choices
        import profile_store
        app = self.make_app()
        app._start_wizard()
        app.mode_var.set("azure")
        app.pades_level_var.set("b-t")
        app.azure_auth_var.set("device-code")
        app.destroy()
        app = self.make_app()
        app._start_wizard()
        app._start_wizard()
        app.update()
        settings = self.saved(app)["settings"]
        self.assertEqual(
            (settings["mode"], settings["pades_level"], settings["azure_auth"]),
            ("azure", "b-t", "device-code"))
        self.assertEqual((app.mode_var.get(), app.azure_auth_var.get()),
                         ("azure", "device-code"))
        # The variables are set one after the other: a trace that saved in
        # between would mix the file's values with the previous session's.
        # (Here the file changed behind the app's back, as a second window
        # would do.)
        store = profile_store.ProfileStore()       # the same temp folders
        profile = store.load()
        profile.settings.mode = "image"
        profile.settings.pades_level = "b-b"
        profile.settings.azure_auth = "interactive"
        store.save(profile)
        on_disk = store.profile_path.read_bytes()
        app._start_wizard()
        app.update()
        self.assertEqual(store.profile_path.read_bytes(), on_disk)
        self.assertEqual(
            (app.mode_var.get(), app.pades_level_var.get(), app.azure_auth_var.get()),
            ("image", "b-b", "interactive"))

    def azure_step(self, app):
        """Park `app` on step 5 in azure mode (the vault entry exists)."""
        self.wizard_with_state(app, self.tpl, self.out)
        app.mode_var.set("azure")
        app._goto_step(4)
        app.update()

    def test_vault_precedence(self):
        # saved user choice > CACHET_AZURE_VAULT_URL > built-in default
        with mock.patch.dict(os.environ):
            os.environ.pop(core.ENV_AZURE_VAULT_URL, None)
            app = self.make_app()
            self.assertEqual(app.azure_vault, "https://login.live.com")
            app.destroy()
            os.environ[core.ENV_AZURE_VAULT_URL] = "https://env.vault.azure.net"
            app = self.make_app()
            self.assertEqual(app.azure_vault, "https://env.vault.azure.net")
            self.azure_step(app)
            self.fill(app.azure_vault_entry, "https://mine.vault.azure.net")
            app._on_azure_entry_edit()             # a real edit is persisted
            self.assertEqual(self.saved(app)["settings"]["azure_vault_url"],
                             "https://mine.vault.azure.net")
            app.destroy()
            app = self.make_app()                  # ... and wins over the env
            self.assertEqual(app.azure_vault, "https://mine.vault.azure.net")

    def test_focus_out_does_not_persist_env_vault(self):
        with mock.patch.dict(os.environ,
                             {core.ENV_AZURE_VAULT_URL: "https://env.vault.azure.net"}):
            app = self.make_app()
            self.azure_step(app)
            self.assertEqual(app.azure_vault_entry.get(), "https://env.vault.azure.net")
            app._on_azure_entry_edit()             # what a mere <FocusOut> calls
            self.assertNotIn("azure_vault_url", self.saved(app)["settings"])
            # the key-name override is never persisted
            self.fill(app.azure_key_entry, "sig-override")
            app._on_azure_entry_edit()
            self.assertEqual(app.azure_key, "sig-override")
            on_disk = app.store.profile_path.read_text(encoding="utf-8")
            self.assertNotIn("sig-override", on_disk)
            self.assertNotIn("env.vault", on_disk)

    def test_output_dir_restored_only_if_it_exists(self):
        app = self.make_app()
        out = self.tmp / "signed"
        out.mkdir()
        with mock.patch.object(self.gui.filedialog, "askdirectory",
                               return_value=str(out)):
            app._pick_output()
        with mock.patch.object(self.gui.filedialog, "askopenfilename",
                               return_value=str(self.tpl)):
            app._pick_azure_anchors()              # (any existing file will do)
        app.destroy()
        app = self.make_app()
        self.assertEqual(app.output_dir, out)
        self.assertEqual(app.azure_anchors_path, self.tpl)
        app.destroy()
        out.rmdir()                                # the folder is gone since
        app = self.make_app()
        self.assertIsNone(app.output_dir)
        self.assertEqual(self.saved(app)["settings"]["output_dir"], str(out))  # kept
        app.destroy()
        # A saved path the OS refuses to even probe (here an over-long name;
        # likewise an unreachable share or a permission problem — pathlib
        # raises for those) is ignored too: the app must still start.
        unprobeable = str(self.tmp / ("a" * 300) / "signed")
        profile = self.saved(app)
        profile["settings"].update(output_dir=unprobeable, azure_trust_anchors=unprobeable)
        app.store.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        with mock.patch.dict(os.environ):
            os.environ.pop(core.ENV_AZURE_TRUST_ANCHORS, None)
            app = self.make_app()
        self.assertIsNone(app.output_dir)
        self.assertIsNone(app.azure_anchors_path)

    def test_language_persisted_and_applied_by_launch_gui(self):
        app = self.make_app()
        app._on_language_change("Deutsch")
        app.update()
        self.assertEqual(self.saved(app)["settings"]["language"], "de")
        app.destroy()
        self.i18n.set_language("en")
        stores = []

        class StubApp:
            def __init__(self, args, store=None):
                stores.append(store)

            def mainloop(self):
                pass

        with mock.patch.object(self.gui, "CachetApp", StubApp), \
                mock.patch.object(self.gui.ctk, "set_appearance_mode"):
            self.assertEqual(self.gui.launch_gui(self.types.SimpleNamespace(lib=None)), 0)
        self.assertEqual(self.i18n.get_language(), "de")   # persisted > system
        self.assertTrue(str(stores[0].profile_path).startswith(str(self.tmp)))

    def test_app_init_does_not_change_language(self):
        # only launch_gui applies the saved language: tests (and embedders)
        # build apps directly and rely on the current one
        app = self.make_app()
        app._on_language_change("Deutsch")
        app.destroy()
        self.i18n.set_language("en")
        self.make_app()
        self.assertEqual(self.i18n.get_language(), "en")

    def test_save_failure_is_non_fatal(self):
        app = self.app_at_place()
        with mock.patch.object(app.store, "save", side_effect=OSError("read-only")):
            sig = self.add_text(app)               # must not raise
            self.click(app)
            self.assertEqual(app._profile_error, "read-only")
            self.assertTrue(app._sig_placed(sig))  # the session goes on
            self.assertEqual(app.place_warn_lbl.cget("text"),
                             self.tr("place.save_failed", error="read-only"))
        app._save_profile()                        # the folder is writable again
        app._update_place_labels()
        self.assertIsNone(app._profile_error)
        self.assertEqual(app.place_warn_lbl.cget("text"), "")
        self.assertEqual(len(self.saved(app)["signatures"]), 1)

    def test_corrupt_profile_starts_clean(self):
        cfg = Path(os.environ["CACHET_CONFIG_DIR"])
        cfg.mkdir(parents=True)
        (cfg / "profile.json").write_bytes(b"\x00\xff garbage {")
        app = self.make_app()                      # must start
        self.assertEqual(app.profile.signatures, [])
        self.assertEqual((cfg / "profile.json.bak").read_bytes(), b"\x00\xff garbage {")
        app._start_wizard()
        app.update()
        self.assertEqual(app.step_index, 0)


class HeadlessImport(unittest.TestCase):
    def test_core_imports_without_tkinter(self):
        code = "import sign_pdfs_beid, sys; print('tkinter' in sys.modules)"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(Path(__file__).parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False")

    def test_core_does_not_import_profile_store(self):
        # The CLI never reads the persisted GUI profile, not even implicitly.
        code = ("import sign_pdfs_beid, sys; "
                "print('profile_store' in sys.modules, 'stamps' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(Path(__file__).parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "False True")

    def test_new_modules_import_without_tkinter(self):
        # stamps.py and profile_store.py serve the CLI binary too (no Tk).
        code = ("import stamps, profile_store, sys; "
                "print(sorted({'tkinter', 'customtkinter', 'gui', 'i18n'} "
                "& set(sys.modules)))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(Path(__file__).parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
