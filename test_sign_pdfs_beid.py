#!/usr/bin/env python3
"""Headless tests (no card, no tkinter) for signApp.

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


class GuiImageEndToEnd(unittest.TestCase):
    """Regression: GUI launch → thread → queue → table path (image mode).

    Since Tkinter is not thread-safe, the worker must NOT call `after()`;
    it pushes onto a queue drained by the main thread. Skipped if
    tkinter/display is unavailable (e.g. headless CI)."""

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
        self.assertEqual(str(app.launch_btn.cget("state")), "disabled")  # locked during
        for _ in range(120):                       # pump the Tk loop (max ~6 s)
            app.update()
            time.sleep(0.05)
            if app.status_lbl.cget("text").startswith(("Done", "Error")):
                break
        app.update()
        status = app.status_lbl.cget("text")
        n_rows = len(app.summary_table.get_children())
        btn_state = str(app.launch_btn.cget("state"))
        app.destroy()
        self.assertTrue(status.startswith("Done"), status)      # not "Error" / not frozen
        self.assertEqual(n_rows, 1)
        self.assertEqual(btn_state, "normal")                   # re-enabled afterwards
        self.assertTrue((out / "t_signe.pdf").exists())

    def test_run_batch_reports_systemexit_without_hanging(self):
        # open_eid_session() raises SystemExit (no reader/card); the worker
        # must catch it and publish an error, not die silently.
        import gui
        app = gui.SignApp(self.types.SimpleNamespace(lib=None))
        app.update()
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


class GuiBeidPlacement(unittest.TestCase):
    """GUI-side refinements 1/4/5 in beid mode: 3:1 placeholder (1/5 page),
    no image picker, canvas that follows the window. Skipped without a display."""

    def setUp(self):
        import types
        try:
            import tkinter
            tkinter.Tk().destroy()
            import gui  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"tkinter/GUI unavailable: {exc}")
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
        # beid mode: no image picker (row not handled by a manager)
        self.assertEqual(app.image_row.winfo_manager(), "")
        # placeholder = 3:1 vignette, width = page/5
        iw, ih = app._placeholder_size_pt()
        self.assertAlmostEqual(iw, 600 / 5)
        self.assertAlmostEqual(iw / ih, 3.0)
        # a click sets the position (in beid mode too, not just image)
        fw, fh, ox, oy = app._frame_geom
        app._on_canvas_click(self.types.SimpleNamespace(x=ox + fw * 0.5, y=oy + fh * 0.5))
        self.assertIsNotNone(app.place_x)
        big = app.canvas.winfo_reqwidth()
        # shrink the window -> the canvas shrinks, proportions preserved
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
