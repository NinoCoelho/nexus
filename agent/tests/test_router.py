"""Smoke tests for the built-in embedding-similarity router and tier heuristics."""

from __future__ import annotations

from nexus.agent.model_profiles import suggest_tier, suggestion_source
from nexus.agent.router import _fallback
from nexus.config_file import (
    AgentConfig,
    ModelEntry,
    NexusConfig,
    ProviderConfig,
)


def _make_cfg() -> NexusConfig:
    return NexusConfig(
        agent=AgentConfig(
            routing_mode="auto",
            default_model="openai/gpt-4o-mini",
        ),
        providers={
            "openai": ProviderConfig(base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
            "anthropic": ProviderConfig(api_key_env="ANTHROPIC_API_KEY"),
        },
        models=[
            ModelEntry(id="openai/gpt-4o-mini", provider="openai", model_name="gpt-4o-mini", tier="fast"),
            ModelEntry(
                id="anthropic/claude-sonnet-4-6",
                provider="anthropic",
                model_name="claude-sonnet-4-6",
                tier="balanced",
            ),
            ModelEntry(
                id="anthropic/claude-opus-4-7",
                provider="anthropic",
                model_name="claude-opus-4-7",
                tier="heavy",
            ),
        ],
    )


def test_suggest_tier_known_fast():
    assert suggest_tier("gpt-4o-mini") == "fast"
    assert suggest_tier("claude-haiku-4-5") == "fast"
    assert suggest_tier("gemini-2.0-flash") == "fast"
    assert suggest_tier("llama-3-8b") == "fast"


def test_suggest_tier_known_heavy():
    assert suggest_tier("claude-opus-4-7") == "heavy"
    assert suggest_tier("o1-preview") == "heavy"
    assert suggest_tier("o3-mini") == "fast"  # mini wins — first-match ordering


def test_suggest_tier_known_balanced():
    assert suggest_tier("claude-sonnet-4-6") == "balanced"
    assert suggest_tier("gpt-4o") == "balanced"
    assert suggest_tier("deepseek-v3") == "balanced"


def test_suggest_tier_unknown_defaults_balanced():
    assert suggest_tier("totally-made-up-model") == "balanced"
    assert suggestion_source("totally-made-up-model") == "default"


def test_fallback_uses_default_model():
    cfg = _make_cfg()
    assert _fallback(cfg) == "openai/gpt-4o-mini"


def test_fallback_empty_config():
    cfg = NexusConfig(agent=AgentConfig(), providers={}, models=[])
    assert _fallback(cfg) == ""
