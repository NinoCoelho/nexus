"""Tests for the backend i18n helper."""

from nexus import i18n


def test_t_resolves_english_by_default():
    i18n.reset_cache()
    assert i18n.t("errors.providers.api_key_required") == "api_key required"


def test_t_resolves_portuguese():
    i18n.reset_cache()
    assert i18n.t("errors.providers.api_key_required", "pt-BR") == "api_key obrigatório"


def test_t_interpolates_kwargs():
    i18n.reset_cache()
    msg = i18n.t("errors.providers.not_found", "pt-BR", name="openai")
    assert "openai" in msg
    assert "encontrado" in msg


def test_t_unknown_key_returns_key():
    i18n.reset_cache()
    assert i18n.t("errors.does_not_exist") == "errors.does_not_exist"


def test_t_falls_back_to_english_when_locale_missing():
    i18n.reset_cache()
    # Catalog has the key in en; pt-BR (here we use a bogus lang) falls back.
    assert i18n.t("errors.credentials.bad_name", "fr") == "name must match ^[A-Z][A-Z0-9_]*$"


def test_normalize_handles_variants():
    assert i18n.normalize("pt-BR") == "pt-BR"
    assert i18n.normalize("pt") == "pt-BR"
    assert i18n.normalize("pt-PT") == "pt-BR"
    assert i18n.normalize("en") == "en"
    assert i18n.normalize("en-US") == "en"
    assert i18n.normalize(None) == "en"
    assert i18n.normalize("") == "en"
    assert i18n.normalize("fr") == "en"
