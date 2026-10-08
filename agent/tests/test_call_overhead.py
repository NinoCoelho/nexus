"""Measured per-call overhead, the tool allowlist, and the skill blurb.

Three fixed costs that every LLM call paid:

* the system-prompt + tool-schema overhead was a hardcoded 12,000 tokens that
  under-counted a default install by roughly 10,000, so every budget
  calculation believed there was more room than there was;
* the full tool registry (~15K tokens of JSON) went to every session,
  including a read-only coordinator sweep that may use almost none of it;
* the skills catalog rendered 49 untruncated descriptions, ~3.4K tokens —
  more than the identity block.
"""

from __future__ import annotations

import pytest

from nexus.agent.loop import overflow
from nexus.agent.prompt_builder import _skill_blurb


@pytest.fixture(autouse=True)
def _reset_overhead():
    overflow.invalidate_measured_overhead()
    yield
    overflow.invalidate_measured_overhead()


# ── measured overhead ──────────────────────────────────────────────────────


def test_falls_back_to_the_documented_floor() -> None:
    assert overflow.tools_and_system_overhead() == overflow.TOOLS_AND_SYSTEM_OVERHEAD


def test_measurement_takes_effect() -> None:
    overflow.set_measured_overhead(21_700)
    assert overflow.tools_and_system_overhead() == 21_700


def test_invalidation_restores_the_floor() -> None:
    overflow.set_measured_overhead(21_700)
    overflow.invalidate_measured_overhead()
    assert overflow.tools_and_system_overhead() == overflow.TOOLS_AND_SYSTEM_OVERHEAD


def test_nonsense_measurements_are_ignored() -> None:
    overflow.set_measured_overhead(0)
    overflow.set_measured_overhead(-5)
    assert overflow.tools_and_system_overhead() == overflow.TOOLS_AND_SYSTEM_OVERHEAD


def test_usable_tokens_follows_the_measurement() -> None:
    before = overflow.usable_tokens(200_000)
    overflow.set_measured_overhead(overflow.TOOLS_AND_SYSTEM_OVERHEAD + 10_000)
    after = overflow.usable_tokens(200_000)
    assert after == before - 10_000


def test_explicit_overhead_still_wins() -> None:
    overflow.set_measured_overhead(50_000)
    assert overflow.usable_tokens(200_000, tools_overhead=0) == (
        200_000 - overflow.OUTPUT_HEADROOM_TOKENS
    )


def test_overflow_check_uses_the_measurement() -> None:
    class _M:
        content = "x" * 400_000  # ~100K tokens
        tool_calls = None
        role = "user"

    overflow.set_measured_overhead(190_000)
    assert overflow.check_overflow([_M()], context_window=200_000).overflowed is True


# ── tool allowlist ─────────────────────────────────────────────────────────


class _Spec:
    def __init__(self, name: str):
        self.name = name


class _FakeRegistry:
    """Just the ``specs()`` / ``unregister()`` surface the filter touches."""

    def __init__(self, names):
        self._names = list(names)

    def specs(self):
        return [_Spec(n) for n in self._names]

    def unregister(self, name):
        self._names.remove(name)


def _filter(names, allowed):
    from nexus.agent._loom_bridge.registry import _apply_tool_allowlist

    reg = _FakeRegistry(names)
    _apply_tool_allowlist(reg, set(allowed))
    return reg._names


def test_allowlist_keeps_only_requested_tools() -> None:
    kept = _filter(
        ["nexus_sessions", "vault_read", "terminal", "datatable_manage"],
        {"nexus_sessions", "vault_read"},
    )
    assert sorted(kept) == ["nexus_sessions", "vault_read"]


def test_empty_allowlist_removes_everything() -> None:
    assert _filter(["a", "b"], set()) == []


def test_unknown_requested_tool_is_tolerated() -> None:
    """A typo in the allowlist must warn, not crash the agent build."""
    assert _filter(["a"], {"a", "does_not_exist"}) == ["a"]


def test_registry_without_specs_is_left_alone() -> None:
    from nexus.agent._loom_bridge.registry import _apply_tool_allowlist

    class _NoSpecs:
        def unregister(self, name):  # pragma: no cover — must never be called
            raise AssertionError("should not unregister without specs()")

    _apply_tool_allowlist(_NoSpecs(), {"a"})


def test_sweep_toolset_is_small_and_read_only() -> None:
    from nexus.coordinator import SWEEP_TOOLS

    assert "nexus_sessions" in SWEEP_TOOLS
    assert "session_dispatch" not in SWEEP_TOOLS, "a sweep must not dispatch"
    for writable in ("terminal", "vault_write", "datatable_manage", "dashboard_manage"):
        assert writable not in SWEEP_TOOLS


# ── skill blurb ────────────────────────────────────────────────────────────


def test_short_description_is_untouched() -> None:
    assert _skill_blurb("Use this for X.") == "Use this for X."


def test_first_sentence_is_kept_whole() -> None:
    desc = (
        "Use this whenever you need a thorough, source-backed research answer. "
        "Prefer over simple web_search for queries that require evidence "
        "evaluation and credibility assessment across many pages."
    )
    out = _skill_blurb(desc)
    assert out == "Use this whenever you need a thorough, source-backed research answer."


def test_long_first_sentence_is_clipped_on_a_word_boundary() -> None:
    desc = "word " * 100
    out = _skill_blurb(desc)
    assert len(out) <= 161
    assert out.endswith("…")
    assert "  " not in out


def test_whitespace_is_collapsed() -> None:
    assert _skill_blurb("a\n\n  b") == "a b"


def test_empty_description() -> None:
    assert _skill_blurb("") == ""
    assert _skill_blurb(None) == ""
