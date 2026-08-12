# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Localisation with a fallback chain that cannot fail.

Every customer-facing string comes from a locale file. A missing or broken
locale must never blank the interface or raise, so lookups fall back:

    requested language -> German -> the key itself

The key is a deliberate last resort: an untranslated "printer.ip_mismatch" on
screen is ugly, but it is still something the customer can read out to support,
which an empty box is not.
"""

from __future__ import annotations

import json
import os
import threading

LANGUAGES = ("de", "en", "ru")
FALLBACK_LANGUAGE = "de"

_lock = threading.Lock()
_cache: dict = {}
_locale_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "locales")


def set_locale_dir(path: str) -> None:
    global _locale_dir
    with _lock:
        _locale_dir = path
        _cache.clear()


def normalise_language(language) -> str:
    if isinstance(language, str):
        candidate = language.strip().lower()[:2]
        if candidate in LANGUAGES:
            return candidate
    return FALLBACK_LANGUAGE


def load(language: str) -> dict:
    """Load one locale. A broken file yields an empty dict, never an exception."""
    language = normalise_language(language)
    with _lock:
        if language in _cache:
            return _cache[language]
    path = os.path.join(_locale_dir, f"{language}.json")
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = {k: v for k, v in loaded.items() if isinstance(v, str)}
    except (OSError, json.JSONDecodeError, ValueError):
        data = {}
    with _lock:
        _cache[language] = data
    return data


def translate(language: str, key: str, **params) -> str:
    """Resolve a key, then substitute placeholders. Never raises."""
    if not isinstance(key, str) or not key:
        return ""
    template = load(language).get(key)
    if template is None and normalise_language(language) != FALLBACK_LANGUAGE:
        template = load(FALLBACK_LANGUAGE).get(key)
    if template is None:
        return key
    if not params:
        return template
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        # A placeholder mismatch must not cost the customer the whole message.
        return template
