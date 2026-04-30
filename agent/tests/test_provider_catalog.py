"""Provider catalog: schema validity + loader behavior.

The catalog ships as a bundled JSON file; these tests guarantee the
shape stays valid as new entries are added and that the
``load_catalog`` cache + lookup helpers behave.
"""

from __future__ import annotations

import pytest

from nexus.providers import find, load_catalog
from nexus.providers.catalog import (
    AuthMethod,
    ModelInfo,
    OAuthSpec,
    ProviderCatalogEntry,
)


@pytest.fixture(autouse=True)
def _clear_catalog_cache() -> None:
    """Re-read catalog.json fresh for every test in this module.

    Without this the lru_cache hides regressions in JSON edits across
    the test session.
    """
    load_catalog.cache_clear()


def test_catalog_loads_and_validates() -> None:
    entries = load_catalog()
    assert len(entries) >= 20  # plan calls for ~25–28 entries
    for e in entries:
        assert isinstance(e, ProviderCatalogEntry)


def test_no_duplicate_provider_ids() -> None:
    ids = [e.id for e in load_catalog()]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


def test_every_entry_has_at_least_one_auth_method() -> None:
    for e in load_catalog():
        assert e.auth_methods, f"{e.id} has no auth methods"


def test_anonymous_methods_have_no_secret_prompts() -> None:
    """Anonymous local providers (Ollama / LM Studio / llama.cpp) must
    not advertise a password prompt — that would confuse the wizard
    into asking for a key that isn't needed."""
    for e in load_catalog():
        for m in e.auth_methods:
            if m.id == "anonymous":
                for p in m.prompts:
                    assert not p.secret, (
                        f"{e.id}/{m.id}: anonymous method advertises secret prompt {p.name!r}"
                    )


def test_oauth_methods_carry_oauth_spec() -> None:
    for e in load_catalog():
        for m in e.auth_methods:
            if m.id in ("oauth_device", "oauth_redirect"):
                assert m.oauth is not None, (
                    f"{e.id}/{m.id}: oauth method missing OAuthSpec"
                )
                if m.id == "oauth_device":
                    assert m.oauth.flavor == "device"
                if m.id == "oauth_redirect":
                    assert m.oauth.flavor == "redirect"


def test_iam_aws_methods_declare_extra() -> None:
    """IAM AWS entries must declare ``requires_extra="bedrock"`` so the
    wizard can surface an install hint when boto3 isn't present."""
    for e in load_catalog():
        for m in e.auth_methods:
            if m.id == "iam_aws":
                assert m.requires_extra == "bedrock"


def test_known_providers_are_in_catalog() -> None:
    must_have = {"openai", "anthropic", "ollama", "openrouter", "groq", "bedrock"}
    have = {e.id for e in load_catalog()}
    missing = must_have - have
    assert not missing, f"catalog missing core providers: {missing}"


def test_find_returns_entry_or_none() -> None:
    assert find("openai") is not None
    assert find("definitely-not-a-provider-xyz") is None


def test_validators_reject_duplicate_ids(tmp_path, monkeypatch) -> None:
    """If the JSON ever lands a duplicate id, ``load_catalog`` must
    raise rather than silently let one shadow the other."""
    from nexus.providers import catalog as catalog_mod

    bad = (
        '[{"id":"x","display_name":"X","category":"other",'
        '"runtime_kind":"openai_compat","auth_methods":[{"id":"anonymous","label":"A"}]},'
        '{"id":"x","display_name":"X2","category":"other",'
        '"runtime_kind":"openai_compat","auth_methods":[{"id":"anonymous","label":"A"}]}]'
    )
    p = tmp_path / "catalog.json"
    p.write_text(bad)

    class _Resource:
        def joinpath(self, _name: str) -> "_Resource":
            return self

        def read_text(self, _enc: str) -> str:
            return bad

    def _files(_pkg: str) -> "_Resource":
        return _Resource()

    monkeypatch.setattr(catalog_mod, "files", _files)
    catalog_mod.load_catalog.cache_clear()
    with pytest.raises(ValueError, match="duplicate"):
        catalog_mod.load_catalog()


def test_pydantic_models_round_trip_through_dict() -> None:
    """Each entry survives a model_dump → model_validate cycle so the
    catalog HTTP route's ``model_dump`` payload deserializes cleanly."""
    for e in load_catalog():
        d = e.model_dump()
        round_tripped = ProviderCatalogEntry.model_validate(d)
        assert round_tripped == e


def test_oauth_spec_pkce_default_is_true() -> None:
    spec = OAuthSpec(flavor="device")
    assert spec.pkce is True


def test_auth_method_priority_default() -> None:
    m = AuthMethod(id="api", label="API key")
    assert m.priority == 100


def test_model_info_string_coercion_uses_default_capabilities() -> None:
    """Bare strings in catalog default_models still parse — they're
    promoted to ModelInfo with the conservative chat+tools capability
    set, keeping older catalog entries readable."""
    m = ModelInfo.model_validate("some-model-id")
    assert m.id == "some-model-id"
    assert "chat" in m.capabilities
    assert "tools" in m.capabilities


def test_model_info_object_form_preserves_capabilities() -> None:
    m = ModelInfo.model_validate({
        "id": "deepseek-reasoner",
        "capabilities": ["chat", "reasoning"],
    })
    assert m.capabilities == ["chat", "reasoning"]


def test_openai_catalog_distinguishes_reasoning_models() -> None:
    """The o-series should carry the ``reasoning`` capability so the
    UI can filter for thinking models without name-pattern guessing."""
    openai = find("openai")
    assert openai is not None
    by_id = {m.id: m for m in openai.default_models}
    assert "reasoning" in by_id["o1-mini"].capabilities
    assert "reasoning" in by_id["o3-mini"].capabilities
    assert "reasoning" not in by_id["gpt-4o-mini"].capabilities
