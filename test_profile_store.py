#!/usr/bin/env python3
"""Headless tests for profile_store.py (user profile + signature image store).

Every test works in its own TemporaryDirectory with EXPLICIT directories:
nothing here may read or write the real user profile.
Run with:  ./venv/bin/python -m unittest -v test_profile_store
"""

import dataclasses
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import profile_store
import stamps as stamplib
from profile_store import Placement, Profile, ProfileStore, Settings, Signature
from stamps import Stamp
from test_sign_pdfs_beid import make_png
from test_stamps import damaged_font

A4 = (595.0, 842.0)
LETTER = (612.0, 792.0)
SETTING_KEYS = {"language", "mode", "pades_level", "output_dir", "azure_vault_url",
                "azure_auth", "azure_trust_anchors", "vignette"}


class StoreCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.store = ProfileStore(config_dir=self.tmp / "cfg", data_dir=self.tmp / "data")

    def write_profile(self, content) -> Path:
        """Put `content` (bytes, text or a JSON-able object) in place of profile.json."""
        path = self.store.profile_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content if isinstance(content, str) else json.dumps(content),
                            encoding="utf-8")
        return path

    def load_signatures(self, entries) -> list:
        self.write_profile({"version": 1, "signatures": entries})
        return self.store.load().signatures

    def assert_set_aside(self, original: bytes):
        """The unusable file went to profile.json.bak, byte for byte."""
        bak = self.store.profile_path.with_name("profile.json.bak")
        self.assertFalse(self.store.profile_path.exists())
        self.assertEqual(bak.read_bytes(), original)

    def text_entry(self, **extra) -> dict:
        return {"kind": "text", "text": "Jane Doe", **extra}


class Dirs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def env(self, **values):
        """os.environ without the two override variables, plus `values`."""
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in (profile_store.ENV_CONFIG_DIR, profile_store.ENV_DATA_DIR):
            if name not in values:
                os.environ.pop(name, None)

    def test_env_override(self):
        self.env(CACHET_CONFIG_DIR=str(self.tmp / "c"), CACHET_DATA_DIR=str(self.tmp / "d"))
        self.assertEqual(profile_store.resolve_config_dir(), self.tmp / "c")
        self.assertEqual(profile_store.resolve_data_dir(), self.tmp / "d")

    def test_explicit_argument_beats_env(self):
        self.env(CACHET_CONFIG_DIR=str(self.tmp / "c"), CACHET_DATA_DIR=str(self.tmp / "d"))
        self.assertEqual(profile_store.resolve_config_dir(self.tmp / "x"), self.tmp / "x")
        self.assertEqual(profile_store.resolve_data_dir(str(self.tmp / "y")), self.tmp / "y")

    def test_default_uses_platformdirs(self):
        import platformdirs

        self.env(CACHET_CONFIG_DIR="", CACHET_DATA_DIR="")      # empty = not set
        with mock.patch.object(platformdirs, "user_config_dir",
                               return_value=str(self.tmp / "pc")) as cfg, \
                mock.patch.object(platformdirs, "user_data_dir",
                                  return_value=str(self.tmp / "pd")) as data:
            self.assertEqual(profile_store.resolve_config_dir(), self.tmp / "pc")
            self.assertEqual(profile_store.resolve_data_dir(), self.tmp / "pd")
            store = ProfileStore()
        cfg.assert_called_with("Cachet", appauthor=False)
        data.assert_called_with("Cachet", appauthor=False)
        self.assertEqual((store.config_dir, store.data_dir), (self.tmp / "pc", self.tmp / "pd"))
        self.assertEqual(list(self.tmp.iterdir()), [])          # nothing created on disk

    def test_constructor_creates_nothing(self):
        store = ProfileStore(config_dir=self.tmp / "cfg", data_dir=self.tmp / "data")
        self.assertEqual(store.profile_path, self.tmp / "cfg" / "profile.json")
        self.assertEqual(store.signatures_dir, self.tmp / "data" / "signatures")
        self.assertEqual(store.load(), Profile())               # loading creates nothing either
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_default_constructor_reads_env(self):
        # Regression: parameters named config_dir/data_dir must not shadow the
        # module-level resolvers (hence their resolve_ prefix).
        self.env(CACHET_CONFIG_DIR=str(self.tmp / "c"), CACHET_DATA_DIR=str(self.tmp / "d"))
        store = ProfileStore()
        self.assertEqual((store.config_dir, store.data_dir), (self.tmp / "c", self.tmp / "d"))
        # resolved ONCE: a later change of the environment does not move the store
        os.environ[profile_store.ENV_CONFIG_DIR] = str(self.tmp / "other")
        self.assertEqual(store.config_dir, self.tmp / "c")

    def test_constructor_keywords(self):
        self.env(CACHET_CONFIG_DIR=str(self.tmp / "c"), CACHET_DATA_DIR=str(self.tmp / "d"))
        store = ProfileStore(config_dir=self.tmp / "a", data_dir=self.tmp / "b")
        self.assertEqual((store.config_dir, store.data_dir), (self.tmp / "a", self.tmp / "b"))
        mixed = ProfileStore(data_dir=self.tmp / "b")
        self.assertEqual((mixed.config_dir, mixed.data_dir), (self.tmp / "c", self.tmp / "b"))

    def test_ids(self):
        ids = {profile_store.new_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)
        self.assertTrue(all(profile_store.is_valid_id(i) for i in ids))
        for bad in ("vignette", "", "0123456789a", "0123456789abc", "0123456789AB",
                    "0123456789ag", 123456789012, None, b"0123456789ab"):
            with self.subTest(bad=bad):
                self.assertFalse(profile_store.is_valid_id(bad))


class LoadSave(StoreCase):
    def full_profile(self) -> Profile:
        stored = self.store.store_image(Image.new("RGBA", (40, 20), (0, 0, 255, 200)))
        return Profile(
            settings=Settings(
                language="fr", mode="azure", pades_level="b-lt", output_dir="/home/jane/signed",
                azure_vault_url="https://myorg-sign.vault.azure.net", azure_auth="interactive",
                azure_trust_anchors="/home/jane/ca-chain.pem",
                vignette=Placement(page=2, x=380.0, y=60.5, placed_on=A4)),
            signatures=[
                Signature(id="3f9a1c0b77de", label="Sébastien", placed_on=A4, stamp=Stamp(
                    kind="text", text="Sébastien\nRead and approved, {date}", font="caveat",
                    color="#AA0000", font_size=18.0, page=2, x=360.0, y=120.25)),
                Signature(id="b20c55e19a04", label="Initials", placed_on=LETTER, stamp=Stamp(
                    kind="image", image_path=stored, width_pt=60.0, all_pages=True,
                    x=540.0, y=20.0)),
                Signature(id="77aa01f3c2d9", label="Company stamp", enabled=False, stamp=Stamp(
                    kind="image", image_path=stored, width_pt=150.0)),
            ])

    def test_missing_file_gives_defaults(self):
        profile = self.store.load()
        self.assertEqual(profile, Profile())
        self.assertEqual(profile.signatures, [])
        self.assertTrue(all(v is None for v in dataclasses.asdict(profile.settings).values()))
        self.assertFalse(self.store.config_dir.exists())

    def test_roundtrip(self):
        profile = self.full_profile()
        self.store.save(profile)
        first = self.store.profile_path.read_bytes()
        loaded = self.store.load()
        self.assertEqual(loaded, profile)
        self.assertIsInstance(loaded.signatures[0].placed_on, tuple)
        self.assertIsInstance(loaded.settings.vignette.placed_on, tuple)
        # save -> load -> save is stable, and a fresh store reads the same thing
        self.store.save(loaded)
        self.assertEqual(self.store.profile_path.read_bytes(), first)
        again = ProfileStore(config_dir=self.store.config_dir, data_dir=self.store.data_dir)
        self.assertEqual(again.load(), profile)
        self.assertFalse(self.store.profile_path.with_name("profile.json.bak").exists())

    def test_saved_json_shape(self):
        self.store.save(self.full_profile())
        raw = self.store.profile_path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))           # UTF-8, no BOM
        self.assertIn("Sébastien".encode("utf-8"), raw)             # not \u-escaped
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(set(data), {"version", "settings", "signatures"})
        self.assertEqual(data["version"], 1)
        self.assertEqual(set(data["settings"]), SETTING_KEYS)
        self.assertEqual(data["settings"]["vignette"],
                         {"page": 2, "x": 380.0, "y": 60.5, "placed_on": [595.0, 842.0]})

        def keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keys(value)
            elif isinstance(node, list):
                for value in node:
                    yield from keys(value)

        for key in keys(data):
            for banned in ("pin", "token", "password", "secret", "key_name", "upn"):
                self.assertNotIn(banned, key.lower())
        text, image, unplaced = data["signatures"]
        self.assertEqual(text, {
            "id": "3f9a1c0b77de", "label": "Sébastien", "enabled": True, "kind": "text",
            "text": "Sébastien\nRead and approved, {date}", "font": "caveat", "color": "#AA0000",
            "font_size": 18.0, "page": 2, "x": 360.0, "y": 120.25, "placed_on": [595.0, 842.0]})
        # a stored image is written as its bare, content-addressed file name
        self.assertRegex(image["image_path"], r"^[0-9a-f]{32}\.png$")
        self.assertEqual(set(image), {"id", "label", "enabled", "kind", "image_path", "width_pt",
                                      "all_pages", "x", "y", "placed_on"})
        self.assertEqual(set(unplaced), {"id", "label", "enabled", "kind", "image_path",
                                         "width_pt"})
        # only the settings that were set are written
        self.store.save(Profile(settings=Settings(language="nl")))
        self.assertEqual(json.loads(self.store.profile_path.read_text(encoding="utf-8")),
                         {"version": 1, "settings": {"language": "nl"}, "signatures": []})

    def test_external_image_written_absolute(self):
        outside = make_png(self.tmp / "elsewhere.png")
        self.store.save(Profile(signatures=[Signature(
            id="0123456789ab", label="x", stamp=Stamp(kind="image", image_path=outside))]))
        data = json.loads(self.store.profile_path.read_text(encoding="utf-8"))
        self.assertEqual(data["signatures"][0]["image_path"], str(outside))
        self.assertEqual(self.store.load().signatures[0].stamp.image_path, outside)

    def test_corrupt_json_moved_to_bak(self):
        original = b'{"version": 1, "signatures": ['
        self.write_profile(original)
        self.assertEqual(self.store.load(), Profile())
        self.assert_set_aside(original)
        # a second corrupt file replaces the older .bak
        self.write_profile(b"again{")
        self.assertEqual(self.store.load(), Profile())
        self.assert_set_aside(b"again{")

    def test_non_object_top_level_is_corrupt(self):
        for content in ("[]", '"x"', "3", "null"):
            with self.subTest(content=content):
                self.write_profile(content)
                self.assertEqual(self.store.load(), Profile())
                self.assert_set_aside(content.encode())

    def test_missing_version_is_corrupt(self):
        for content in ({"settings": {"language": "fr"}}, {"version": "1"}, {"version": 1.0},
                        {"version": None}):
            with self.subTest(content=content):
                path = self.write_profile(content)
                original = path.read_bytes()
                self.assertEqual(self.store.load(), Profile())
                self.assert_set_aside(original)

    def test_unknown_version_moved_to_bak(self):
        path = self.write_profile({"version": 2, "settings": {"language": "fr"},
                                   "signatures": [self.text_entry()]})
        original = path.read_bytes()
        self.assertEqual(self.store.load(), Profile())
        self.assert_set_aside(original)
        # so a later save cannot destroy the newer profile
        self.store.save(Profile(settings=Settings(language="en")))
        self.assert_bak_kept(original)

    def assert_bak_kept(self, original: bytes):
        self.assertEqual(self.store.profile_path.with_name("profile.json.bak").read_bytes(),
                         original)
        self.assertTrue(self.store.profile_path.exists())

    def test_bad_entry_skipped_others_kept(self):
        sigs = self.load_signatures([
            self.text_entry(label="first"),
            "not an object",
            None,
            {"text": "no kind"},
            {"kind": "sticker", "text": "x"},
            {"kind": "text", "text": "   "},
            {"kind": "text", "text": "x", "font_size": "big"},
            {"kind": "text", "text": "x", "font_size": 0},
            {"kind": "text", "text": "x", "color": "blue"},
            {"kind": "image"},
            {"kind": "image", "image_path": 5},
            self.text_entry(label="last"),
        ])
        self.assertEqual([s.label for s in sigs], ["first", "last"])
        self.assertTrue(self.store.profile_path.exists())           # not set aside

    def test_missing_image_entry_kept(self):
        name = "9c1185a5c5e9fc54612808977ee8f548.png"
        sigs = self.load_signatures([
            {"kind": "image", "image_path": name, "width_pt": 60, "label": "Initials",
             "page": 2, "x": 540, "y": 20, "placed_on": [595, 842]},
            {"kind": "text", "text": "Jane", "font": "/nowhere/mine.ttf",
             "all_pages": True, "x": 1, "y": 2, "placed_on": [595, 842]},
        ])
        self.assertEqual(len(sigs), 2)                               # unusable, but deletable
        image, text = sigs
        self.assertEqual(image.stamp.image_path, self.store.signatures_dir / name)
        self.assertEqual(text.stamp.font, "/nowhere/mine.ttf")
        with self.assertRaises(stamplib.StampError):
            stamplib.validate_stamp(image.stamp, placed=False)
        # the content is unusable NOW; its valid placement is not thrown away
        self.assertEqual((image.stamp.page, image.stamp.x, image.stamp.y), (2, 540.0, 20.0))
        self.assertEqual(image.placed_on, A4)
        self.assertTrue(text.stamp.all_pages)
        self.assertEqual(text.placed_on, A4)
        # save -> load is an identity for it as well
        before = self.store.load()
        self.store.save(before)
        self.assertEqual(self.store.load(), before)

    def test_half_placement_cleared(self):
        sigs = self.load_signatures([
            self.text_entry(page=1, x=10, placed_on=[595, 842]),             # x without y
            self.text_entry(page=1, page_anchor="last", x=1, y=2),           # two targets
            self.text_entry(page=3, placed_on=[595, 842]),                   # no position
            self.text_entry(page=0, x=1, y=2),                               # page < 1
            self.text_entry(page_anchor="middle", x=1, y=2),                 # unknown anchor
            self.text_entry(x=1, y=2, placed_on=[595, 842]),                 # no target
            self.text_entry(all_pages=True, x=1),                            # all pages, half
            self.text_entry(),                                               # never placed
        ])
        self.assertEqual(len(sigs), 8)
        for sig in sigs:
            with self.subTest(sig=sig):
                self.assertEqual(sig.stamp, Stamp(kind="text", text="Jane Doe"))
                self.assertFalse(sig.stamp.is_placed)
                self.assertIsNone(sig.placed_on)

    def test_wrong_typed_settings_dropped(self):
        self.write_profile({"version": 1, "settings": {
            "language": 5, "mode": ["beid"], "pades_level": "b-t", "output_dir": None,
            "azure_vault_url": {"a": 1}, "azure_auth": True, "azure_trust_anchors": 1.5,
            "vignette": "bottom", "unknown": "ignored"}})
        self.assertEqual(self.store.load().settings, Settings(pades_level="b-t"))
        for settings in ("x", [], 3, None):
            with self.subTest(settings=settings):
                self.write_profile({"version": 1, "settings": settings, "signatures": "nope"})
                self.assertEqual(self.store.load(), Profile())
                self.assertTrue(self.store.profile_path.exists())   # tolerated, not corrupt

    def test_duplicate_and_missing_ids_regenerated(self):
        sigs = self.load_signatures([
            self.text_entry(id="0123456789ab"),
            self.text_entry(id="0123456789ab"),
            self.text_entry(),
            self.text_entry(id="aaaaaaaaaaaa"),
        ])
        ids = [s.id for s in sigs]
        self.assertEqual((ids[0], ids[3]), ("0123456789ab", "aaaaaaaaaaaa"))   # first one wins
        self.assertEqual(len(set(ids)), 4)
        self.assertTrue(all(profile_store.is_valid_id(i) for i in ids))

    def test_save_is_atomic(self):
        self.store.save(Profile(settings=Settings(language="fr")))
        before = self.store.profile_path.read_bytes()
        with mock.patch("profile_store.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save(Profile(settings=Settings(language="de")))
        self.assertEqual(self.store.profile_path.read_bytes(), before)
        self.assertEqual([p.name for p in self.store.config_dir.iterdir()], ["profile.json"])
        self.assertEqual(self.store.load().settings.language, "fr")

    def test_save_creates_parent_dirs(self):
        store = ProfileStore(config_dir=self.tmp / "a" / "b" / "cfg", data_dir=self.tmp / "data")
        store.save(Profile(settings=Settings(mode="image")))
        self.assertEqual(store.load().settings.mode, "image")
        self.assertFalse((self.tmp / "data").exists())              # only what is needed

    @unittest.skipUnless(os.name == "posix", "POSIX file modes")
    def test_file_mode_0600(self):
        self.store.save(Profile(settings=Settings(language="fr")))
        self.assertEqual(self.store.profile_path.stat().st_mode & 0o777, 0o600)
        self.store.save(Profile(settings=Settings(language="nl")))  # replacing keeps it private
        self.assertEqual(self.store.profile_path.stat().st_mode & 0o777, 0o600)
        stored = self.store.store_image(Image.new("RGBA", (4, 4)))
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)

    def test_load_never_raises_on_unreadable(self):
        self.store.profile_path.mkdir(parents=True)                 # a directory in its place
        self.assertEqual(self.store.load(), Profile())
        self.assertTrue(self.store.profile_path.is_dir())           # left alone
        self.assertEqual([p.name for p in self.store.config_dir.iterdir()], ["profile.json"])

    def test_save_refused_after_unreadable_load(self):
        # readable again later (a transient I/O error, a file owned by another
        # user): saving the EMPTY profile that load() fell back to would
        # destroy the library
        self.store.save(Profile(signatures=[
            Signature(id="0123456789ab", label="Jane", stamp=Stamp(kind="text", text="Jane"))]))
        before = self.store.profile_path.read_bytes()
        with mock.patch.object(Path, "read_text", side_effect=PermissionError(13, "denied")):
            profile = self.store.load()
        self.assertEqual(profile, Profile())
        profile.settings.language = "fr"
        with self.assertRaises(OSError):
            self.store.save(profile)
        self.assertEqual(self.store.profile_path.read_bytes(), before)
        self.assertEqual([p.name for p in self.store.config_dir.iterdir()], ["profile.json"])
        # the next successful load lifts the refusal
        profile = self.store.load()
        self.assertEqual([sig.label for sig in profile.signatures], ["Jane"])
        profile.settings.language = "fr"
        self.store.save(profile)
        self.assertEqual(self.store.load().settings.language, "fr")

    def test_save_refused_when_set_aside_failed(self):
        newer = json.dumps({"version": 2, "signatures": [{"future": True}]}).encode()
        self.write_profile(newer)
        bak = self.store.profile_path.with_name("profile.json.bak")
        (bak / "keep").mkdir(parents=True)                          # the .bak slot is unusable
        self.assertEqual(self.store.load(), Profile())
        self.assertEqual(self.store.profile_path.read_bytes(), newer)    # still in place
        with self.assertRaises(OSError):
            self.store.save(Profile(settings=Settings(language="fr")))
        self.assertEqual(self.store.profile_path.read_bytes(), newer)

    def test_utf8_bom_is_not_corruption(self):
        self.store.save(Profile(settings=Settings(language="nl"), signatures=[
            Signature(id="0123456789ab", label="Jane", stamp=Stamp(kind="text", text="Jane"))]))
        path = self.store.profile_path
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())       # e.g. PowerShell 5
        profile = self.store.load()
        self.assertEqual(profile.settings.language, "nl")
        self.assertEqual([sig.label for sig in profile.signatures], ["Jane"])
        self.assertEqual([p.name for p in self.store.config_dir.iterdir()], ["profile.json"])
        self.store.save(profile)                                    # written back without it
        self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_binary_garbage_is_corrupt(self):
        garbage = b"\xff\xfe\x00garbage"
        self.write_profile(garbage)
        self.assertEqual(self.store.load(), Profile())
        self.assert_set_aside(garbage)

    def test_deeply_nested_json_is_corrupt(self):
        deep = "[" * 200000
        self.write_profile(deep)
        self.assertEqual(self.store.load(), Profile())              # no RecursionError
        self.assert_set_aside(deep.encode())

    def test_bool_version_is_corrupt(self):
        path = self.write_profile('{"version": true, "settings": {"language": "fr"}}')
        original = path.read_bytes()
        self.assertEqual(self.store.load(), Profile())
        self.assert_set_aside(original)

    def test_bad_id_types_regenerated(self):
        bad_ids = [5, "vignette", "XYZ", "0123456789AB", None, ["a"], ""]
        sigs = self.load_signatures(
            [self.text_entry(id=bad) for bad in bad_ids] + [self.text_entry(id="0a1b2c3d4e5f")])
        self.assertEqual(len(sigs), len(bad_ids) + 1)
        for sig, bad in zip(sigs, bad_ids):
            with self.subTest(bad=bad):
                self.assertNotEqual(sig.id, bad)
                self.assertTrue(profile_store.is_valid_id(sig.id))
        self.assertEqual(sigs[-1].id, "0a1b2c3d4e5f")
        self.assertEqual(len({s.id for s in sigs}), len(sigs))

    def test_bad_label_replaced_and_long_label_truncated(self):
        stored = self.store.store_image(Image.new("RGBA", (4, 4)))
        sigs = self.load_signatures([
            self.text_entry(label=5),
            self.text_entry(label="   "),
            self.text_entry(),
            {"kind": "text", "text": '"quoted"', "label": None},
            {"kind": "image", "image_path": stored.name, "label": ["x"]},
            self.text_entry(label="L" * 300),
            {"kind": "text", "text": "T" * 200},
            self.text_entry(label="  Kept as typed "),
        ])
        self.assertEqual([s.label for s in sigs[:5]],
                         ["Jane Doe", "Jane Doe", "Jane Doe", '"quoted"', stored.name])
        self.assertEqual(sigs[5].label, "L" * 80)
        self.assertEqual(sigs[6].label, "T" * 30 + "…")
        self.assertEqual(sigs[7].label, "  Kept as typed ")

    def test_non_finite_placement_cleared(self):
        self.write_profile(
            '{"version": 1, "signatures": ['
            '{"kind": "text", "text": "a", "page": 1, "x": NaN, "y": 2, "placed_on": [595, 842]},'
            '{"kind": "text", "text": "b", "page": 1, "x": 1, "y": Infinity},'
            '{"kind": "text", "text": "c", "all_pages": true, "x": -Infinity, "y": 2},'
            '{"kind": "text", "text": "d", "page": 1, "x": 1e999, "y": 2}]}')
        sigs = self.store.load().signatures
        self.assertEqual([s.stamp.text for s in sigs], ["a", "b", "c", "d"])   # all kept
        for sig in sigs:
            self.assertEqual(sig.stamp, Stamp(kind="text", text=sig.stamp.text))
            self.assertIsNone(sig.placed_on)

    def test_huge_integers_never_raise(self):
        huge = "1" + "0" * 400                                       # too large for a float
        self.write_profile(
            '{"version": 1, "settings": {"vignette": {"page": 1, "x": %s, "y": 2, '
            '"placed_on": [595, 842]}}, "signatures": ['
            '{"kind": "text", "text": "a", "page": 1, "x": %s, "y": 2},'
            '{"kind": "text", "text": "b", "page": %s, "x": 1, "y": 2, "placed_on": [%s, 842]},'
            '{"kind": "text", "text": "c", "page": 1, "x": 1, "y": 2, "placed_on": [595, 842]}]}'
            % (huge, huge, huge, huge))
        profile = self.store.load()
        self.assertIsNone(profile.settings.vignette)
        # a: not a number -> skipped; b: kept, its placed_on dropped; c: untouched
        self.assertEqual([s.stamp.text for s in profile.signatures], ["b", "c"])
        self.assertIsNone(profile.signatures[0].placed_on)
        self.assertEqual(profile.signatures[1].placed_on, A4)
        self.assertFalse(profile_store.stamp_placement_applies(
            profile.signatures[0].stamp, A4, [A4]))
        self.store.save(profile)                                     # still serialisable

    def test_hostile_paths_never_raise(self):
        # an unknown ~user (RuntimeError in pathlib) and an over-long name
        # (OSError): both are just files that are not there
        # a NUL or a lone surrogate (ValueError in Path.resolve) must not make
        # the NEXT save raise either
        sigs = self.load_signatures([
            {"kind": "image", "image_path": "~no_such_user_zz/sig.png"},
            {"kind": "image", "image_path": "x" * 5000 + ".png"},
            {"kind": "image", "image_path": "a\x00b.png"},
            {"kind": "image", "image_path": "\ud800.png"},
            {"kind": "text", "text": "a", "font": "~no_such_user_zz/f.ttf"},
            {"kind": "text", "text": "b", "font": "x" * 5000},
            {"kind": "text", "text": "c", "font": "\ud800\x00.ttf"},
            self.text_entry(label="fine"),
        ])
        self.assertEqual(len(sigs), 8)                               # kept, unusable
        profile = Profile(signatures=list(sigs))
        self.store.save(profile)
        self.assertEqual(self.store.load(), profile)
        for sig in sigs[:4]:
            self.assertFalse(self.store.is_store_file(sig.stamp.image_path))
            self.store.remove_signature(profile, sig.id)             # deletable, harmlessly
        self.assertEqual(len(profile.signatures), 4)

    def test_damaged_font_entry_kept(self):
        # a font file FreeType opens but fails on (OSError while measuring):
        # load must not raise; the entry is kept like any unusable font
        font = damaged_font(self.tmp / "damaged.ttf")
        sigs = self.load_signatures([
            self.text_entry(label="before"),
            self.text_entry(label="damaged", font=str(font), page=1, x=1, y=2,
                            placed_on=[595, 842]),
            self.text_entry(label="after"),
        ])
        self.assertEqual([s.label for s in sigs], ["before", "damaged", "after"])
        self.assertEqual(sigs[1].stamp.font, str(font))
        self.assertEqual(sigs[1].placed_on, A4)                      # placement not thrown away
        with self.assertRaises(stamplib.StampError) as cm:
            stamplib.validate_stamp(sigs[1].stamp, placed=False)
        self.assertTrue(str(cm.exception).startswith("cannot load font "))

    def test_lone_surrogate_roundtrips(self):
        # e.g. an output folder whose name is not valid UTF-8 (surrogateescape),
        # or a "\ud800" escape typed into the file: save must not raise a
        # ValueError, and the file must stay loadable UTF-8.
        self.write_profile('{"version": 1, "settings": {"output_dir": "/data/caf\\udce9"}, '
                           '"signatures": [{"kind": "text", "text": "J\\ud800D \\\\ é", '
                           '"label": "L\\udfff", "id": "0123456789ab"}]}')
        profile = self.store.load()
        self.assertEqual(profile.settings.output_dir, "/data/caf\udce9")
        self.assertEqual(profile.signatures[0].stamp.text, "J\ud800D \\ é")
        self.store.save(profile)
        raw = self.store.profile_path.read_bytes()
        raw.decode("utf-8")                                          # valid UTF-8
        self.assertIn("é".encode("utf-8"), raw)                      # the rest is not escaped
        self.assertEqual(self.store.load(), profile)
        self.assertFalse(self.store.profile_path.with_name("profile.json.bak").exists())

    def test_bad_placed_on_dropped_placement_kept(self):
        bad_values = ["x", [595], [595, "842"], [595, 842, 1], [True, 842], None, {"w": 1}]
        entries = [self.text_entry(page=2, x=10, y=20, placed_on=bad) for bad in bad_values]
        self.write_profile(json.dumps({"version": 1, "signatures": entries})[:-2]
                           + ', {"kind": "text", "text": "Jane Doe", "page": 2, "x": 10, '
                             '"y": 20, "placed_on": [NaN, 842]}]}')
        sigs = self.store.load().signatures
        self.assertEqual(len(sigs), len(bad_values) + 1)
        for sig in sigs:
            self.assertEqual(sig.stamp, Stamp(kind="text", text="Jane Doe", page=2, x=10.0, y=20.0))
            self.assertIsNone(sig.placed_on)                         # simply not in force
        good = self.load_signatures([self.text_entry(page=2, x=10, y=20, placed_on=[595, 842])])
        self.assertEqual(good[0].placed_on, A4)

    def test_all_pages_without_position_roundtrips(self):
        profile = Profile(signatures=[Signature(
            id="0123456789ab", label="Initials", stamp=Stamp(kind="text", text="JD",
                                                              all_pages=True))])
        self.store.save(profile)
        loaded = self.store.load()
        self.assertEqual(loaded, profile)
        stamp = loaded.signatures[0].stamp
        self.assertTrue(stamp.all_pages)
        self.assertIsNone(stamp.x)
        self.assertIsNone(stamp.y)

    def test_oversized_text_entry_skipped(self):
        sigs = self.load_signatures([
            self.text_entry(label="before"),
            {"kind": "text", "text": "W" * 2000, "font": "lato", "font_size": 400},
            self.text_entry(label="after"),
        ])
        self.assertEqual([s.label for s in sigs], ["before", "after"])

    def test_non_finite_vignette_dropped(self):
        good = '{"page": 2, "x": 380, "y": 60, "placed_on": [595, 842]}'
        self.write_profile('{"version": 1, "settings": {"vignette": %s}}' % good)
        self.assertEqual(self.store.load().settings.vignette,
                         Placement(page=2, x=380.0, y=60.0, placed_on=A4))
        for bad in ('{"page": 2, "x": NaN, "y": 60, "placed_on": [595, 842]}',
                    '{"page": 2, "x": 380, "y": Infinity, "placed_on": [595, 842]}',
                    '{"page": 2, "x": 380, "y": 60, "placed_on": [NaN, 842]}',
                    '{"page": 2, "x": 380, "y": 60, "placed_on": [595]}',
                    '{"page": 2, "x": 380, "y": 60}',
                    '{"page": 0, "x": 380, "y": 60, "placed_on": [595, 842]}',
                    '{"page": true, "x": 380, "y": 60, "placed_on": [595, 842]}',
                    '{"page": 2.0, "x": 380, "y": 60, "placed_on": [595, 842]}',
                    '{"page": 2, "x": true, "y": 60, "placed_on": [595, 842]}',
                    '{"page": 2, "x": "380", "y": 60, "placed_on": [595, 842]}',
                    '{"x": 380, "y": 60, "placed_on": [595, 842]}',
                    '[2, 380, 60]', '"bottom-right"'):
            with self.subTest(vignette=bad):
                self.write_profile(
                    '{"version": 1, "settings": {"language": "fr", "vignette": %s}}' % bad)
                settings = self.store.load().settings
                self.assertIsNone(settings.vignette)
                self.assertEqual(settings.language, "fr")            # the rest is kept

    def test_incomplete_vignette_cannot_be_built(self):
        # save() writes a vignette as it is: a partial one must not exist at all
        with self.assertRaises(TypeError):
            Placement(page=1, x=5.0)


class ImageStore(StoreCase):
    def image_sig(self, path, sig_id="0123456789ab") -> Signature:
        return Signature(id=sig_id, label="img", stamp=Stamp(kind="image", image_path=path))

    def stored(self, color=(0, 0, 255, 255)) -> Path:
        return self.store.store_image(Image.new("RGBA", (8, 8), color))

    def test_import_is_content_addressed(self):
        src = make_png(self.tmp / "My Signature.PNG")
        stored = self.store.import_image(src)
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        self.assertEqual(stored, self.store.signatures_dir / f"{digest[:32]}.png")
        self.assertEqual(stored.read_bytes(), src.read_bytes())
        self.assertTrue(self.store.is_store_file(stored))
        # the same bytes under another name: one file, left untouched
        mtime = stored.stat().st_mtime_ns
        copy = self.tmp / "copy.png"
        copy.write_bytes(src.read_bytes())
        self.assertEqual(self.store.import_image(copy), stored)
        self.assertEqual(stored.stat().st_mtime_ns, mtime)
        self.assertEqual(len(list(self.store.signatures_dir.iterdir())), 1)
        # other content -> another file; an exotic extension becomes .img
        other = self.tmp / "scan.jfif"
        Image.new("RGB", (5, 5), (9, 9, 9)).save(other, format="JPEG")
        second = self.store.import_image(other)
        self.assertEqual(second.suffix, ".img")
        self.assertTrue(self.store.is_store_file(second))
        self.assertEqual(stamplib.load_image(second).size, (5, 5))
        self.assertEqual(len(list(self.store.signatures_dir.iterdir())), 2)

    def test_import_survives_moved_original(self):
        src = make_png(self.tmp / "sig.png", size=(30, 10))
        stored = self.store.import_image(src)
        src.unlink()
        self.assertEqual(stamplib.load_image(stored).size, (30, 10))
        stamplib.validate_stamp(Stamp(kind="image", image_path=stored), placed=False)

    def test_import_rejects_non_image(self):
        fake = self.tmp / "fake.png"
        fake.write_text("not an image", encoding="utf-8")
        for source in (fake, self.tmp / "missing.png"):
            with self.subTest(source=source.name):
                with self.assertRaises(stamplib.StampError):
                    self.store.import_image(source)
        self.assertFalse(self.store.signatures_dir.exists())        # not even created
        self.assertFalse(self.store.data_dir.exists())

    def test_import_write_failure_is_oserror(self):
        src = make_png(self.tmp / "sig.png")
        self.store.data_dir.write_text("a file where the data dir should be", encoding="utf-8")
        with self.assertRaises(OSError) as cm:
            self.store.import_image(src)
        self.assertNotIsInstance(cm.exception, stamplib.StampError)

    def test_store_image_writes_png_with_alpha(self):
        drawn = stamplib.render_strokes_image([[(5, 5), (40, 20)]], "#102030")
        stored = self.store.store_image(drawn)
        self.assertRegex(stored.name, r"^[0-9a-f]{32}\.png$")
        self.assertEqual(stored.parent, self.store.signatures_dir)
        self.assertEqual(stored.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        back = stamplib.load_image(stored)
        self.assertEqual((back.mode, back.size), ("RGBA", drawn.size))
        self.assertEqual(back.tobytes(), drawn.tobytes())           # alpha included
        self.assertEqual(self.store.store_image(drawn), stored)     # same pixels, same file
        self.assertEqual(len(list(self.store.signatures_dir.iterdir())), 1)

    def test_remove_signature_deletes_unreferenced_file(self):
        stored = self.stored()
        profile = Profile(signatures=[
            self.image_sig(stored),
            Signature(id="aaaaaaaaaaaa", label="t", stamp=Stamp(kind="text", text="x"))])
        self.store.remove_signature(profile, "0123456789ab")
        self.assertEqual([s.id for s in profile.signatures], ["aaaaaaaaaaaa"])
        self.assertFalse(stored.exists())
        self.assertFalse(self.store.profile_path.exists())          # does NOT save
        # removing a text signature touches no file; a missing file is ignored
        self.store.remove_signature(profile, "aaaaaaaaaaaa")
        self.assertEqual(profile.signatures, [])
        gone = Profile(signatures=[self.image_sig(stored)])
        self.store.remove_signature(gone, "0123456789ab")
        self.assertEqual(gone.signatures, [])

    def test_remove_keeps_file_shared_by_another_signature(self):
        stored = self.stored()
        profile = Profile(signatures=[self.image_sig(stored, "0123456789ab"),
                                      self.image_sig(stored, "aaaaaaaaaaaa")])
        self.store.remove_signature(profile, "0123456789ab")
        self.assertTrue(stored.exists())
        self.store.remove_signature(profile, "aaaaaaaaaaaa")        # the last reference
        self.assertFalse(stored.exists())

    def test_remove_never_deletes_outside_store(self):
        outside = make_png(self.tmp / "original.png")
        lookalike = make_png(self.tmp / ("a" * 32 + ".png"))        # right name, wrong place
        profile = Profile(signatures=[self.image_sig(outside, "0123456789ab"),
                                      self.image_sig(lookalike, "aaaaaaaaaaaa")])
        self.store.remove_signature(profile, "0123456789ab")
        self.store.remove_signature(profile, "aaaaaaaaaaaa")
        self.assertEqual(profile.signatures, [])
        self.assertTrue(outside.exists())
        self.assertTrue(lookalike.exists())

    def test_remove_never_follows_traversal(self):
        self.stored()                                               # signatures/ exists
        plain = make_png(self.store.data_dir / "outside.png")
        lookalike = make_png(self.store.data_dir / ("b" * 32 + ".png"))
        sigs = self.load_signatures([
            {"kind": "image", "image_path": "../outside.png", "id": "0123456789ab"},
            {"kind": "image", "image_path": f"../{lookalike.name}", "id": "aaaaaaaaaaaa"},
        ])
        self.assertEqual(len(sigs), 2)                              # the files exist: usable
        self.assertEqual(sigs[0].stamp.image_path,
                         self.store.signatures_dir / ".." / "outside.png")
        profile = Profile(signatures=list(sigs))
        for sig_id in ("0123456789ab", "aaaaaaaaaaaa"):
            self.assertFalse(self.store.is_store_file(profile.signatures[0].stamp.image_path))
            self.store.remove_signature(profile, sig_id)
        self.assertEqual(profile.signatures, [])
        self.assertTrue(plain.exists())
        self.assertTrue(lookalike.exists())
        # and saving such an entry never writes it as a store-relative name
        self.store.save(Profile(signatures=sigs))
        saved = json.loads(self.store.profile_path.read_text(encoding="utf-8"))
        self.assertEqual([e["image_path"] for e in saved["signatures"]],
                         [str(plain), str(lookalike)])

    @unittest.skipUnless(os.name == "posix", "symlinks")
    def test_remove_ignores_symlink(self):
        self.stored()
        secret = make_png(self.tmp / "secret.png")
        link = self.store.signatures_dir / ("c" * 32 + ".png")      # a correctly named link
        link.symlink_to(secret)
        self.assertFalse(self.store.is_store_file(link))
        profile = Profile(signatures=[self.image_sig(link)])
        self.store.remove_signature(profile, "0123456789ab")
        self.assertEqual(profile.signatures, [])
        self.assertTrue(secret.exists())
        self.assertTrue(link.is_symlink())                          # nothing was unlinked
        # a link to a REAL store file is not a store file either: deleting the
        # signature that points at the link must not delete the link's target
        stored = self.stored()
        alias = self.store.signatures_dir / ("d" * 32 + ".png")
        alias.symlink_to(stored)
        profile = Profile(signatures=[self.image_sig(alias)])
        self.store.remove_signature(profile, "0123456789ab")
        self.assertTrue(stored.exists())
        self.assertTrue(alias.is_symlink())

    def test_remove_ignores_foreign_name_in_store(self):
        self.stored()
        mine = make_png(self.store.signatures_dir / "mine.png")
        profile = Profile(signatures=[self.image_sig(mine)])
        self.store.remove_signature(profile, "0123456789ab")
        self.assertEqual(profile.signatures, [])
        self.assertTrue(mine.exists())

    def test_is_store_file_rules(self):
        stored = self.stored()
        sig_dir = self.store.signatures_dir
        name = "d" * 32 + ".png"
        (sig_dir / "sub").mkdir()
        rows = [
            (stored, True),
            (str(stored), True),
            (sig_dir / "sub" / ".." / stored.name, True),           # resolves INTO the store
            (sig_dir / name, True),                                 # the name decides
            (sig_dir / ".." / name, False),                         # traversal out of the store
            (sig_dir / ".." / ".." / name, False),
            (make_png(self.tmp / name), False),                     # absolute path elsewhere
            (make_png(sig_dir / "mine.png"), False),                # foreign name
            (make_png(sig_dir / ("D" * 32 + ".png")), False),       # upper-case hex
            (make_png(sig_dir / ("d" * 31 + ".png")), False),       # too short
            (make_png(sig_dir / ("d" * 32 + ".PNG")), False),       # upper-case extension
            (make_png(sig_dir / "sub" / name), False),              # sub-directory of the store
            (sig_dir, False),
            (Path(name), False),                                    # relative to the cwd
        ]
        if os.name == "posix":
            link = sig_dir / ("e" * 32 + ".png")
            link.symlink_to(stored)                                 # even to a real store file
            rows.append((link, False))
        for path, expected in rows:
            with self.subTest(path=str(path)):
                self.assertIs(self.store.is_store_file(path), expected)

    def test_shared_file_compared_by_resolved_path(self):
        stored = self.stored()
        alias = self.store.signatures_dir / ".." / "signatures" / stored.name
        self.assertNotEqual(alias, stored)                          # different spelling…
        self.assertEqual(alias.resolve(), stored.resolve())         # …same file
        profile = Profile(signatures=[self.image_sig(stored, "0123456789ab"),
                                      self.image_sig(alias, "aaaaaaaaaaaa")])
        self.store.remove_signature(profile, "0123456789ab")
        self.assertTrue(stored.exists())                            # still referenced
        self.store.remove_signature(profile, "aaaaaaaaaaaa")
        self.assertFalse(stored.exists())

    def test_remove_unknown_id_is_noop(self):
        stored = self.stored()
        profile = Profile(signatures=[self.image_sig(stored)])
        for sig_id in ("ffffffffffff", "vignette", None):
            self.store.remove_signature(profile, sig_id)
        self.assertEqual(len(profile.signatures), 1)
        self.assertTrue(stored.exists())

    def test_no_sweep_of_unreferenced_files(self):
        orphan = self.stored((1, 2, 3, 255))
        used = self.stored((4, 5, 6, 255))
        profile = Profile(signatures=[self.image_sig(used)])
        self.store.save(profile)
        self.store.load()
        self.store.remove_signature(profile, "0123456789ab")
        self.assertTrue(orphan.exists())                            # never listed, never swept
        self.assertFalse(used.exists())


class PlacementRule(unittest.TestCase):
    def placed(self, **kwargs) -> Stamp:
        return Stamp(kind="text", text="x", x=10.0, y=20.0, **kwargs)

    def test_numbered_page_same_size_applies(self):
        dims = [A4, LETTER]
        self.assertTrue(profile_store.stamp_placement_applies(self.placed(page=1), A4, dims))
        self.assertTrue(profile_store.stamp_placement_applies(self.placed(page=2), LETTER, dims))
        # lists (as read from JSON) compare like tuples
        self.assertTrue(profile_store.stamp_placement_applies(
            self.placed(page=1), [595.0, 842.0], [[595.0, 842.0]]))

    def test_different_size_does_not_apply(self):
        dims = [A4, LETTER]
        self.assertFalse(profile_store.stamp_placement_applies(self.placed(page=2), A4, dims))
        # EXACT equality, no tolerance
        self.assertFalse(profile_store.stamp_placement_applies(
            self.placed(page=1), (595.0, 842.001), dims))

    def test_page_beyond_template_does_not_apply(self):
        self.assertFalse(profile_store.stamp_placement_applies(self.placed(page=3), A4, [A4, A4]))
        self.assertFalse(profile_store.stamp_placement_applies(self.placed(page=0), A4, [A4, A4]))
        self.assertFalse(profile_store.stamp_placement_applies(self.placed(page=1), A4, []))
        self.assertFalse(profile_store.stamp_placement_applies(self.placed(page=1), A4, None))

    def test_unplaced_does_not_apply(self):
        dims = [A4]
        unplaced = Stamp(kind="text", text="x")
        self.assertFalse(profile_store.stamp_placement_applies(unplaced, A4, dims))
        self.assertFalse(profile_store.stamp_placement_applies(
            Stamp(kind="text", text="x", page=1, x=1.0), A4, dims))        # no y
        self.assertFalse(profile_store.stamp_placement_applies(
            Stamp(kind="text", text="x", x=1.0, y=1.0), A4, dims))         # no target
        self.assertFalse(profile_store.stamp_placement_applies(self.placed(page=1), None, dims))
        self.assertFalse(profile_store.stamp_placement_applies(
            Stamp(kind="text", text="x", all_pages=True), A4, dims))       # remembers the flag only

    def test_all_pages_applies_if_any_page_matches(self):
        stamp = self.placed(all_pages=True)
        self.assertTrue(profile_store.stamp_placement_applies(stamp, LETTER, [A4, LETTER, A4]))
        self.assertFalse(profile_store.stamp_placement_applies(stamp, LETTER, [A4, A4]))

    def test_anchor_requires_anchor_page(self):
        dims = [A4, A4]
        stamp = self.placed(page=1)
        self.assertFalse(profile_store.stamp_placement_applies(stamp, A4, dims, anchor="last"))
        self.assertTrue(profile_store.stamp_placement_applies(stamp, A4, dims, anchor="first"))
        self.assertTrue(profile_store.stamp_placement_applies(
            self.placed(page=2), A4, dims, anchor="last"))
        # a one-page template: page 1 is both the first and the last page
        self.assertTrue(profile_store.stamp_placement_applies(stamp, A4, [A4], anchor="last"))

    def test_all_pages_under_anchor_needs_anchor_page_size(self):
        stamp = self.placed(all_pages=True)
        dims = [A4, LETTER]
        self.assertTrue(profile_store.stamp_placement_applies(stamp, A4, dims, anchor="first"))
        self.assertFalse(profile_store.stamp_placement_applies(stamp, A4, dims, anchor="last"))
        self.assertTrue(profile_store.stamp_placement_applies(stamp, LETTER, dims, anchor="last"))

    def test_stamp_with_page_anchor_maps_to_template_page(self):
        dims = [A4, LETTER, LETTER]
        last, first = self.placed(page_anchor="last"), self.placed(page_anchor="first")
        self.assertTrue(profile_store.stamp_placement_applies(last, LETTER, dims))
        self.assertFalse(profile_store.stamp_placement_applies(last, A4, dims))
        self.assertTrue(profile_store.stamp_placement_applies(first, A4, dims))
        self.assertTrue(profile_store.stamp_placement_applies(last, LETTER, dims, anchor="last"))
        self.assertFalse(profile_store.stamp_placement_applies(last, LETTER, dims, anchor="first"))

    def test_vignette_rule(self):
        dims = [A4, LETTER]
        applies = profile_store.page_placement_applies
        self.assertTrue(applies(1, A4, dims))
        self.assertTrue(applies(2, LETTER, dims))
        self.assertFalse(applies(2, A4, dims))
        self.assertFalse(applies(3, A4, dims))
        self.assertFalse(applies(None, A4, dims))
        self.assertFalse(applies(1, None, dims))
        self.assertFalse(applies(1, A4, []))
        self.assertFalse(applies(1, A4, dims, anchor="last"))
        self.assertTrue(applies(2, LETTER, dims, anchor="last"))
        self.assertTrue(applies(1, A4, dims, anchor="first"))


if __name__ == "__main__":
    unittest.main()
