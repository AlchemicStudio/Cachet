#!/usr/bin/env python3
"""Headless tests for the i18n catalog (no tkinter, no network).

Guards the two invariants the GUI relies on: every key exists in every
supported language, and each translation uses exactly the same ``{...}``
placeholders as the English reference (a mismatch would raise KeyError at
``tr(...)`` time, i.e. in the middle of the wizard)."""

import re
import unittest

import i18n
from i18n import CATALOG, LANGUAGE_NAMES, LANGUAGES, detect_language, tr

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class CatalogCompleteness(unittest.TestCase):
    def test_every_key_has_every_language(self):
        for key, entry in CATALOG.items():
            self.assertEqual(
                set(entry), set(LANGUAGES),
                f"{key}: languages {sorted(entry)} != {sorted(LANGUAGES)}",
            )

    def test_no_empty_translation(self):
        for key, entry in CATALOG.items():
            for lang, text in entry.items():
                self.assertTrue(
                    isinstance(text, str) and text.strip(),
                    f"{key}/{lang} is empty",
                )

    def test_placeholders_match_english(self):
        for key, entry in CATALOG.items():
            ref = set(_PLACEHOLDER.findall(entry["en"]))
            for lang in LANGUAGES:
                got = set(_PLACEHOLDER.findall(entry[lang]))
                self.assertEqual(
                    got, ref,
                    f"{key}/{lang}: placeholders {sorted(got)} != {sorted(ref)}",
                )

    def test_language_names_cover_all_languages(self):
        self.assertEqual(set(LANGUAGE_NAMES), set(LANGUAGES))

    def test_no_stray_braces(self):
        # Texts only use braces for placeholders; a literal "{" would crash
        # str.format on keys formatted with kwargs.
        for key, entry in CATALOG.items():
            for lang, text in entry.items():
                stripped = _PLACEHOLDER.sub("", text)
                self.assertNotIn("{", stripped, f"{key}/{lang}")
                self.assertNotIn("}", stripped, f"{key}/{lang}")


class TrBehaviour(unittest.TestCase):
    def tearDown(self):
        i18n.set_language(i18n.DEFAULT_LANGUAGE)

    def test_translates_in_active_language(self):
        i18n.set_language("fr")
        self.assertEqual(tr("landing.start"), "Commencer")

    def test_formats_placeholders(self):
        i18n.set_language("de")
        self.assertEqual(tr("val.summary", ok=2, total=3), "2/3 gültige Datei(en).")

    def test_unknown_key_returns_key(self):
        self.assertEqual(tr("no.such.key"), "no.such.key")

    def test_set_language_rejects_unknown(self):
        with self.assertRaises(ValueError):
            i18n.set_language("it")

    def test_every_language_selectable(self):
        for lang in LANGUAGES:
            i18n.set_language(lang)
            self.assertEqual(i18n.get_language(), lang)
            self.assertTrue(tr("landing.heading"))


class DetectLanguage(unittest.TestCase):
    def test_locale_forms(self):
        cases = {
            "fr_BE.UTF-8": "fr",
            "nl": "nl",
            "de_DE@euro": "de",
            "es_ES.ISO8859-1": "es",
            "pt_PT.UTF-8": "pt",
            "en_US": "en",
            "C.UTF-8": "en",   # unsupported "c." prefix -> default
            "it_IT": "en",     # unsupported language -> default
            "": "en",
            None: "en",
        }
        for raw, expected in cases.items():
            self.assertEqual(detect_language(raw), expected, raw)

    def test_system_language_is_supported(self):
        self.assertIn(i18n.system_language(), LANGUAGES)


if __name__ == "__main__":
    unittest.main()
