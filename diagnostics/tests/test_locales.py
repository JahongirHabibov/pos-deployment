# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Localisation.

Key parity is not cosmetic here: a key missing in one language silently turns
into the raw key on a customer's screen. The placeholder check matters just as
much — a translation that renames {ip} to {adresse} loses the address in that
language only, which is exactly the kind of defect that ships unnoticed.
"""

import json
import os
import re

import pytest

from kassio_diagnostics import i18n

LOCALE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "locales")
PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def load(language):
    with open(os.path.join(LOCALE_DIR, f"{language}.json"), encoding="utf-8") as handle:
        return json.load(handle)


def test_all_languages_exist():
    for language in i18n.LANGUAGES:
        assert os.path.isfile(os.path.join(LOCALE_DIR, f"{language}.json"))


def test_key_sets_are_identical():
    reference = set(load("de"))
    for language in i18n.LANGUAGES:
        assert set(load(language)) == reference, f"{language} differs from de"


def test_no_empty_translations():
    for language in i18n.LANGUAGES:
        for key, value in load(language).items():
            assert isinstance(value, str) and value.strip(), f"{language}:{key}"


def test_placeholders_match_across_languages():
    german = load("de")
    for language in i18n.LANGUAGES:
        strings = load(language)
        for key, template in german.items():
            assert set(PLACEHOLDER.findall(template)) == \
                   set(PLACEHOLDER.findall(strings[key])), f"{language}:{key}"


def test_fallback_chain_reaches_german(tmp_path):
    (tmp_path / "de.json").write_text(json.dumps({"a.b": "deutsch"}), encoding="utf-8")
    (tmp_path / "en.json").write_text(json.dumps({}), encoding="utf-8")
    i18n.set_locale_dir(str(tmp_path))
    try:
        assert i18n.translate("en", "a.b") == "deutsch"
    finally:
        i18n.set_locale_dir(LOCALE_DIR)


def test_unknown_key_falls_back_to_the_key_itself():
    assert i18n.translate("de", "does.not.exist") == "does.not.exist"


def test_broken_locale_file_does_not_raise(tmp_path):
    (tmp_path / "de.json").write_text("{ this is not json", encoding="utf-8")
    i18n.set_locale_dir(str(tmp_path))
    try:
        assert i18n.load("de") == {}
        assert i18n.translate("de", "any.key") == "any.key"
    finally:
        i18n.set_locale_dir(LOCALE_DIR)


def test_placeholder_mismatch_does_not_lose_the_message(tmp_path):
    (tmp_path / "de.json").write_text(json.dumps({"a.b": "Wert {ip}"}), encoding="utf-8")
    i18n.set_locale_dir(str(tmp_path))
    try:
        # Wrong parameter name: the customer still gets the sentence.
        assert i18n.translate("de", "a.b", address="1.2.3.4") == "Wert {ip}"
    finally:
        i18n.set_locale_dir(LOCALE_DIR)


@pytest.mark.parametrize("value,expected", [
    ("de", "de"), ("EN", "en"), ("ru-RU", "ru"), ("fr", "de"), (None, "de"), (5, "de"),
])
def test_language_normalisation(value, expected):
    assert i18n.normalise_language(value) == expected
