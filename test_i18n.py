#!/usr/bin/env python3
"""Headless tests for the i18n catalog (no tkinter, no network).

Guards the invariants the GUI relies on: every key exists in every supported
language; each translation uses exactly the same ``{...}`` placeholders as
the English reference (a mismatch would raise KeyError at ``tr(...)`` time,
i.e. in the middle of the wizard); the light ``**bold**`` markup is balanced
with the same emphasis count per language (``split_markup`` renders it); and
the long-form documentation (``i18n_docs.py``) is merged into the catalog,
its sections/sources resolve, and it is genuinely translated rather than
falling back to English."""

import re
import unittest

import i18n
from i18n import CATALOG, LANGUAGE_NAMES, LANGUAGES, detect_language, split_markup, tr

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


class Markup(unittest.TestCase):
    """Light ``**bold**`` markup: balanced everywhere, same emphasis count
    per language, and the pure splitter the GUI renders it with."""

    def test_bold_markers_balanced_everywhere(self):
        for key, entry in CATALOG.items():
            for lang, text in entry.items():
                self.assertEqual(text.count("**") % 2, 0, f"{key}/{lang}")

    def test_bold_pairs_match_english(self):
        for key, entry in CATALOG.items():
            ref = entry["en"].count("**")
            for lang in LANGUAGES:
                self.assertEqual(entry[lang].count("**"), ref, f"{key}/{lang}")

    def test_split_markup(self):
        self.assertEqual(split_markup("a **b** c"),
                         [("a ", False), ("b", True), (" c", False)])
        self.assertEqual(split_markup("**x**"), [("x", True)])
        self.assertEqual(split_markup("plain"), [("plain", False)])
        self.assertEqual(split_markup("odd ** marker"), [("odd ** marker", False)])
        self.assertEqual(split_markup(""), [])


class Documentation(unittest.TestCase):
    """The "Full documentation" popup: sections and sources resolve to
    catalog keys, the docs module is merged, and the docs are really
    translated (no English fallback hiding behind ``tr``)."""

    def test_sections_and_sources_exist_in_catalog(self):
        for key in i18n.DOC_SECTIONS + ("docs.sources_heading", "docs.sources_intro"):
            self.assertIn(key, CATALOG)
        for title_key, url in i18n.DOC_SOURCES:
            self.assertIn(title_key, CATALOG)
            self.assertTrue(url.startswith("https://"), url)
        urls = [u for _, u in i18n.DOC_SOURCES]
        self.assertEqual(len(urls), len(set(urls)))

    def test_docs_module_merged(self):
        from i18n_docs import DOCS_CATALOG
        self.assertTrue(DOCS_CATALOG)
        for key, entry in DOCS_CATALOG.items():
            self.assertIs(CATALOG[key], entry)

    def test_docs_translated_in_every_language(self):
        for key in (i18n.DOC_SECTIONS + tuple(k for k, _ in i18n.DOC_SOURCES)
                    + ("docs.sources_heading", "docs.sources_intro")):
            for lang in LANGUAGES:
                if lang != "en":
                    self.assertNotEqual(CATALOG[key][lang], CATALOG[key]["en"],
                                        f"{key}/{lang} is still English")


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
