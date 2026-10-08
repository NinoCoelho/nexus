"""Prompt-cache breakpoints.

The tool schemas plus the stable half of the system prompt are ~15K tokens
re-sent on every LLM call, including each iteration of one multi-step turn.
Nothing requested caching, so all of it was billed at full price.

Two things had to be true for caching to work at all, and both are pinned
here: the cacheable region must be a real *prefix* (the time-and-location
block used to sit second in the prompt and change every minute, which made
the prefix byte-unstable), and the marker that delimits it must never reach a
model.
"""

from __future__ import annotations

from nexus.agent.llm.prompt_cache import (
    anthropic_system_and_tools,
    bedrock_system_and_tools,
)
from nexus.agent.prompt_builder import (
    CACHE_BREAKPOINT_MARKER,
    split_cache_zones,
    strip_cache_marker,
)

_PROMPT = f"STABLE identity and skills\n{CACHE_BREAKPOINT_MARKER}\nVOLATILE time 12:34"


# ── marker handling ────────────────────────────────────────────────────────


def test_split_separates_the_zones() -> None:
    stable, volatile = split_cache_zones(_PROMPT)
    assert stable == "STABLE identity and skills"
    assert volatile == "VOLATILE time 12:34"
    assert CACHE_BREAKPOINT_MARKER not in stable
    assert CACHE_BREAKPOINT_MARKER not in volatile


def test_split_without_a_marker_treats_everything_as_stable() -> None:
    stable, volatile = split_cache_zones("no marker here")
    assert stable == "no marker here"
    assert volatile == ""


def test_strip_removes_every_marker() -> None:
    out = strip_cache_marker(_PROMPT)
    assert CACHE_BREAKPOINT_MARKER not in out
    assert "STABLE identity and skills" in out
    assert "VOLATILE time 12:34" in out


# ── Anthropic ──────────────────────────────────────────────────────────────


def test_anthropic_emits_two_blocks_with_the_prefix_cached() -> None:
    system, tools = anthropic_system_and_tools(_PROMPT, [])
    assert isinstance(system, list) and len(system) == 2
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert system[0]["text"] == "STABLE identity and skills"
    # The volatile block must NOT be cached, or every minute invalidates it.
    assert "cache_control" not in system[1]
    assert system[1]["text"] == "VOLATILE time 12:34"
    assert tools == []


def test_anthropic_caches_the_last_tool() -> None:
    tools = [{"name": "a"}, {"name": "b"}]
    _system, out = anthropic_system_and_tools(_PROMPT, tools)
    assert "cache_control" not in out[0]
    assert out[-1]["cache_control"] == {"type": "ephemeral"}
    # The caller's list is not mutated.
    assert "cache_control" not in tools[-1]


def test_anthropic_without_a_marker_caches_the_whole_prompt() -> None:
    system, _ = anthropic_system_and_tools("one zone only", [])
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_empty_system_is_passed_through() -> None:
    system, tools = anthropic_system_and_tools("", [{"name": "a"}])
    assert system == ""
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_never_leaks_the_marker() -> None:
    system, _ = anthropic_system_and_tools(_PROMPT, [])
    assert all(CACHE_BREAKPOINT_MARKER not in b["text"] for b in system)


# ── Bedrock ────────────────────────────────────────────────────────────────


def test_bedrock_inserts_a_cache_point_between_the_zones() -> None:
    system, tools = bedrock_system_and_tools([{"text": _PROMPT}], [])
    assert system == [
        {"text": "STABLE identity and skills"},
        {"cachePoint": {"type": "default"}},
        {"text": "VOLATILE time 12:34"},
    ]
    assert tools == []


def test_bedrock_appends_a_tool_cache_point() -> None:
    _system, tools = bedrock_system_and_tools([{"text": _PROMPT}], [{"toolSpec": {}}])
    assert tools[-1] == {"cachePoint": {"type": "default"}}
    assert len(tools) == 2


def test_bedrock_keeps_later_system_blocks_after_the_breakpoint() -> None:
    """Only the first block is the built prompt; later ones are carried
    context (the session-memory summary) and belong on the volatile side."""
    system, _ = bedrock_system_and_tools(
        [{"text": _PROMPT}, {"text": "[Session Memory] carried"}], []
    )
    assert system[-1] == {"text": "[Session Memory] carried"}
    assert system.index({"cachePoint": {"type": "default"}}) == 1


def test_bedrock_without_a_marker_still_caches() -> None:
    system, _ = bedrock_system_and_tools([{"text": "one zone"}], [])
    assert system == [{"text": "one zone"}, {"cachePoint": {"type": "default"}}]


def test_bedrock_empty_system() -> None:
    system, tools = bedrock_system_and_tools([], [{"toolSpec": {}}])
    assert system == []
    assert tools[-1] == {"cachePoint": {"type": "default"}}


def test_bedrock_never_leaks_the_marker() -> None:
    system, _ = bedrock_system_and_tools([{"text": _PROMPT}], [])
    for block in system:
        assert CACHE_BREAKPOINT_MARKER not in str(block.get("text", ""))


def test_bedrock_cache_support_is_family_gated() -> None:
    """Bedrock fronts families that reject cachePoint; sending it anyway
    would break those users outright."""
    from nexus.agent.llm.prompt_cache import bedrock_supports_cache

    assert bedrock_supports_cache("us.anthropic.claude-opus-4-5") is True
    assert bedrock_supports_cache("anthropic.claude-3-5-sonnet-20241022-v2:0") is True
    assert bedrock_supports_cache("us.amazon.nova-pro-v1:0") is True
    assert bedrock_supports_cache("meta.llama3-70b-instruct-v1:0") is False
    assert bedrock_supports_cache("amazon.titan-text-express-v1") is False
    assert bedrock_supports_cache("") is False


# ── the prefix must actually be stable ─────────────────────────────────────


class _StubRegistry:
    """Enough of SkillRegistry for the prompt builder: an empty catalog."""

    def descriptions(self):
        return []


def _prompt(monkeypatch, **kwargs) -> str:
    """Build a prompt without touching the developer's real ~/.nexus."""
    import nexus.agent.prompt_builder as pb

    monkeypatch.setattr(pb, "_migrate_legacy_memory", lambda: None)
    monkeypatch.setattr(pb, "_memory_summary", lambda: "")
    monkeypatch.setattr(pb, "_credentials_block", lambda: "")
    monkeypatch.setattr(pb, "_site_credentials_block", lambda: "")
    monkeypatch.setattr(pb, "_user_block", lambda home: "")
    monkeypatch.setattr(
        pb, "_time_location_block", lambda: "## Current time & location\n\n- UTC: now"
    )
    return pb.build_system_prompt(_StubRegistry(), **kwargs)


def test_volatile_time_block_is_behind_the_breakpoint(monkeypatch) -> None:
    """The regression that made caching impossible: a per-minute timestamp
    ahead of everything else in the prompt."""
    stable, volatile = split_cache_zones(_prompt(monkeypatch))
    assert "Current time & location" not in stable
    assert "Current time & location" in volatile


def test_identity_and_skills_are_in_the_cacheable_prefix(monkeypatch) -> None:
    stable, _ = split_cache_zones(_prompt(monkeypatch))
    assert "## Available skills" in stable
    assert "## Status updates" in stable


def test_per_session_content_is_behind_the_breakpoint(monkeypatch) -> None:
    """Project and session context must not sit in the shared prefix, or the
    cache entry stops being reusable across sessions."""
    _stable, volatile = split_cache_zones(
        _prompt(monkeypatch, context="session ctx", coordinator=True)
    )
    assert "session ctx" in volatile
    assert "## Coordinator" in volatile


def test_stable_zone_is_identical_across_sessions(monkeypatch) -> None:
    a, _ = split_cache_zones(_prompt(monkeypatch, context="one"))
    b, _ = split_cache_zones(_prompt(monkeypatch, context="two", coordinator=True))
    assert a == b
