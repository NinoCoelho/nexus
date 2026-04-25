"""acp_call should only register when ACP env vars are set."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nexus.agent._loom_bridge.registry import AgentHandlers, build_tool_registry
from nexus.skills.registry import SkillRegistry


@pytest.fixture
def skill_registry():
    with tempfile.TemporaryDirectory() as td:
        yield SkillRegistry(skills_dir=Path(td))


def _has_acp(registry) -> bool:
    return any(h.tool.name == "acp_call" for h in registry._handlers.values())


def test_acp_call_hidden_when_unconfigured(monkeypatch, skill_registry):
    monkeypatch.delenv("NEXUS_ACP_GATEWAY_URL", raising=False)
    monkeypatch.delenv("NEXUS_ACP_TOKEN", raising=False)
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    assert not _has_acp(registry)


def test_acp_call_registered_when_configured(monkeypatch, skill_registry):
    monkeypatch.setenv("NEXUS_ACP_GATEWAY_URL", "wss://acp.example/agent")
    monkeypatch.setenv("NEXUS_ACP_TOKEN", "deadbeef")
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    assert _has_acp(registry)
