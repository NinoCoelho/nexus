"""Tests for provider/model resolution fallbacks and delete-model
reference cleanup.

Regression origin: the user's ``[[models]]`` entry for
``openai-compat-generic/nexus`` was deleted while ``agent.default_model``
still pointed at it — the adapter then sent the *qualified* id upstream
(``model=openai-compat-generic/nexus``) and the gateway 400'd. Two guards:
the adapter now strips a known ``provider/`` prefix, and DELETE /models
clears dangling references.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent._loom_bridge.adapter import LoomProviderAdapter
from nexus.agent.registry import ProviderRegistry


class _FakeProvider:
    name = "fake"

    async def aclose(self) -> None:
        return None


def _registry() -> ProviderRegistry:
    reg = ProviderRegistry()
    p = _FakeProvider()
    reg.register_provider("prov", p)
    reg.register_model("prov/known", "prov", "bare-known")
    return reg


def _adapter(reg: ProviderRegistry) -> LoomProviderAdapter:
    return LoomProviderAdapter(
        _FakeProvider(),  # type: ignore[arg-type]
        provider_registry=reg,
        default_model="prov/known",
    )


def test_resolve_registered_maps_to_bare_name() -> None:
    provider, upstream = _adapter(_registry())._resolve("prov/known")
    assert upstream == "bare-known"


def test_resolve_unmapped_catalog_id_strips_provider_prefix() -> None:
    """`prov/nexus` with no [[models]] entry must reach the provider as
    bare `nexus`, not as the qualified id."""
    provider, upstream = _adapter(_registry())._resolve("prov/nexus")
    assert upstream == "nexus"
    assert isinstance(provider, _FakeProvider)


def test_resolve_unknown_provider_keeps_old_fallback() -> None:
    provider, upstream = _adapter(_registry())._resolve("other/model")
    # Falls back to the default (nexus) provider with the same id, as before.
    assert upstream == "other/model"


def test_resolve_bare_id_falls_back_unchanged() -> None:
    provider, upstream = _adapter(_registry())._resolve("just-a-name")
    assert upstream == "just-a-name"


async def test_delete_model_clears_dangling_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs against a temp CONFIG_PATH — never the user's real config."""
    import nexus.config_file as cf
    from nexus.config_file import ModelEntry
    from nexus.server.routes import models as models_route

    monkeypatch.setattr(cf, "CONFIG_PATH", tmp_path / "config.toml")

    cfg = cf.default_config()
    victim = ModelEntry(
        id="prov/victim", provider="prov", model_name="victim", tags=[], tier="balanced", notes=""
    )
    keeper = ModelEntry(
        id="prov/keeper", provider="prov", model_name="keeper", tags=[], tier="balanced", notes=""
    )
    cfg.models = [victim, keeper]
    cfg.agent.default_model = victim.id
    cfg.agent.last_used_model = victim.id
    cfg.agent.vision_model = victim.id
    cfg.graphrag.embedding_model_id = victim.id
    cfg.graphrag.extraction_model_id = victim.id
    cf.save(cfg)

    rebuilt: list[bool] = []

    def fake_rebuild(c, s, a) -> None:
        rebuilt.append(True)

    monkeypatch.setattr(models_route, "_rebuild_registry", fake_rebuild)
    await models_route.delete_model(
        "prov/victim",
        app_state={"cfg": cf.load()},
        a=object(),
    )

    cfg2 = cf.load()
    assert [m.id for m in cfg2.models] == ["prov/keeper"]
    assert cfg2.agent.default_model == "prov/keeper"
    assert cfg2.agent.last_used_model == ""
    assert cfg2.agent.vision_model == ""
    assert cfg2.graphrag.embedding_model_id == ""
    assert cfg2.graphrag.extraction_model_id == ""
    assert rebuilt == [True]


async def test_delete_missing_model_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nexus.config_file as cf
    from fastapi import HTTPException
    from nexus.server.routes import models as models_route

    monkeypatch.setattr(cf, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cf.default_config()
    cf.save(cfg)

    with pytest.raises(HTTPException) as exc:
        await models_route.delete_model(
            "prov/does-not-exist",
            app_state={"cfg": cfg},
            a=object(),
        )
    assert exc.value.status_code == 404
