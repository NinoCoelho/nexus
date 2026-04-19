"""Smoke tests for the auto-router heuristic."""

import pytest
from nexus.agent.router import choose_model
from nexus.config_file import NexusConfig, AgentConfig, ProviderConfig, ModelEntry, ModelStrengths


def _make_cfg() -> NexusConfig:
    return NexusConfig(
        agent=AgentConfig(routing_mode="auto", default_model="openai/gpt-4o-mini"),
        providers={
            "openai": ProviderConfig(base_url="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY"),
            "anthropic": ProviderConfig(api_key_env="ANTHROPIC_API_KEY"),
        },
        models=[
            ModelEntry(
                id="openai/gpt-4o-mini",
                provider="openai",
                model_name="gpt-4o-mini",
                tags=["fast"],
                strengths=ModelStrengths(speed=9, cost=9, reasoning=5, coding=6),
            ),
            ModelEntry(
                id="anthropic/claude-sonnet-4-6",
                provider="anthropic",
                model_name="claude-sonnet-4-6",
                tags=["balanced"],
                strengths=ModelStrengths(speed=7, cost=6, reasoning=9, coding=9),
            ),
        ],
    )


def test_coding_message():
    cfg = _make_cfg()
    result = choose_model("def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)", cfg)
    assert result == "anthropic/claude-sonnet-4-6"  # coding=9 beats coding=6


def test_reasoning_message():
    cfg = _make_cfg()
    result = choose_model("why is the sky blue and what does it tell us about atmospheric physics?", cfg)
    assert result == "anthropic/claude-sonnet-4-6"  # reasoning=9 beats reasoning=5


def test_trivial_message():
    cfg = _make_cfg()
    result = choose_model("hi", cfg)
    assert result == "openai/gpt-4o-mini"  # speed=9 beats speed=7


def test_fixed_routing():
    cfg = _make_cfg()
    cfg.agent.routing_mode = "fixed"
    result = choose_model("def foo(): pass", cfg)
    assert result == "openai/gpt-4o-mini"  # always default
