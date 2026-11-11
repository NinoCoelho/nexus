"""Integration: real registry + discovery + prompt + overhead measurement.

End-to-end guard for the Phase 2/3 wiring: with the *real* tool registry,
installing discovery must hide the per-domain tools, keep the core dispatchable,
render the "More tools (on demand)" catalog into the system prompt's stable
zone, and make the measured per-call overhead reflect the advertised set
(well under the old 12K floor).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nexus.agent import tool_discovery
from nexus.agent._loom_bridge.registry import AgentHandlers, build_tool_registry
from nexus.agent.loop._builder import _measure_call_overhead
from nexus.agent.loop.overflow import (
    TOOLS_AND_SYSTEM_OVERHEAD,
    invalidate_measured_overhead,
    tools_and_system_overhead,
)
from nexus.agent.prompt_builder import CACHE_BREAKPOINT_MARKER, build_system_prompt
from nexus.skills.registry import SkillRegistry


@pytest.fixture
def skill_registry(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        skills_dir = Path(td)
        with tempfile.TemporaryDirectory() as bundled:
            monkeypatch.setenv("NEXUS_BUILTIN_SKILLS_DIR", bundled)
            yield SkillRegistry(skills_dir=skills_dir)


@pytest.fixture
def discovery_on(monkeypatch):
    class _AgentCfg:
        tool_discovery = True
        tool_advertisement = "sticky"
        tool_discovery_sticky_max = 28
        core_tools: list[str] = []

    class _Cfg:
        agent = _AgentCfg()

    import nexus.config_file as config_file

    monkeypatch.setattr(config_file, "load_cached", lambda: _Cfg(), raising=False)
    monkeypatch.setattr(tool_discovery, "_INSTALLED_REGISTRY", None)
    monkeypatch.setattr(tool_discovery, "_STICKY", {})
    yield


def test_real_registry_hides_domain_tools_and_renders_catalog(
    skill_registry, discovery_on, monkeypatch
) -> None:
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    assert tool_discovery._INSTALLED_REGISTRY is registry

    advertised = {s.name for s in registry.specs()}
    # Core keeps the everyday primitives…
    for expected in ("terminal", "ask_user", "vault_read", "vault_write",
                     "skills_list", "skill_view", "spawn_subagents",
                     "context_status", "fork_session", "history_recall",
                     "tool_search"):
        assert expected in advertised, expected
    # …and hides the per-domain tools.
    for hidden in ("kanban_manage", "ocr_image", "calendar_manage",
                   "dashboard_manage", "visualize_table", "nexus_kb_search"):
        assert hidden not in advertised, hidden
        assert hidden in registry._handlers, f"{hidden} must stay dispatchable"

    # The prompt catalog lists every non-core tool, so the model can find them.
    prompt = build_system_prompt(skill_registry)
    assert "More tools (on demand)" in prompt
    assert "`kanban_manage`" in prompt
    assert prompt.index("More tools (on demand)") < prompt.index(
        CACHE_BREAKPOINT_MARKER  # stable zone, before the breakpoint
    )


def test_overhead_measurement_uses_advertised_set(
    skill_registry, discovery_on
) -> None:
    invalidate_measured_overhead()
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    n_advertised = len(registry.specs())
    assert n_advertised < len(registry._handlers), "discovery must be installed"

    _measure_call_overhead(registry, skill_registry, None)
    measured = tools_and_system_overhead()
    assert measured != TOOLS_AND_SYSTEM_OVERHEAD, "measurement must have run"
    # The advertised set + prompt should measure far under the historical
    # ~22K (≈15K schemas + ~7K prompt) a full registry produced.
    assert measured < 12_000
