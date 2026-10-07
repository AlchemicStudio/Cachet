#!/usr/bin/env python3
"""Headless tests for stamps.py (visual signatures: text, images, strokes).

No card, no network, no display. Text sizes are never asserted pixel-exact
(font hinting differs between FreeType builds): ratios, bounding boxes and
colours only.
Run with:  ./venv/bin/python -m unittest -v test_stamps
"""

import dataclasses
import datetime
import gc
import io
import json
import re
import shutil
import struct
import subprocess
import sys
import unittest
import warnings
from pathlib import Path
from unittest import mock

from PIL import Image, ImageFont

from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.fields import SigFieldSpec, append_signature_field, enumerate_sig_fields

import sign_pdfs_beid as core
import stamps
from stamps import Stamp, StampError
from test_sign_pdfs_beid import TmpCase, make_pdf, make_png, pdf_stream, write_pdf_objects

DAY = datetime.date(2026, 10, 7)
INK = (26, 43, 140)                      # stamps.DEFAULT_COLOR


def strip_page_contents(pdf_path, page_index) -> Path:
    """Writes a sibling of `pdf_path` whose page `page_index` (0-based) has no
    /Contents entry — the "blank page" some producers emit (/Contents is
    optional in PDF). Returns the new path."""
    src = Path(pdf_path)
    with src.open("rb") as inf:
        writer = IncrementalPdfFileWriter(inf, strict=False)
        page_ref, _ = writer.find_page_for_modification(page_index)
        del page_ref.get_object()["/Contents"]
        writer.mark_update(page_ref)
        out = io.BytesIO()
        writer.write(out)
    dst = src.with_name(f"{src.stem}_nocontents.pdf")
    dst.write_bytes(out.getvalue())
    return dst


def damaged_font(path) -> Path:
    """Writes a copy of a bundled font whose `glyf` table is overwritten with
    0xFF: FreeType (2.13) still OPENS it, then fails on the first glyph it has
    to load (every glyph is an invalid composite). Returns the path."""
    data = bytearray(stamps.resolve_font("lato").read_bytes())
    for i in range(struct.unpack(">H", data[4:6])[0]):               # table directory
        tag, _, offset, length = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        if tag == b"glyf":
            data[offset:offset + length] = b"\xff" * length
    Path(path).write_bytes(bytes(data))
    return Path(path)


def damaged_png(path) -> Path:
    """Writes a PNG whose IDAT chunk declares a length of 0: Pillow identifies
    it, then raises a SyntaxError (not an OSError) while decoding."""
    data = bytearray(make_png(path).read_bytes())
    pos = data.index(b"IDAT")
    data[pos - 4:pos] = struct.pack(">I", 0)
    Path(path).write_bytes(bytes(data))
    return Path(path)


def pdf_pages(pdf_path) -> list:
    """Page dictionaries of a (flat page tree) PDF made by make_pdf."""
    reader = PdfFileReader(io.BytesIO(Path(pdf_path).read_bytes()), strict=False)
    return [kid.get_object() for kid in reader.root["/Pages"]["/Kids"]]


def page_xobjects(page) -> dict:
    """{resource name: object id} of the XObjects of a page dictionary."""
    # [] dereferences indirect objects; dict.get() would hand back the reference
    if "/Resources" not in page or "/XObject" not in page["/Resources"]:
        return {}
    xobjects = page["/Resources"]["/XObject"]
    return {name: xobjects.raw_get(name).idnum for name in xobjects}


def near(pixel, rgb, tol=40) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(pixel[:3], rgb))


class StampCase(TmpCase):
    """TmpCase + cleanup + a helper that stamps a PDF and returns the output."""

    def setUp(self):
        super().setUp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def stamped(self, pdf, stamp_list, **kwargs) -> Path:
        pdf = Path(pdf)
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            stamps.apply_stamps(writer, stamp_list, **kwargs)
            out = io.BytesIO()
            writer.write(out)
        dst = pdf.with_name(f"{pdf.stem}_out.pdf")
        dst.write_bytes(out.getvalue())
        return dst


class FontRegistry(StampCase):
    def test_six_bundled_fonts_resolve_and_load(self):
        self.assertEqual(len(stamps.BUNDLED_FONTS), 6)
        self.assertEqual([fid for fid, _ in stamps.font_choices()], list(stamps.BUNDLED_FONTS))
        self.assertIn(stamps.DEFAULT_FONT, stamps.BUNDLED_FONTS)
        for font_id, name in stamps.font_choices():
            with self.subTest(font=font_id):
                path = stamps.resolve_font(font_id)
                self.assertTrue(path.is_file(), path)
                self.assertTrue(name)
                ImageFont.truetype(str(path), 40)          # raises if not a font
                img = stamps.render_text_image("Jane Doe", font_id, 24, "#000000")
                self.assertGreater(img.width, img.height)

    def test_ids_are_case_insensitive(self):
        self.assertEqual(stamps.resolve_font("  Great-Vibes "), stamps.resolve_font("great-vibes"))
        self.assertEqual(stamps.resolve_font("LATO"), stamps.resolve_font("lato"))

    def test_unknown_font_raises(self):
        with self.assertRaises(StampError) as cm:
            stamps.resolve_font("comic-sans")
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("unknown font 'comic-sans'"), msg)
        for font_id in stamps.BUNDLED_FONTS:
            self.assertIn(font_id, msg)
        with self.assertRaises(StampError):
            stamps.resolve_font(str(self.p("missing.ttf")))
        with self.assertRaises(StampError):
            stamps.resolve_font("")

    def test_user_font_path_accepted(self):
        mine = self.p("mine.ttf")
        shutil.copy(stamps.resolve_font("lato"), mine)
        self.assertEqual(stamps.resolve_font(str(mine)), mine)
        user = stamps.render_text_image("Jane", str(mine), 24, "#000000")
        bundled = stamps.render_text_image("Jane", "lato", 24, "#000000")
        self.assertEqual(user.size, bundled.size)

    def test_asset_path_honours_meipass(self):
        here = Path(stamps.__file__).resolve().parent
        self.assertEqual(stamps.asset_path("fonts"), here / "fonts")
        with mock.patch.object(sys, "_MEIPASS", str(self.tmp), create=True):
            self.assertEqual(stamps.asset_path("fonts"), self.tmp / "fonts")
            # the frozen layout is what resolve_font looks at
            with self.assertRaises(StampError) as cm:
                stamps.resolve_font("lato")
            self.assertTrue(str(cm.exception).startswith("bundled font file missing: "))
            (self.tmp / "fonts").mkdir()
            shutil.copy(here / "fonts" / "Lato-Regular.ttf", self.tmp / "fonts")
            self.assertEqual(stamps.resolve_font("lato"),
                             self.tmp / "fonts" / "Lato-Regular.ttf")

    def test_licences_ship_with_fonts(self):
        licences = stamps.asset_path("fonts") / "licenses"
        self.assertEqual(len(list(licences.glob("OFL-*.txt"))), len(stamps.BUNDLED_FONTS))
        for font_id in stamps.BUNDLED_FONTS:
            lic = licences / f"OFL-{font_id.replace('-', '')}.txt"
            self.assertTrue(lic.is_file(), lic)
            self.assertIn("SIL OPEN FONT LICENSE", lic.read_text(encoding="utf-8").upper())


class TextRendering(StampCase):
    def render(self, text="Jane Doe", font="great-vibes", size=24, color="#1A2B8C", **kw):
        return stamps.render_text_image(text, font, size, color, **kw)

    def test_rgba_tight_crop_flat_colour(self):
        img = self.render()
        self.assertEqual(img.mode, "RGBA")
        # tight on the four sides: the ink reaches every edge
        self.assertEqual(img.getchannel("A").getbbox(), (0, 0) + img.size)
        # the RGB plane is the flat colour, transparent pixels included
        self.assertEqual(img.convert("RGB").getcolors(), [(img.width * img.height, INK)])

    def test_alpha_is_antialiased(self):
        levels = sum(1 for count in self.render().getchannel("A").histogram() if count)
        self.assertGreater(levels, 16)

    def test_size_scales_with_font_size(self):
        small, big = self.render(size=24), self.render(size=48)
        self.assertAlmostEqual(big.height / small.height, 2.0, delta=0.16)
        self.assertAlmostEqual(big.width / small.width, 2.0, delta=0.16)

    def test_multiline_is_taller(self):
        one, two = self.render("Jane Doe"), self.render("Jane Doe\nJane Doe")
        self.assertGreater(two.height, one.height * 1.6)
        self.assertAlmostEqual(two.width, one.width, delta=one.width * 0.05)

    def test_blank_text_raises(self):
        for text in ("", "   ", "\n"):
            with self.subTest(text=text):
                with self.assertRaises(StampError) as cm:
                    self.render(text)
                self.assertEqual(str(cm.exception), "text is empty")

    def test_bad_colour_raises(self):
        for color in ("blue", "#12345", "#GGGGGG", "1A2B8C", "#1A2B8CFF", "", None):
            with self.subTest(color=color):
                with self.assertRaises(StampError) as cm:
                    self.render(color=color)
                self.assertEqual(str(cm.exception), f"bad color {color!r} (expected #RRGGBB)")
        self.assertEqual(self.render(color="#ff0000").convert("RGB").getpixel((0, 0)),
                         (255, 0, 0))

    def test_deterministic(self):
        a, b = self.render("Jane Doe\n07/10/2026"), self.render("Jane Doe\n07/10/2026")
        self.assertEqual(a.size, b.size)
        self.assertEqual(a.tobytes(), b.tobytes())

    def test_dpi_sets_point_size(self):
        stamp = Stamp(kind="text", text="Jane Doe", font_size=24)
        img, (w_pt, h_pt) = stamps.stamp_content(stamp)
        self.assertEqual((w_pt, h_pt), (img.width * 72 / 300, img.height * 72 / 300))
        # a 24 pt text is a few tens of points high, whatever the raster size
        self.assertTrue(12 < h_pt < 60, h_pt)

    def test_oversized_text_raises_stamp_error(self):
        real_new = Image.new
        for text in ("W" * 2000, "a" * 300):
            with self.subTest(chars=len(text)):
                with mock.patch.object(stamps.Image, "new", side_effect=real_new) as spy:
                    with self.assertRaises(StampError) as cm:
                        stamps.render_text_image(text, "lato", 400, "#000000")
                self.assertIn("too large", str(cm.exception))
                # refused BEFORE any allocation: only the 1x1 probe was requested
                sizes = [tuple(call.args[1]) for call in spy.call_args_list]
                self.assertEqual(sizes, [(1, 1)])

    def test_budget_boundary_typical_sizes_pass(self):
        self.assertGreater(self.render("Jane Doe", size=400).height, 1000)
        three = self.render("Jane Doe\nRead and approved\n07/10/2026", size=72)
        self.assertGreater(three.height, 600)

    def test_validate_stamp_refuses_oversized_text(self):
        for placed in (True, False):
            with self.subTest(placed=placed):
                stamp = Stamp(kind="text", text="W" * 2000, font="lato", font_size=400,
                              page=1, x=0.0, y=0.0)
                real_new = Image.new
                with mock.patch.object(stamps.Image, "new", side_effect=real_new) as spy:
                    with self.assertRaises(StampError) as cm:
                        stamps.validate_stamp(stamp, placed=placed)
                self.assertIn("too large", str(cm.exception))
                self.assertEqual([tuple(c.args[1]) for c in spy.call_args_list], [(1, 1)])

    def test_validation_never_rasterises(self):
        stamp = Stamp(kind="text", text="Jane Doe", page=1, x=0.0, y=0.0)
        real_new = Image.new
        with mock.patch.object(stamps.Image, "new", side_effect=real_new) as spy:
            stamps.validate_stamp(stamp)
        self.assertEqual([tuple(c.args[1]) for c in spy.call_args_list], [(1, 1)])

    def test_damaged_font_raises_stamp_error(self):
        # A font file FreeType opens but cannot use fails while MEASURING, with
        # an OSError: it must surface as the "cannot load font" StampError
        # (validate_stamp runs on every profile load, which never raises).
        font = damaged_font(self.p("damaged.ttf"))
        message = f"cannot load font {str(font)!r}: "
        stamp = Stamp(kind="text", text="Jane Doe", font=str(font), page=1, x=0.0, y=0.0)
        for placed in (True, False):
            with self.assertRaises(StampError) as cm:
                stamps.validate_stamp(stamp, placed=placed)
            self.assertTrue(str(cm.exception).startswith(message), str(cm.exception))
        with self.assertRaises(StampError) as cm:
            stamps.stamp_content(stamp)
        self.assertTrue(str(cm.exception).startswith(message), str(cm.exception))
        # same contract when FreeType only fails while DRAWING
        with mock.patch.object(stamps.ImageDraw.ImageDraw, "multiline_text",
                               side_effect=OSError("raster overflow")):
            with self.assertRaises(StampError) as cm:
                self.render(font="lato")
        self.assertEqual(str(cm.exception), "cannot load font 'lato': raster overflow")


class Placeholders(unittest.TestCase):
    def test_date_and_filename_replaced(self):
        out = stamps.resolve_text("Signed {date} on {filename}", filename="contract", date=DAY)
        self.assertEqual(out, "Signed 07/10/2026 on contract")
        today = datetime.date.today().strftime("%d/%m/%Y")
        self.assertEqual(stamps.resolve_text("{date}"), today)

    def test_other_braces_untouched(self):
        self.assertEqual(stamps.resolve_text("{x} {} {{date}}", date=DAY), "{x} {} {07/10/2026}")
        self.assertEqual(stamps.resolve_text("{0} {Date} {file name}", date=DAY),
                         "{0} {Date} {file name}")

    def test_filename_containing_placeholder_not_reexpanded(self):
        out = stamps.resolve_text("{filename} / {date}", filename="{date}_{filename}", date=DAY)
        self.assertEqual(out, "{date}_{filename} / 07/10/2026")

    def test_newlines_normalised(self):
        self.assertEqual(stamps.resolve_text("a\r\nb\rc\nd", date=DAY), "a\nb\nc\nd")


class ImageLoading(StampCase):
    def test_palette_transparency_preserved(self):
        path = self.p("pal.png")
        im = Image.new("P", (4, 4), 0)
        im.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
        im.putpixel((1, 1), 1)
        im.save(path, transparency=0)
        with Image.open(path) as check:
            self.assertEqual(check.mode, "P")
        out = stamps.load_image(path)
        self.assertEqual(out.mode, "RGBA")
        self.assertEqual(out.getpixel((0, 0))[3], 0)
        self.assertEqual(out.getpixel((1, 1)), (0, 255, 0, 255))

    def test_cmyk_and_bilevel_converted(self):
        cmyk = self.p("cmyk.jpg")
        Image.new("CMYK", (30, 20), (0, 255, 255, 0)).save(cmyk)
        bilevel = self.p("bw.png")
        Image.new("1", (30, 20), 1).save(bilevel)
        for path in (cmyk, bilevel):
            with self.subTest(path=path.name):
                out = stamps.load_image(path)
                self.assertEqual(out.mode, "RGBA")
                self.assertEqual(out.size, (30, 20))
                # and it really goes into a PDF (was a bare NotImplementedError)
                pdf = make_pdf(self.p(f"{path.stem}.pdf"), [(595, 842)])
                out_pdf = self.stamped(pdf, [Stamp(kind="image", image_path=path,
                                                   page=1, x=10.0, y=10.0)])
                self.assertEqual(len(page_xobjects(pdf_pages(out_pdf)[0])), 1)

    def test_rgb_kept(self):
        for mode, name in (("RGB", "a.jpg"), ("RGBA", "a.png"), ("L", "l.png"), ("LA", "la.png")):
            with self.subTest(mode=mode):
                path = self.p(name)
                Image.new(mode, (30, 20)).save(path)
                self.assertEqual(stamps.load_image(path).mode, mode)

    def test_exif_orientation_applied(self):
        path = self.p("rot.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6                                   # rotate 90° clockwise to display
        Image.new("RGB", (40, 20), (200, 0, 0)).save(path, exif=exif)
        with Image.open(path) as raw:
            self.assertEqual(raw.size, (40, 20))
        self.assertEqual(stamps.load_image(path).size, (20, 40))
        # the stamp's size follows the NORMALISED image
        _, size = stamps.stamp_content(Stamp(kind="image", image_path=path, width_pt=100))
        self.assertEqual(size, (100.0, 200.0))

    def test_unreadable_raises_stamp_error(self):
        text = self.p("not_an_image.png")
        text.write_text("hello", encoding="utf-8")
        truncated = self.p("truncated.png")
        truncated.write_bytes(make_png(self.p("ok.png")).read_bytes()[:40])
        broken = damaged_png(self.p("broken_chunk.png"))     # a SyntaxError inside Pillow
        for path in (text, truncated, broken, self.p("missing.png"), self.tmp):
            with self.subTest(path=path.name):
                with self.assertRaises(StampError) as cm:
                    stamps.load_image(path)
                self.assertTrue(str(cm.exception).startswith(f"cannot read image {path}: "))
                self.assertEqual(str(cm.exception).count(str(path)), 1)    # not repeated
        # Pillow's decoders raise more than OSError/ValueError on damaged files
        ok = make_png(self.p("ok.png"))
        for exc in (SyntaxError("bad EXIF"), NotImplementedError("pixel format"), MemoryError()):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(stamps.ImageOps, "exif_transpose", side_effect=exc):
                    with self.assertRaises(StampError) as cm:
                        stamps.load_image(ok)
                self.assertTrue(str(cm.exception).startswith(f"cannot read image {ok}: "))

    def test_file_handle_closed(self):
        path = make_png(self.p("sig.png"))
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            img = stamps.load_image(path)
            gc.collect()
        moved = self.p("moved.png")
        path.rename(moved)                                 # fails on Windows if still open
        moved.unlink()
        self.assertEqual(img.getpixel((0, 0)), (255, 0, 0, 128))   # pixels are in memory

    def test_large_image_downsampled_to_600_dpi(self):
        path = make_png(self.p("big.png"), size=(3000, 1200))
        img, size = stamps.stamp_content(Stamp(kind="image", image_path=path, width_pt=150))
        self.assertEqual(img.size, (1250, 500))
        self.assertEqual(size, (150.0, 60.0))
        self.assertEqual(img.mode, "RGBA")

    def test_small_image_not_resampled(self):
        path = make_png(self.p("small.png"), size=(300, 120))
        img, size = stamps.stamp_content(Stamp(kind="image", image_path=path, width_pt=150))
        self.assertEqual(img.size, (300, 120))
        self.assertEqual(size, (150.0, 60.0))

    def test_missing_image_path_raises(self):
        with self.assertRaises(StampError) as cm:
            stamps.stamp_content(Stamp(kind="image"))
        self.assertEqual(str(cm.exception), "image_path is required")


class StampModel(StampCase):
    def test_roundtrip_text(self):
        stamp = Stamp(kind="text", text="Jane Doe\nRead and approved, {date}", font="caveat",
                      color="#AA0000", font_size=18.0, page_anchor="last", x=360.0, y=120.5)
        data = json.loads(json.dumps(stamp.to_dict()))
        self.assertEqual(data, {
            "kind": "text", "text": "Jane Doe\nRead and approved, {date}", "font": "caveat",
            "color": "#AA0000", "font_size": 18.0, "page_anchor": "last", "x": 360.0, "y": 120.5})
        self.assertEqual(Stamp.from_dict(data), stamp)
        # unplaced: no placement key at all; "every page" is the only flag kept
        self.assertEqual(set(Stamp(kind="text", text="x").to_dict()),
                         {"kind", "text", "font", "color", "font_size"})
        paraphe = Stamp(kind="text", text="JD", all_pages=True)
        self.assertIs(paraphe.to_dict()["all_pages"], True)
        self.assertEqual(Stamp.from_dict(paraphe.to_dict()), paraphe)

    def test_roundtrip_image_relative_to_base_dir(self):
        base = self.p("store")
        base.mkdir()
        png = make_png(base / "sig.png")
        stamp = Stamp(kind="image", image_path=png, width_pt=120.0, page=2, x=60.0, y=700.0)
        data = stamp.to_dict(base_dir=base)
        self.assertEqual(data, {"kind": "image", "image_path": "sig.png", "width_pt": 120.0,
                                "page": 2, "x": 60.0, "y": 700.0})
        self.assertEqual(Stamp.from_dict(json.loads(json.dumps(data)), base_dir=base), stamp)

    def test_image_outside_base_dir_is_absolute(self):
        base = self.p("store")
        (base / "sub").mkdir(parents=True)
        outside = make_png(self.p("elsewhere.png"))
        nested = make_png(base / "sub" / "deep.png")
        for png in (outside, nested):                      # only a DIRECT child is relative
            data = Stamp(kind="image", image_path=png).to_dict(base_dir=base)
            self.assertEqual(data["image_path"], str(png))
        self.assertEqual(Stamp(kind="image", image_path=outside).to_dict()["image_path"],
                         str(outside))
        # an absolute path is taken as it is, whatever the base dir
        loaded = Stamp.from_dict({"kind": "image", "image_path": str(outside)}, base_dir=base)
        self.assertEqual(loaded.image_path, outside)

    def test_traversal_path_is_not_relative(self):
        base = self.p("store")
        base.mkdir()
        make_png(self.p("x.png"))
        data = Stamp(kind="image", image_path=base / ".." / "x.png").to_dict(base_dir=base)
        self.assertTrue(Path(data["image_path"]).is_absolute(), data)
        self.assertEqual(Path(data["image_path"]), self.p("x.png"))
        # and the other way round: a relative entry is joined as written
        loaded = Stamp.from_dict({"kind": "image", "image_path": "../x.png"}, base_dir=base)
        self.assertEqual(loaded.image_path, base / ".." / "x.png")

    def test_unknown_keys_ignored(self):
        stamp = Stamp.from_dict({"kind": "text", "text": "x", "id": "abc", "label": "L",
                                 "placed_on": [595, 842], "enabled": True, "zzz": {"a": 1}})
        self.assertEqual(stamp, Stamp(kind="text", text="x"))
        # missing keys take the dataclass defaults
        self.assertEqual((stamp.font, stamp.color, stamp.font_size, stamp.width_pt),
                         ("great-vibes", "#1A2B8C", 24.0, 150.0))

    def test_numbers_stored_as_float(self):
        stamp = Stamp.from_dict({"kind": "text", "text": "x", "font_size": 12, "width_pt": 80,
                                 "page": 3, "x": 10, "y": 20})
        for value in (stamp.font_size, stamp.width_pt, stamp.x, stamp.y):
            self.assertIs(type(value), float)
        self.assertIs(type(stamp.page), int)

    def test_relative_font_resolved_against_base_dir(self):
        base = self.p("cfg")
        base.mkdir()
        shutil.copy(stamps.resolve_font("lato"), base / "mine.ttf")
        stamp = Stamp.from_dict({"kind": "text", "text": "x", "font": "mine.ttf"}, base_dir=base)
        self.assertEqual(stamp.font, str(base / "mine.ttf"))
        stamps.validate_stamp(stamp, placed=False)
        # a bundled id is never turned into a path
        bundled = Stamp.from_dict({"kind": "text", "text": "x", "font": "Caveat"}, base_dir=base)
        self.assertEqual(bundled.font, "Caveat")

    def test_font_untouched_without_base_dir(self):
        for font in ("./mine.ttf", "~/fonts/mine.ttf", "mine.ttf"):
            stamp = Stamp.from_dict({"kind": "text", "text": "x", "font": font})
            self.assertEqual(stamp.font, font)
            self.assertEqual(Stamp.from_dict(stamp.to_dict()), stamp)

    def test_hostile_paths_raise_stamp_error_only(self):
        # pathlib raises RuntimeError for an unknown ~user and OSError for an
        # over-long name: neither may escape as itself.
        long_name = "x" * 5000
        rows = [
            ({"kind": "image", "image_path": "~no_such_user_zz/sig.png"}, "image not found: "),
            ({"kind": "image", "image_path": long_name + ".png"}, "image not found: "),
            ({"kind": "image", "image_path": "a\x00b.png"}, "image not found: "),
            ({"kind": "image", "image_path": "\ud800.png"}, "image not found: "),
            ({"kind": "text", "text": "x", "font": "~no_such_user_zz/f.ttf"}, "unknown font "),
            ({"kind": "text", "text": "x", "font": long_name}, "unknown font "),
            ({"kind": "text", "text": "x", "font": "a\x00b"}, "unknown font "),
            ({"kind": "text", "text": "x", "font": "\ud800.ttf"}, "unknown font "),
        ]
        for base_dir in (None, self.tmp):
            for data, prefix in rows:
                with self.subTest(base_dir=base_dir, data=ascii(data)[:60]):
                    stamp = Stamp.from_dict(data, base_dir=base_dir)
                    with self.assertRaises(StampError) as cm:
                        stamps.validate_stamp(stamp, placed=False)
                    self.assertTrue(str(cm.exception).startswith(prefix),
                                    ascii(str(cm.exception))[:80])
                    # and it still serialises (a profile holding it must save):
                    # a NUL / lone surrogate makes Path.resolve raise ValueError
                    out = stamp.to_dict(base_dir=self.tmp)
                    self.assertEqual("image_path" in out, stamp.kind == "image")
        with self.assertRaises(StampError):
            stamps.load_image(long_name)

    def test_from_dict_type_errors(self):
        cases = [([], "expected an object"), ("x", "expected an object"),
                 (None, "expected an object"), ({}, "missing 'kind'")]
        for key in ("kind", "text", "font", "color", "image_path", "page_anchor"):
            cases.append(({"kind": "text", key: 5}, f"'{key}' must be a string"))
            cases.append(({"kind": "text", key: None}, f"'{key}' must be a string"))
        for key in ("font_size", "width_pt", "x", "y"):
            cases.append(({"kind": "text", key: "1"}, f"'{key}' must be a number"))
            cases.append(({"kind": "text", key: 10 ** 400}, f"'{key}' must be a number"))
        for bad in (1.5, "1", None):
            cases.append(({"kind": "text", "page": bad}, "'page' must be an integer"))
        for bad in (1, "true", None):
            cases.append(({"kind": "text", "all_pages": bad}, "'all_pages' must be true or false"))
        for data, message in cases:
            with self.subTest(data=data):
                with self.assertRaises(StampError) as cm:
                    Stamp.from_dict(data)
                self.assertEqual(str(cm.exception), message)

    def test_bool_is_not_a_number(self):
        for key in ("font_size", "width_pt", "x", "y"):
            with self.subTest(key=key):
                with self.assertRaises(StampError) as cm:
                    Stamp.from_dict({"kind": "text", key: True})
                self.assertEqual(str(cm.exception), f"'{key}' must be a number")
        with self.assertRaises(StampError) as cm:
            Stamp.from_dict({"kind": "text", "page": True})
        self.assertEqual(str(cm.exception), "'page' must be an integer")

    def test_validate_rules(self):
        png = make_png(self.p("sig.png"))
        not_a_font = self.p("fake.ttf")
        not_a_font.write_text("not a font", encoding="utf-8")
        text = dict(kind="text", text="Jane", page=1, x=0.0, y=0.0)
        image = dict(kind="image", image_path=png, page=1, x=0.0, y=0.0)
        nan, inf = float("nan"), float("inf")
        size_msg = "font_size must be greater than 0 and at most 400"
        width_msg = "width_pt must be greater than 0 and at most 5000"
        target_msg = "give exactly one page target: page, page_anchor or all_pages"
        rows = [
            # content rules, in the order they are checked
            (dict(text, kind="sticker"), "unknown kind 'sticker' (expected text|image)"),
            (dict(text, text="  \n "), "text is empty"),
            (dict(text, text="a" * 2001), "text is too long (max 2000 characters)"),
            (dict(text, color="blue"), "bad color 'blue' (expected #RRGGBB)"),
            (dict(text, font_size=0.0), size_msg),
            (dict(text, font_size=-3.0), size_msg),
            (dict(text, font_size=400.5), size_msg),
            (dict(text, font_size=nan), size_msg),
            (dict(text, font_size=inf), size_msg),
            (dict(text, font="comic-sans"), "unknown font 'comic-sans'"),
            (dict(text, font=str(not_a_font)), f"cannot load font {str(not_a_font)!r}: "),
            (dict(text, text="W" * 2000, font="lato", font_size=400.0),
             "text is too large to render (reduce the font size or the text length)"),
            (dict(image, image_path=None), "image_path is required"),
            (dict(image, image_path=self.p("gone.png")), f"image not found: {self.p('gone.png')}"),
            (dict(image, width_pt=0.0), width_msg),
            (dict(image, width_pt=-1.0), width_msg),
            (dict(image, width_pt=5000.5), width_msg),
            (dict(image, width_pt=nan), width_msg),
            # placement rules
            (dict(text, page=None), target_msg),
            (dict(text, page_anchor="last"), target_msg),
            (dict(text, all_pages=True), target_msg),
            (dict(image, page=None, page_anchor="first", all_pages=True), target_msg),
            (dict(text, page=0), "page must be an integer >= 1"),
            (dict(text, page=None, page_anchor="middle"),
             "unknown page_anchor 'middle' (expected first|last)"),
            (dict(text, x=None), "x and y are required"),
            (dict(image, y=None), "x and y are required"),
            (dict(text, x=nan), "x and y must be finite numbers"),
            (dict(image, y=inf), "x and y must be finite numbers"),
            # content is checked before placement
            (dict(text, text="", page=None), "text is empty"),
        ]
        for kwargs, message in rows:
            with self.subTest(message=message, kwargs={k: str(v)[:20] for k, v in kwargs.items()}):
                with self.assertRaises(StampError) as cm:
                    stamps.validate_stamp(Stamp(**kwargs))
                if message.endswith(("'", ": ")):          # open-ended messages
                    self.assertTrue(str(cm.exception).startswith(message), str(cm.exception))
                else:
                    self.assertEqual(str(cm.exception), message)
        valid = [text, image, dict(text, font_size=400.0, text="J"), dict(image, width_pt=5000.0),
                 dict(text, page=None, page_anchor="first"), dict(image, page=None, all_pages=True),
                 dict(text, text="a" * 2000, font="lato", font_size=6.0), dict(text, x=-5.0, y=1e6)]
        for kwargs in valid:
            with self.subTest(valid={k: str(v)[:20] for k, v in kwargs.items()}):
                stamps.validate_stamp(Stamp(**kwargs))

    def test_bundled_font_missing_is_reported(self):
        stamp = Stamp(kind="text", text="Jane", font="lato", page=1, x=0.0, y=0.0)
        with mock.patch.object(sys, "_MEIPASS", str(self.tmp), create=True):
            with self.assertRaises(StampError) as cm:
                stamps.validate_stamp(stamp)
        self.assertTrue(str(cm.exception).startswith("bundled font file missing: "))

    def test_validate_unplaced_content_only(self):
        unplaced = Stamp(kind="text", text="Jane")
        stamps.validate_stamp(unplaced, placed=False)
        with self.assertRaises(StampError):
            stamps.validate_stamp(unplaced)
        half = Stamp(kind="image", image_path=make_png(self.p("s.png")), page=1, x=3.0)
        stamps.validate_stamp(half, placed=False)
        with self.assertRaises(StampError):
            stamps.validate_stamp(half)
        with self.assertRaises(StampError) as cm:           # content rules still run
            stamps.validate_stamp(Stamp(kind="text", text=""), placed=False)
        self.assertEqual(str(cm.exception), "text is empty")

    def test_validate_placement_ignores_content(self):
        # a saved placement stays judgeable while its image file is missing
        gone = Stamp(kind="image", image_path=self.p("gone.png"), page=2, x=1.0, y=2.0)
        stamps.validate_placement(gone)
        with self.assertRaises(StampError) as cm:
            stamps.validate_placement(dataclasses.replace(gone, y=None))
        self.assertEqual(str(cm.exception), "x and y are required")

    def test_page_indexes(self):
        self.assertEqual(Stamp(page=1).page_indexes(3), [0])
        self.assertEqual(Stamp(page=3).page_indexes(3), [2])
        self.assertEqual(Stamp(page_anchor="first").page_indexes(3), [0])
        self.assertEqual(Stamp(page_anchor="last").page_indexes(3), [2])
        self.assertEqual(Stamp(page_anchor="last").page_indexes(1), [0])
        self.assertEqual(Stamp(all_pages=True).page_indexes(3), [0, 1, 2])
        for page in (0, 4, -1):
            with self.subTest(page=page):
                with self.assertRaises(StampError) as cm:
                    Stamp(page=page).page_indexes(3)
                self.assertEqual(str(cm.exception),
                                 f"page {page} out of range (the document has 3 page(s))")
        with self.assertRaises(StampError) as cm:
            Stamp().page_indexes(3)
        self.assertEqual(str(cm.exception), "the signature has no page target")
        with self.assertRaises(StampError) as cm:
            Stamp(page=1).page_indexes(0)
        self.assertEqual(str(cm.exception), "the document has no page")
        with self.assertRaises(StampError):
            Stamp(page_anchor="middle").page_indexes(3)

    def test_describe_and_labels(self):
        text = Stamp(kind="text", text="\n  Jane Doe \nRead and approved", page_anchor="last",
                     x=360.4, y=119.6)
        self.assertTrue(text.is_placed)
        self.assertEqual(text.target_label(), "last page")
        self.assertEqual(text.describe(), "text last page @ (360, 120)")
        self.assertEqual(text.content_label(), '"Jane Doe"')
        image = Stamp(kind="image", image_path=Path("/x/y/stamp.png"), page=2, x=1.0, y=2.0)
        self.assertEqual(image.describe(), "image page 2 @ (1, 2)")
        self.assertEqual(image.content_label(), "stamp.png")
        self.assertEqual(Stamp(kind="image").content_label(), "?")
        self.assertEqual(Stamp(kind="text", text="JD", all_pages=True, x=540.0, y=20.0).describe(),
                         "text all pages @ (540, 20)")
        self.assertEqual(Stamp(kind="text", page_anchor="first").target_label(), "first page")
        unplaced = Stamp(kind="text", text="x")
        self.assertFalse(unplaced.is_placed)
        self.assertEqual(unplaced.target_label(), "unplaced")
        self.assertEqual(unplaced.describe(), "text unplaced")
        long = Stamp(kind="text", text="A" * 31)
        self.assertEqual(long.content_label(), '"' + "A" * 30 + '…"')
        self.assertEqual(Stamp(kind="text", text="A" * 30).content_label(), '"' + "A" * 30 + '"')
        # placed needs a position AND exactly one target
        self.assertFalse(Stamp(page=1, x=1.0).is_placed)
        self.assertFalse(Stamp(x=1.0, y=1.0).is_placed)
        self.assertFalse(Stamp(page=1, all_pages=True, x=1.0, y=1.0).is_placed)

    def test_content_key(self):
        a = Stamp(kind="text", text="{filename} {date}", color="#aa00ff", page=1, x=1.0, y=2.0)
        b = Stamp(kind="text", text="{filename} {date}", color="#AA00FF", all_pages=True)
        key = stamps.content_key(a, filename="doc", date=DAY)
        self.assertEqual(key, ("text", "doc 07/10/2026", "great-vibes", 24.0, "#AA00FF"))
        self.assertEqual(key, stamps.content_key(b, filename="doc", date=DAY))  # placement excluded
        self.assertNotEqual(key, stamps.content_key(a, filename="other", date=DAY))
        img = Stamp(kind="image", image_path=Path("/x/s.png"), width_pt=80)
        self.assertEqual(stamps.content_key(img), ("image", "/x/s.png", 80.0))
        hash(key)


class SignaturesFile(StampCase):
    def write(self, entries, name="sigs.json", encoding="utf-8") -> Path:
        path = self.p(name)
        path.write_text(json.dumps(entries), encoding=encoding)
        return path

    def assert_rejected(self, path, fragment):
        with self.assertRaises(StampError) as cm:
            stamps.load_stamps_file(path)
        self.assertIn(fragment, str(cm.exception))
        return str(cm.exception)

    def test_load_list(self):
        make_png(self.p("stamp.png"))
        path = self.write([
            {"kind": "text", "text": "Jane Doe\nRead and approved, {date}",
             "font": "great-vibes", "color": "#1A2B8C", "font_size": 24,
             "page_anchor": "last", "x": 360, "y": 120},
            {"kind": "text", "text": "JD", "font": "caveat", "font_size": 14,
             "all_pages": True, "x": 540, "y": 20},
            {"kind": "image", "image_path": "stamp.png", "width_pt": 120,
             "page": 1, "x": 60, "y": 700},
        ])
        loaded = stamps.load_stamps_file(path)
        self.assertEqual([s.describe() for s in loaded], [
            "text last page @ (360, 120)", "text all pages @ (540, 20)",
            "image page 1 @ (60, 700)"])
        self.assertTrue(all(s.is_placed for s in loaded))
        self.assertEqual(loaded[0].text, "Jane Doe\nRead and approved, {date}")
        self.assertEqual(stamps.load_stamps_file(self.write([], name="empty.json")), [])

    def test_relative_image_resolved_against_file_dir(self):
        sub = self.p("conf")
        sub.mkdir()
        png = make_png(sub / "stamp.png")
        (sub / "sigs.json").write_text(json.dumps(
            [{"kind": "image", "image_path": "stamp.png", "page": 1, "x": 0, "y": 0}]),
            encoding="utf-8")
        loaded = stamps.load_stamps_file(sub / "sigs.json")     # cwd is NOT sub
        self.assertEqual(loaded[0].image_path, png)

    def test_disabled_entries_skipped(self):
        path = self.write([
            {"kind": "text", "text": "kept", "page": 1, "x": 0, "y": 0, "enabled": True},
            # skipped BEFORE validation: it may even be invalid
            {"kind": "text", "text": "", "enabled": False},
            {"kind": "text", "text": "also kept", "page": 1, "x": 0, "y": 0},
        ])
        self.assertEqual([s.text for s in stamps.load_stamps_file(path)], ["kept", "also kept"])
        bad = self.write([{"kind": "text", "text": "x", "page": 1, "x": 0, "y": 0,
                           "enabled": "no"}], name="bad.json")
        msg = self.assert_rejected(bad, "'enabled' must be true or false")
        self.assertEqual(msg, "bad.json: signature #1: 'enabled' must be true or false")

    def test_profile_extras_ignored(self):
        path = self.write([{"id": "3f9a1c0b77de", "label": "Jane Doe", "enabled": True,
                            "kind": "text", "text": "Jane Doe", "font": "great-vibes",
                            "color": "#1A2B8C", "font_size": 24.0,
                            "page": 2, "x": 360.0, "y": 120.0, "placed_on": [595.0, 842.0]}])
        self.assertEqual(stamps.load_stamps_file(path),
                         [Stamp(kind="text", text="Jane Doe", page=2, x=360.0, y=120.0)])

    def test_not_a_list_rejected(self):
        for value in ({"kind": "text"}, "x", 3, None):
            with self.subTest(value=value):
                path = self.write(value)
                msg = self.assert_rejected(path, "expected a JSON list of signatures")
                self.assertEqual(msg, f"{path}: expected a JSON list of signatures")

    def test_bad_json_rejected(self):
        path = self.p("bad.json")
        path.write_text("[{", encoding="utf-8")
        msg = self.assert_rejected(path, "invalid JSON")
        self.assertTrue(msg.startswith(f"invalid JSON in {path}: "), msg)

    def test_missing_file_rejected(self):
        path = self.p("nope.json")
        msg = self.assert_rejected(path, "cannot read signatures file")
        self.assertTrue(msg.startswith(f"cannot read signatures file {path}: "), msg)

    def test_error_names_file_and_entry_number(self):
        ok = {"kind": "text", "text": "x", "page": 1, "x": 0, "y": 0}
        cases = [
            ([ok, {"kind": "text", "text": "x", "x": 0, "y": 0}],
             "sigs.json: signature #2: give exactly one page target: "
             "page, page_anchor or all_pages"),
            ([ok, ok, {"kind": "image", "page": 1, "x": 0, "y": 0}],
             "sigs.json: signature #3: image_path is required"),
            ([{"text": "x"}], "sigs.json: signature #1: missing 'kind'"),
            ([ok, "nope"], "sigs.json: signature #2: expected an object"),
            ([{"kind": "text", "text": "x", "page": "1", "x": 0, "y": 0}],
             "sigs.json: signature #1: 'page' must be an integer"),
        ]
        for entries, message in cases:
            with self.subTest(message=message):
                self.assertEqual(self.assert_rejected(self.write(entries), "signature #"), message)

    def test_unknown_font_id_reported_as_typed(self):
        entry = {"kind": "text", "text": "x", "page": 1, "x": 0, "y": 0}
        msg = self.assert_rejected(self.write([dict(entry, font="latoo")]), "unknown font")
        # the id as typed (not joined to the folder of the JSON file) + the valid ids
        self.assertTrue(msg.startswith("sigs.json: signature #1: unknown font 'latoo' "), msg)
        self.assertNotIn(str(self.tmp), msg)
        for font_id in stamps.BUNDLED_FONTS:
            self.assertIn(font_id, msg)
        # a relative PATH is still looked up next to the JSON file, and says where
        msg = self.assert_rejected(self.write([dict(entry, font="sub/mine.ttf")]), "unknown font")
        self.assertIn(str(self.p("sub") / "mine.ttf"), msg)
        shutil.copy(stamps.resolve_font("lato"), self.p("mine"))     # a bare-word font file
        loaded = stamps.load_stamps_file(self.write([dict(entry, font="mine")]))
        self.assertEqual(loaded[0].font, str(self.p("mine")))

    def test_utf8_bom_accepted(self):
        path = self.write([{"kind": "text", "text": "Sébastien", "page": 1, "x": 0, "y": 0}],
                          encoding="utf-8-sig")
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(stamps.load_stamps_file(path)[0].text, "Sébastien")

    def test_non_ascii_text_roundtrip(self):
        path = self.p("sigs.json")
        path.write_bytes(json.dumps(
            [{"kind": "text", "text": "Sébastien — Ünï", "page": 1, "x": 0, "y": 0}],
            ensure_ascii=False).encode("utf-8"))
        self.assertIn("Sébastien".encode("utf-8"), path.read_bytes())
        self.assertEqual(stamps.load_stamps_file(path)[0].text, "Sébastien — Ünï")

    def test_not_utf8_rejected(self):
        path = self.p("latin1.json")
        path.write_bytes(b'[{"text": "S\xe9b"}]')
        self.assertEqual(self.assert_rejected(path, "not valid UTF-8"),
                         f"{path}: not valid UTF-8")

    def test_deeply_nested_json_rejected(self):
        path = self.p("deep.json")
        path.write_text("[" * 200000, encoding="utf-8")
        msg = self.assert_rejected(path, "invalid JSON")     # a StampError, no RecursionError
        self.assertTrue(msg.startswith("invalid JSON"), msg[:80])

    def test_nan_coordinates_rejected(self):
        path = self.p("nan.json")
        path.write_text('[{"kind": "text", "text": "x", "page": 1, "x": NaN, "y": 0}]',
                        encoding="utf-8")
        self.assertEqual(self.assert_rejected(path, "finite"),
                         "nan.json: signature #1: x and y must be finite numbers")


class ApplyStamps(StampCase):
    def text(self, **kwargs):
        return Stamp(kind="text", text="Jane Doe", **kwargs)

    def counts(self, pdf):
        return [len(page_xobjects(page)) for page in pdf_pages(pdf)]

    def test_one_xobject_per_target_page(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)] * 3)
        png = make_png(self.p("sig.png"))
        out = self.stamped(pdf, [
            self.text(page=1, x=50.0, y=50.0),
            Stamp(kind="image", image_path=png, page=1, x=300.0, y=50.0),
            self.text(page=3, x=50.0, y=50.0),
        ])
        self.assertEqual(self.counts(out), [2, 0, 1])
        self.assertEqual(self.counts(pdf), [0, 0, 0])        # the input is untouched

    def test_all_pages_single_image(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)] * 5)
        out = self.stamped(pdf, [self.text(all_pages=True, x=540.0, y=20.0)])
        xobjects = [page_xobjects(page) for page in pdf_pages(out)]
        self.assertEqual([len(x) for x in xobjects], [1] * 5)
        # ONE image XObject shared by the five pages (not five copies)
        self.assertEqual(len({ref for x in xobjects for ref in x.values()}), 1)

    def test_pages_sharing_one_resources_dictionary(self):
        # two pages pointing at the SAME indirect /Resources: each page needs
        # its own XObject name (pyHanko refuses a duplicate name)
        page = b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents %d 0 R/Resources 7 0 R>>"
        pdf = write_pdf_objects(self.p("shared.pdf"), [
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[3 0 R 5 0 R]/Count 2>>",
            page % 4, pdf_stream(b"q Q\n"), page % 6, pdf_stream(b"q Q\n"),
            b"<</ProcSet[/PDF]>>"])
        out = self.stamped(pdf, [self.text(all_pages=True, x=100.0, y=100.0)])
        xobjects = [page_xobjects(p) for p in pdf_pages(out)]
        self.assertEqual(xobjects[0], xobjects[1])           # the shared dictionary
        self.assertEqual(len(xobjects[0]), 2)                # one name per page...
        self.assertEqual(len(set(xobjects[0].values())), 1)  # ...for one image

    def test_indirect_contents_array_with_direct_resources(self):
        # /Contents -> an indirect ARRAY: pyHanko then updates the array only,
        # so the /Resources it adds to the page must be written explicitly
        for label, resources in (("direct", b"/Resources<</ProcSet[/PDF]>>"), ("absent", b"")):
            with self.subTest(resources=label):
                pdf = write_pdf_objects(self.p(f"{label}.pdf"), [
                    b"<</Type/Catalog/Pages 2 0 R>>",
                    b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
                    b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R"
                    + resources + b">>",
                    b"[5 0 R]", pdf_stream(b"q Q\n")])
                out = self.stamped(pdf, [self.text(page=1, x=100.0, y=100.0)])
                page = pdf_pages(out)[0]
                (name,) = page_xobjects(page)                # the XObject reached the file
                data = b" ".join(part.get_object().data for part in page["/Contents"])
                self.assertIn(name.encode() + b" Do", data)

    def test_pages_sharing_one_contents_array(self):
        # two pages pointing at the SAME indirect /Contents array: a stamp
        # for page 1 must not reach page 2 (pyHanko appends to it in place)
        page = b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R%s>>"
        for label, resources in (("own", b"/Resources<</ProcSet[/PDF]>>"),
                                 ("shared", b"/Resources 6 0 R")):
            with self.subTest(resources=label):
                pdf = write_pdf_objects(self.p(f"{label}.pdf"), [
                    b"<</Type/Catalog/Pages 2 0 R>>",
                    b"<</Type/Pages/Kids[3 0 R 7 0 R]/Count 2>>",
                    page % resources, b"[5 0 R]", pdf_stream(b"q Q\n"),
                    b"<</ProcSet[/PDF]>>", page % resources])
                out = self.stamped(pdf, [self.text(page=1, x=100.0, y=100.0),
                                         self.text(page=1, x=100.0, y=300.0)])
                painted = [b" ".join(part.get_object().data for part in page["/Contents"])
                           .count(b" Do") for page in pdf_pages(out)]
                self.assertEqual(painted, [2, 0])

    def test_anchor_last_on_shorter_document(self):
        last = self.text(page_anchor="last", x=50.0, y=50.0)
        first = self.text(page_anchor="first", x=50.0, y=50.0)
        one = make_pdf(self.p("one.pdf"), [(595, 842)])
        three = make_pdf(self.p("three.pdf"), [(595, 842)] * 3)
        self.assertEqual(self.counts(self.stamped(one, [last])), [1])
        self.assertEqual(self.counts(self.stamped(three, [last])), [0, 0, 1])
        self.assertEqual(self.counts(self.stamped(three, [first])), [1, 0, 0])
        self.assertEqual(self.counts(self.stamped(one, [first, last])), [2])

    def test_page_out_of_range_before_any_write(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)] * 2)
        good = self.text(page=1, x=50.0, y=50.0)
        bad = self.text(page=5, x=50.0, y=50.0)
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            with self.assertRaises(StampError) as cm:
                stamps.apply_stamps(writer, [good, bad])
            self.assertEqual(str(cm.exception),
                             "page 5 out of range (the document has 2 page(s))")
            out = io.BytesIO()
            writer.write(out)
        # the valid FIRST stamp was not applied either: nothing half-stamped
        after = self.p("after.pdf")
        after.write_bytes(out.getvalue())
        self.assertEqual(self.counts(after), [0, 0])

    def test_unreadable_image_before_any_write(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)])
        broken = self.p("broken.png")
        broken.write_text("nope", encoding="utf-8")
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            with self.assertRaises(StampError):
                stamps.apply_stamps(writer, [
                    self.text(page=1, x=50.0, y=50.0),
                    Stamp(kind="image", image_path=broken, page=1, x=0.0, y=0.0)])
            out = io.BytesIO()
            writer.write(out)
        after = self.p("after.pdf")
        after.write_bytes(out.getvalue())
        self.assertEqual(self.counts(after), [0])

    def test_unplaced_raises(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)])
        for stamp in (self.text(), self.text(page=1), self.text(x=1.0, y=1.0),
                      self.text(page=1, page_anchor="last", x=1.0, y=1.0)):
            with self.subTest(stamp=stamp.describe()):
                with self.assertRaises(StampError) as cm:
                    self.stamped(pdf, [stamp])
                self.assertEqual(str(cm.exception), "the signature is not placed")

    def test_empty_list_is_a_noop(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)])
        self.assertEqual(self.counts(self.stamped(pdf, [])), [0])

    def test_page_without_contents_is_stamped(self):
        pdf = strip_page_contents(make_pdf(self.p("a.pdf"), [(595, 842)] * 2), 1)
        before = pdf_pages(pdf)
        self.assertIn("/Contents", before[0])
        self.assertNotIn("/Contents", before[1])             # the fixture really is blank
        out = self.stamped(pdf, [self.text(all_pages=True, x=100.0, y=100.0)])
        pages = pdf_pages(out)
        self.assertIn("/Contents", pages[1])
        self.assertEqual(self.counts(out), [1, 1])
        img = core.render_page_image(out, 1, 595)            # 1 px per point
        if img is not None:                                  # pypdfium2 / pdftoppm available
            box = img.crop((100, 842 - 160, 300, 842 - 100))
            self.assertTrue(any(near(px, INK) for _, px in box.getcolors(100000)))

    def test_existing_signature_count_zero_for_unsigned(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)] * 2)
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            self.assertEqual(stamps.existing_signature_count(writer), 0)
            # read-only: stamps applied afterwards still work, count unchanged
            stamps.apply_stamps(writer, [self.text(page=1, x=10.0, y=10.0)])
            self.assertEqual(stamps.existing_signature_count(writer), 0)

    def test_empty_signature_field_is_not_a_signature(self):
        # a form prepared for signing: the field exists but nothing is signed
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)])
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            append_signature_field(
                writer, SigFieldSpec("ToBeSigned", on_page=0, box=(10, 10, 110, 60)))
            out = io.BytesIO()
            writer.write(out)
        prepared = self.p("prepared.pdf")
        prepared.write_bytes(out.getvalue())
        with prepared.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            self.assertEqual(len(list(enumerate_sig_fields(writer.prev))), 1)   # the fixture
            self.assertEqual(stamps.existing_signature_count(writer), 0)

    def test_page_count_and_mediabox(self):
        pdf = make_pdf(self.p("a.pdf"), [(200, 300), (400, 500), (612, 792)])
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            self.assertEqual(stamps.page_count(writer), 3)
            self.assertEqual(stamps.page_mediabox(writer, 0), [0.0, 0.0, 200.0, 300.0])
            self.assertEqual(stamps.page_mediabox(writer, -1), [0.0, 0.0, 612.0, 792.0])

    def test_static_content_cached_once(self):
        cache: dict = {}
        stamp = Stamp(kind="text", text="Jane Doe, {date}", page=1, x=50.0, y=50.0)
        with mock.patch.object(stamps, "render_text_image",
                               wraps=stamps.render_text_image) as spy:
            for name in ("a", "b", "c"):
                pdf = make_pdf(self.p(f"{name}.pdf"), [(595, 842)])
                out = self.stamped(pdf, [stamp], filename=name, date=DAY, cache=cache)
                self.assertEqual(self.counts(out), [1])
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(list(cache), [stamps.content_key(stamp, date=DAY)])

    def test_image_content_cached_once(self):
        cache: dict = {}
        stamp = Stamp(kind="image", image_path=make_png(self.p("s.png")), all_pages=True,
                      x=5.0, y=5.0)
        with mock.patch.object(stamps, "load_image", wraps=stamps.load_image) as spy:
            for name in ("a", "b"):
                self.stamped(make_pdf(self.p(f"{name}.pdf"), [(595, 842)] * 2), [stamp],
                             filename=name, cache=cache)
        self.assertEqual(spy.call_count, 1)

    def test_filename_text_not_cached(self):
        cache: dict = {}
        stamp = Stamp(kind="text", text="Ref {filename}", page=1, x=50.0, y=50.0)
        with mock.patch.object(stamps, "render_text_image",
                               wraps=stamps.render_text_image) as spy:
            for name in ("a", "b", "c"):
                pdf = make_pdf(self.p(f"{name}.pdf"), [(595, 842)])
                self.stamped(pdf, [stamp], filename=name, date=DAY, cache=cache)
        self.assertEqual(spy.call_count, 3)
        self.assertEqual([call.args[0] for call in spy.call_args_list],
                         ["Ref a", "Ref b", "Ref c"])
        self.assertEqual(cache, {})                          # no growth over the batch

    def paint_matrix(self, pdf, page_index=0) -> list[float]:
        """[w, h, x, y] of the only stamp painted on a page: the operands of
        its ``w 0 0 h x y cm`` matrix."""
        contents = pdf_pages(pdf)[page_index]["/Contents"]
        data = b" ".join(part.get_object().data for part in contents)
        (found,) = re.findall(rb"q (\S+) 0 0 (\S+) (\S+) (\S+) cm /Stamp\w+ Do Q", data)
        return [float(v) for v in found]

    def test_float_coordinates_accepted(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)])
        out = self.stamped(pdf, [self.text(page=1, x=100.5, y=200.25)])
        self.assertEqual(self.paint_matrix(out)[2:], [100.5, 200.25])    # not rounded to integers

    def test_stamped_size_is_the_content_size(self):
        # the size is NOT truncated to whole points (a pyHanko stamp box would
        # be): what is previewed is what gets stamped, down to under 1 pt
        thin = self.p("rule.png")
        Image.new("RGB", (2000, 1), (0, 0, 0)).save(thin)
        for stamp in (
                Stamp(kind="text", text="JD", font="lato", font_size=8, page=1, x=100.0, y=400.0),
                Stamp(kind="text", text="__________", font="lato", font_size=12,
                      page=1, x=100.0, y=400.0),
                Stamp(kind="image", image_path=thin, width_pt=150.0, page=1, x=100.0, y=400.0)):
            with self.subTest(stamp=stamp.content_label()):
                _, (w, h) = stamps.stamp_content(stamp)
                self.assertNotEqual((w, h), (int(w), int(h)))        # a fractional size
                pdf = make_pdf(self.p("a.pdf"), [(595, 842)])
                matrix = self.paint_matrix(self.stamped(pdf, [stamp]))
                self.assertAlmostEqual(matrix[0], w, places=3)
                self.assertAlmostEqual(matrix[1], h, places=3)
                self.assertEqual(matrix[2:], [100.0, 400.0])
        self.assertLess(h, 1.0)                                      # the rule: under 1 pt high

    def test_position_is_relative_to_the_mediabox_origin(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)], origin=(100, 200))
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            self.assertEqual(stamps.page_mediabox(writer, 0), [100.0, 200.0, 695.0, 1042.0])
        out = self.stamped(pdf, [self.text(page=1, x=50.0, y=60.0)])
        self.assertEqual(self.paint_matrix(out)[2:], [150.0, 260.0])

    def test_mediabox_inherited_from_the_page_tree(self):
        # the page has no /MediaBox of its own: the one of /Pages applies
        pdf = write_pdf_objects(self.p("inherited.pdf"), [
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[3 0 R]/Count 1/MediaBox[10 20 410 620]>>",
            b"<</Type/Page/Parent 2 0 R/Contents 4 0 R/Resources<<>>>>",
            pdf_stream(b"q Q\n")])
        with pdf.open("rb") as inf:
            writer = IncrementalPdfFileWriter(inf, strict=False)
            self.assertEqual(stamps.page_mediabox(writer, 0), [10.0, 20.0, 410.0, 620.0])
        out = self.stamped(pdf, [self.text(page=1, x=50.0, y=60.0)])
        self.assertEqual(self.paint_matrix(out)[2:], [60.0, 80.0])

    def test_rendered_pixels(self):
        pdf = make_pdf(self.p("a.pdf"), [(595, 842)])
        # 200x100 px: left half opaque red, right half fully transparent
        png = self.p("half.png")
        im = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
        im.paste((255, 0, 0, 255), (0, 0, 100, 100))
        im.save(png)
        stamp_text = self.text(page=1, x=300.0, y=400.0)
        _, (tw, th) = stamps.stamp_content(stamp_text)
        # 30x15 px, fewer pixels than its 200x100 pt box: it must be ENLARGED
        small = self.p("small.png")
        Image.new("RGB", (30, 15), (0, 0, 255)).save(small)
        out = self.stamped(pdf, [
            Stamp(kind="image", image_path=png, width_pt=100.0, page=1, x=100.0, y=100.0),
            stamp_text,
            Stamp(kind="image", image_path=small, width_pt=200.0, page=1, x=100.0, y=600.0)])
        img = core.render_page_image(out, 0, 595)            # 1 px per point, y downwards
        if img is None:
            self.skipTest("no PDF renderer available (pypdfium2 / pdftoppm)")
        white = (255, 255, 255)
        # image: 100x50 pt box at (100, 100) -> rows 692..742
        self.assertTrue(near(img.getpixel((125, 842 - 125)), (255, 0, 0)))    # opaque half
        self.assertTrue(near(img.getpixel((175, 842 - 125)), white, 6))       # page shows through
        self.assertTrue(near(img.getpixel((125, 842 - 170)), white, 6))       # above the box
        self.assertTrue(near(img.getpixel((80, 842 - 125)), white, 6))        # left of the box
        # text: ink inside its own box, in the chosen colour; none just outside
        box = img.crop((300, round(842 - 400 - th), round(300 + tw), 842 - 400))
        self.assertTrue(any(near(px, INK) for _, px in box.getcolors(100000)))
        margin = img.crop((300, round(842 - 400 - th) - 40, round(300 + tw),
                           round(842 - 400 - th) - 4))
        self.assertTrue(all(near(px, white, 6) for _, px in margin.getcolors(100000)))
        # the small image fills its whole box (both corners), not its natural 30x15 pt
        self.assertTrue(near(img.getpixel((105, 842 - 605)), (0, 0, 255)))
        self.assertTrue(near(img.getpixel((295, 842 - 695)), (0, 0, 255)))
        # no frame around either element
        self.assertTrue(near(img.getpixel((199, 842 - 125)), white, 6))       # right edge
        self.assertTrue(near(img.getpixel((175, 842 - 149)), white, 6))       # top edge
        self.assertTrue(near(img.getpixel((101, 842 - 125)), (255, 0, 0)))    # left edge: the image


class StrokeRendering(unittest.TestCase):
    def test_strokes_give_transparent_rgba(self):
        strokes = [[(10, 10), (60, 40), (110, 10)], [(10, 50), (110, 50)]]
        img = stamps.render_strokes_image(strokes, "#0000FF", width=3.0, scale=4)
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.convert("RGB").getcolors(), [(img.width * img.height, (0, 0, 255))])
        alpha = img.getchannel("A")
        self.assertEqual(alpha.getbbox(), (0, 0) + img.size)  # tightly cropped
        self.assertEqual(alpha.getpixel((img.width // 2, 2)), 0)          # between the strokes
        self.assertEqual(alpha.getpixel((img.width // 2, img.height - 6)), 255)   # on the line
        # scale x (bounding box of the points + the pen width)
        self.assertAlmostEqual(img.width, 4 * (100 + 3), delta=8)
        self.assertAlmostEqual(img.height, 4 * (40 + 3), delta=8)
        double = stamps.render_strokes_image(strokes, "#0000FF", width=3.0, scale=8)
        self.assertAlmostEqual(double.width / img.width, 2.0, delta=0.1)

    def test_single_point_stroke_draws_a_dot(self):
        img = stamps.render_strokes_image([[(50, 50)]], "#000000", width=3.0, scale=4)
        self.assertAlmostEqual(img.width, 12, delta=3)
        self.assertAlmostEqual(img.height, 12, delta=3)
        self.assertEqual(img.getchannel("A").getpixel((img.width // 2, img.height // 2)), 255)
        # points outside the canvas (negative coordinates) are kept
        off = stamps.render_strokes_image([[(-20, -5), (30, 10)]], "#000000")
        self.assertAlmostEqual(off.width, 4 * (50 + 3), delta=8)

    def test_empty_raises(self):
        for strokes in ([], [[]], [[], []]):
            with self.subTest(strokes=strokes):
                with self.assertRaises(StampError) as cm:
                    stamps.render_strokes_image(strokes, "#000000")
                self.assertEqual(str(cm.exception), "nothing was drawn")
        with self.assertRaises(StampError) as cm:
            stamps.render_strokes_image([[(1, 1)]], "black")
        self.assertEqual(str(cm.exception), "bad color 'black' (expected #RRGGBB)")

    def test_oversized_drawing_raises_stamp_error(self):
        # two points a few screens apart: the mask would be gigapixels
        real_new = Image.new
        for strokes in ([[(0, 0), (30000, 30000)]], [[(0, 0)], [(1e9, 5)]],
                        [[(0, 0), (float("inf"), 5)]]):
            with self.subTest(strokes=strokes):
                with mock.patch.object(stamps.Image, "new", side_effect=real_new) as spy:
                    with self.assertRaises(StampError) as cm:
                        stamps.render_strokes_image(strokes, "#000000")
                self.assertEqual(str(cm.exception), "drawing is too large")
                spy.assert_not_called()                      # refused BEFORE any allocation
        # a large canvas crossed corner to corner still renders
        img = stamps.render_strokes_image([[(0, 0), (1000, 600)]], "#000000", scale=4)
        self.assertGreater(img.width, 3900)


class HeadlessImportNewModules(unittest.TestCase):
    def test_import_without_tkinter_or_pdfium(self):
        code = ("import stamps, profile_store, sys; "
                "print([m for m in ('tkinter', 'customtkinter', 'pypdfium2', "
                "'sign_pdfs_beid', 'gui', 'i18n') if m in sys.modules])")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(Path(__file__).parent))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
