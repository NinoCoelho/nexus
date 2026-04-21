"""Tests for the agent/skill/session graph builder."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nexus.server.graph import build_agent_graph, _cache
import nexus.server.graph as graph_module


def _make_skill(name: str, description: str = "A skill", trust: str = "builtin"):
    skill = MagicMock()
    skill.name = name
    skill.description = description
    skill.trust = trust
    return skill


def _make_session_summary(id: str, title: str, message_count: int = 0, updated_at: int = 0):
    summary = MagicMock()
    summary.id = id
    summary.title = title
    summary.message_count = message_count
    summary.updated_at = updated_at
    return summary


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset the module-level cache between tests."""
    graph_module._cache = None
    yield
    graph_module._cache = None


def test_build_agent_graph_basic():
    """Graph contains hub node, skill nodes, and edges."""
    registry = MagicMock()
    skill_a = _make_skill("fetch-url", description="Fetch a URL")
    skill_b = _make_skill("write-file", description="Write a file")
    registry.list.return_value = [skill_a, skill_b]

    store = MagicMock()
    store.list.return_value = []

    result = build_agent_graph(registry, store)

    node_ids = {n["id"] for n in result["nodes"]}
    assert "agent:nexus" in node_ids
    assert "skill:fetch-url" in node_ids
    assert "skill:write-file" in node_ids

    # Hub node shape
    hub = next(n for n in result["nodes"] if n["id"] == "agent:nexus")
    assert hub["type"] == "agent"
    assert hub["label"] == "Nexus"

    # Skill node shape
    skill_node = next(n for n in result["nodes"] if n["id"] == "skill:fetch-url")
    assert skill_node["type"] == "skill"
    assert skill_node["label"] == "fetch-url"
    assert skill_node["meta"]["trust"] == "builtin"
    assert skill_node["meta"]["description"] == "Fetch a URL"

    # Every skill has an edge to the hub
    edge_targets = {e["target"] for e in result["edges"]}
    assert "agent:nexus" in edge_targets


def test_build_agent_graph_with_sessions():
    """Session nodes are added and linked to touched skills."""
    registry = MagicMock()
    skill_a = _make_skill("search")
    registry.list.return_value = [skill_a]

    sess_summary = _make_session_summary(
        id="abc123def456",
        title="Test session",
        message_count=4,
        updated_at=1700000000,
    )

    # Simulate a session that touched the "search" skill
    from nexus.agent.llm import ChatMessage, Role, ToolCall
    tool_call = ToolCall(id="tc1", name="search__run", arguments="{}")
    msg_with_tool = ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tool_call])

    mock_session = MagicMock()
    mock_session.history = [msg_with_tool]

    store = MagicMock()
    store.list.return_value = [sess_summary]
    store.get.return_value = mock_session

    result = build_agent_graph(registry, store)

    node_ids = {n["id"] for n in result["nodes"]}
    assert "session:abc123def456" in node_ids

    sess_node = next(n for n in result["nodes"] if n["id"] == "session:abc123def456")
    assert sess_node["type"] == "session"
    assert sess_node["label"] == "Test session"
    assert sess_node["meta"]["message_count"] == 4

    # Edge from session to touched skill
    edges = {(e["source"], e["target"]) for e in result["edges"]}
    assert ("session:abc123def456", "skill:search") in edges


def test_build_agent_graph_no_sessions():
    """Graph works fine with no sessions."""
    registry = MagicMock()
    registry.list.return_value = []

    store = MagicMock()
    store.list.return_value = []

    result = build_agent_graph(registry, store)

    assert result["nodes"] == [{"id": "agent:nexus", "label": "Nexus", "type": "agent", "meta": {}}]
    assert result["edges"] == []


def test_cache_is_used(monkeypatch):
    """Second call within TTL returns cached result without re-querying."""
    registry = MagicMock()
    registry.list.return_value = [_make_skill("tool-a")]

    store = MagicMock()
    store.list.return_value = []

    first = build_agent_graph(registry, store)
    second = build_agent_graph(registry, store)

    # list() should only have been called once (cache hit on second call)
    assert registry.list.call_count == 1
    assert first is second
