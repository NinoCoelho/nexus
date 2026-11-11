"""Progressive tool discovery — the advertised-specs contract.

With ~46 tools the JSON-schema block re-sent on every LLM call costs ~15K
tokens. Discovery shrinks it to a core set plus one ``tool_search`` tool.
These tests pin the properties that make hiding tools safe:

- hidden tools stay **dispatchable** (the registry keeps every handler),
- ``specs()`` shows only the advertised set (what the provider sees),
- ``tool_search`` returns full schemas in its result and (sticky mode)
  advertises them for the rest of the session,
- pure mode never mutates the advertised set,
- the coordinator path (``tool_allowlist``) never installs discovery,
- the prompt catalog lists *all* non-core tools regardless of stickiness, so
  sticky discoveries never rewrite the cacheable stable zone.
"""

from __future__ import annotations

import json

import pytest
from loom.tools.registry import ToolRegistry

from nexus.agent import tool_discovery
from nexus.agent._loom_bridge.registry import _SimpleToolHandler
from nexus.agent.context import CURRENT_SESSION_ID
from nexus.agent.llm import ToolSpec


def _spec(name: str, description: str = "") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description or f"Test tool {name}. Does {name.replace('_', ' ')} things.",
        parameters={"type": "object", "properties": {}},
    )


def _registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for n in names:
        reg.register(_SimpleToolHandler(_spec(n), lambda args, _n=n: f"ran {_n}"))
    return reg


class _AgentCfg:
    def __init__(
        self,
        *,
        on: bool = True,
        mode: str = "sticky",
        sticky_max: int = 28,
        core_tools: list[str] | None = None,
    ) -> None:
        self.tool_discovery = on
        self.tool_advertisement = mode
        self.tool_discovery_sticky_max = sticky_max
        self.core_tools = core_tools or []


@pytest.fixture()
def discovery(tmp_path, monkeypatch):
    """Fresh module state + config stub per test."""
    class _Cfg:
        agent = _AgentCfg()

    import nexus.config_file as config_file

    monkeypatch.setattr(config_file, "load_cached", lambda: _Cfg(), raising=False)
    monkeypatch.setattr(tool_discovery, "_INSTALLED_REGISTRY", None)
    monkeypatch.setattr(tool_discovery, "_STICKY", {})
    monkeypatch.setattr(tool_discovery, "_CORE", tool_discovery._DEFAULT_CORE)
    monkeypatch.setattr(tool_discovery, "_MODE", "sticky")
    token = CURRENT_SESSION_ID.set("sess-1")
    yield monkeypatch
    CURRENT_SESSION_ID.reset(token)


ALL_TOOLS = (
    "ask_user", "notify_user", "page", "terminal",
    "vault_read", "vault_write", "vault_list", "vault_search",
    "skills_list", "skill_view", "spawn_subagents",
    "context_status", "fork_session", "history_recall",
    "kanban_manage", "ocr_image", "web_search", "calendar_manage",
)


def test_install_filters_specs_but_keeps_dispatch(discovery) -> None:
    reg = _registry(*ALL_TOOLS)
    assert tool_discovery.install_tool_discovery(reg) is True

    names = {s.name for s in reg.specs()}
    core_and_search = tool_discovery._DEFAULT_CORE | {"tool_search"}
    assert names == ({n for n in ALL_TOOLS if n in core_and_search} | {"tool_search"})
    assert "kanban_manage" not in names and "ocr_image" not in names

    # Hidden does NOT mean unavailable: dispatch resolves it.
    import asyncio

    res = asyncio.run(reg.dispatch("kanban_manage", {}))
    assert res.text == "ran kanban_manage"


def test_tool_search_returns_schema_and_stickies(discovery) -> None:
    reg = _registry(*ALL_TOOLS)
    tool_discovery.install_tool_discovery(reg)
    out = json.loads(tool_discovery.handle_tool_search({"query": "kanban board tasks"}))
    assert out["ok"] is True and out["mode"] == "sticky"
    schemas = [t["name"] for t in out["tools"]]
    assert "kanban_manage" in schemas
    assert "parameters" in out["tools"][0], "the full schema must ride the result"

    # Sticky: now advertised for this session, and only for this session.
    assert "kanban_manage" in {s.name for s in reg.specs()}
    CURRENT_SESSION_ID.set("sess-2")
    assert "kanban_manage" not in {s.name for s in reg.specs()}


def test_pure_mode_never_mutates_advertised_set(discovery) -> None:
    class _Cfg:
        agent = _AgentCfg(mode="pure")

    import nexus.config_file as config_file

    discovery.setattr(config_file, "load_cached", lambda: _Cfg(), raising=False)
    reg = _registry(*ALL_TOOLS)
    tool_discovery.install_tool_discovery(reg)
    before = {s.name for s in reg.specs()}
    out = json.loads(tool_discovery.handle_tool_search({"query": "ocr image"}))
    assert out["ok"] is True and out["mode"] == "pure"
    assert "ocr_image" in [t["name"] for t in out["tools"]]
    assert {s.name for s in reg.specs()} == before


def test_sticky_cap_stops_adding_without_evicting(discovery) -> None:
    class _Cfg:
        agent = _AgentCfg(sticky_max=16)

    import nexus.config_file as config_file

    discovery.setattr(config_file, "load_cached", lambda: _Cfg(), raising=False)
    reg = _registry(*ALL_TOOLS)
    tool_discovery.install_tool_discovery(reg)
    tool_discovery.handle_tool_search({"names": ["kanban_manage", "ocr_image"]})
    tool_discovery.handle_tool_search({"names": ["web_search", "calendar_manage"]})
    names = {s.name for s in reg.specs()}
    assert "kanban_manage" in names  # first discovery sticks
    # Core (14 here, minus the 4 absentees) + tool_search + discoveries ≤ cap
    assert len(names) <= 16


def test_core_tools_override_validated_against_registry(discovery) -> None:
    class _Cfg:
        agent = _AgentCfg(core_tools=["ask_user", "vault_read", "not_a_real_tool"])

    import nexus.config_file as config_file

    discovery.setattr(config_file, "load_cached", lambda: _Cfg(), raising=False)
    reg = _registry(*ALL_TOOLS)
    tool_discovery.install_tool_discovery(reg)
    names = {s.name for s in reg.specs()}
    assert {"ask_user", "vault_read", "tool_search"} <= names
    assert "not_a_real_tool" not in names


def test_catalog_lists_all_non_core_stable_across_sticky(discovery) -> None:
    reg = _registry(*ALL_TOOLS)
    tool_discovery.install_tool_discovery(reg)
    catalog_before = tool_discovery.catalog_markdown()
    assert "`kanban_manage`" in catalog_before
    assert "tool_search" in catalog_before

    tool_discovery.handle_tool_search({"names": ["kanban_manage"]})
    catalog_after = tool_discovery.catalog_markdown()
    assert catalog_before == catalog_after, (
        "sticky discovery must not rewrite the stable-zone catalog"
    )


def test_disabled_config_leaves_registry_untouched(discovery) -> None:
    class _Cfg:
        agent = _AgentCfg(on=False)

    import nexus.config_file as config_file

    discovery.setattr(config_file, "load_cached", lambda: _Cfg(), raising=False)
    reg = _registry(*ALL_TOOLS)
    assert tool_discovery.install_tool_discovery(reg) is False
    names = {s.name for s in reg.specs()}
    assert names == set(ALL_TOOLS)


def test_bare_search_returns_directory(discovery) -> None:
    reg = _registry(*ALL_TOOLS)
    tool_discovery.install_tool_discovery(reg)
    out = json.loads(tool_discovery.handle_tool_search({}))
    assert out["ok"] is True
    entry = {d["name"]: d["blurb"] for d in out["directory"]}
    assert "kanban_manage" in entry and entry["kanban_manage"]
