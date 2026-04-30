"""Tiny dict-based i18n for backend HTTP error messages.

Catalogs live next to this module under ``locales/<lang>.toml`` and are
loaded lazily on first lookup. Keys are dotted (``errors.providers.not_found``).
Unknown keys / unknown locales fall back to English; if the key is also
missing in English we return the key itself so a missing-translation bug
surfaces visibly instead of silently producing an empty string.

The agent's system prompt and skill bodies are NOT translated through this
module — those stay English so the LLM keeps its grounding. Translated
output for the human user is produced by the LLM itself, instructed via
``prompt_builder`` based on the resolved UI language.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "pt-BR")
DEFAULT_LANGUAGE = "en"

_LOCALES_DIR = Path(__file__).parent / "locales"
_catalogs: dict[str, dict[str, Any]] = {}


def _load(lang: str) -> dict[str, Any]:
    cached = _catalogs.get(lang)
    if cached is not None:
        return cached
    path = _LOCALES_DIR / f"{lang}.toml"
    if not path.exists():
        _catalogs[lang] = {}
        return _catalogs[lang]
    with open(path, "rb") as f:
        _catalogs[lang] = tomllib.load(f)
    return _catalogs[lang]


def _resolve(catalog: dict[str, Any], key: str) -> str | None:
    node: Any = catalog
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def normalize(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANGUAGE
    if lang in SUPPORTED_LANGUAGES:
        return lang
    primary = lang.split("-", 1)[0].lower()
    for cand in SUPPORTED_LANGUAGES:
        if cand.split("-", 1)[0].lower() == primary:
            return cand
    return DEFAULT_LANGUAGE


def t(key: str, lang: str | None = None, /, **kwargs: Any) -> str:
    """Resolve ``key`` in the catalog for ``lang``.

    Falls back to English, then to the key string. ``kwargs`` are interpolated
    via ``str.format``; missing placeholders never raise (we return the
    template unrendered rather than crashing the request).
    """
    resolved = normalize(lang)
    s = _resolve(_load(resolved), key)
    if s is None and resolved != DEFAULT_LANGUAGE:
        s = _resolve(_load(DEFAULT_LANGUAGE), key)
    if s is None:
        return key
    if not kwargs:
        return s
    try:
        return s.format(**kwargs)
    except (KeyError, IndexError):
        return s


def reset_cache() -> None:
    """Drop the catalog cache. Used by tests."""
    _catalogs.clear()
