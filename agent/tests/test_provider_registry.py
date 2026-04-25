"""build_registry skips unconfigured providers and respects env keys."""

from __future__ import annotations

import pytest

from nexus.agent.registry import build_registry
from nexus.config_schema import NexusConfig, ProviderConfig, ModelEntry


def _cfg(providers: dict[str, ProviderConfig], models: list[ModelEntry] | None = None) -> NexusConfig:
    return NexusConfig(
        providers=providers,
        models=models or [],
    )


def test_provider_skipped_when_env_var_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = _cfg({
        "openai": ProviderConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            type="openai_compat",
        ),
    })
    reg = build_registry(cfg)
    assert "openai" not in reg._providers


def test_provider_registered_when_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _cfg({
        "openai": ProviderConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            type="openai_compat",
        ),
    })
    reg = build_registry(cfg)
    assert "openai" in reg._providers


def test_ollama_registered_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    cfg = _cfg({
        "local": ProviderConfig(
            base_url="http://localhost:11434",
            type="ollama",
        ),
    })
    reg = build_registry(cfg)
    assert "local" in reg._providers


def test_switching_providers_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a /config PATCH that swaps the active provider key var."""
    monkeypatch.setenv("KEY_A", "first")
    monkeypatch.delenv("KEY_B", raising=False)

    cfg1 = _cfg({
        "vendor": ProviderConfig(
            base_url="https://example/v1",
            api_key_env="KEY_A",
            type="openai_compat",
        ),
    })
    reg1 = build_registry(cfg1)
    assert "vendor" in reg1._providers

    # Now flip to a key var that isn't set.
    cfg2 = _cfg({
        "vendor": ProviderConfig(
            base_url="https://example/v1",
            api_key_env="KEY_B",
            type="openai_compat",
        ),
    })
    reg2 = build_registry(cfg2)
    assert "vendor" not in reg2._providers
