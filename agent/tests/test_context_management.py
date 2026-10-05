"""Best-in-class context-management additions.

Covers:
  * soft-threshold auto-compact predicate (``should_soft_compact``) —
    percentage crossing + 15% growth hysteresis
  * post-compaction re-injection of recently-accessed vault paths
  * persistent session notes — persist/load round-trip and history seeding

Microcompaction (``stub_older_than``) tests live in test_compact_history.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from nexus import home as nexus_home
from nexus.agent.llm import ChatMessage, ChatResponse, Role, StopReason, ToolCall
from nexus.agent.loop.budget import should_soft_compact
from nexus.agent.loop.compact import _recent_vault_paths, compact_and_summarize
from nexus.agent.loop.summarize import (
    _SUMMARY_PREFIX,
    load_session_summary,
    persist_session_summary,
)


# ── should_soft_compact ────────────────────────────────────────────────────


def test_soft_compact_disabled_when_pct_zero() -> None:
    assert should_soft_compact(est_tokens=999_999, usable_tokens=1000, threshold_pct=0, watermark=0) is False


def test_soft_compact_fires_when_threshold_crossed() -> None:
    # 85% of 100k usable = 85k; 90k crosses it, no watermark yet.
    assert should_soft_compact(est_tokens=90_000, usable_tokens=100_000, threshold_pct=85, watermark=0) is True


def test_soft_compact_quiet_below_threshold() -> None:
    assert should_soft_compact(est_tokens=80_000, usable_tokens=100_000, threshold_pct=85, watermark=0) is False


def test_soft_compact_hysteresis_blocks_retrigger() -> None:
    # Threshold crossed but est is within 15% of the last-compaction
    # watermark (90k <= 85k * 1.15 = 97.7k) → hold off.
    assert should_soft_compact(est_tokens=90_000, usable_tokens=100_000, threshold_pct=85, watermark=85_000) is False


def test_soft_compact_hysteresis_rearms_after_growth() -> None:
    assert should_soft_compact(est_tokens=99_000, usable_tokens=100_000, threshold_pct=85, watermark=85_000) is True


# ── _recent_vault_paths ────────────────────────────────────────────────────


def _call(name: str, **args: object) -> ChatMessage:
    return ChatMessage(role=Role.ASSISTANT, content="", tool_calls=[ToolCall(id="t", name=name, arguments=args)])


def test_recent_vault_paths_most_recent_first_deduped() -> None:
    msgs = [
        _call("vault_read", path="old.md"),
        _call("vault_read", path="work.md"),
        _call("vault_write", path="work.md"),
        _call("vault_read", path="final.md"),
    ]
    assert _recent_vault_paths(msgs) == ["final.md", "work.md", "old.md"]


def test_recent_vault_paths_ignores_non_vault_and_missing_path() -> None:
    msgs = [
        _call("http_call", url="https://x"),
        _call("web_scrape", path="https://not-a-vault"),
        _call("vault_read"),
        ChatMessage(role=Role.USER, content="plain"),
    ]
    assert _recent_vault_paths(msgs) == []


def test_recent_vault_paths_capped_at_five() -> None:
    msgs = [_call("vault_read", path=f"f{i}.md") for i in range(9)]
    # Most recent first → f8..f4.
    assert _recent_vault_paths(msgs) == [f"f{i}.md" for i in range(8, 3, -1)]


# ── end-to-end through compact_and_summarize ───────────────────────────────


class _MockSummarizer:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, *, tools=None, model=None, max_tokens=None):
        self.calls += 1
        return ChatResponse(content="## Session Memory\n- Goals: testing", stop_reason=StopReason.STOP)


def _fat_history(n: int = 40, paths: list[str] | None = None) -> list[ChatMessage]:
    paths = paths if paths is not None else [f"note{i}.md" for i in range(n)]
    msgs: list[ChatMessage] = []
    for i in range(n):
        msgs.append(_call("vault_read", path=paths[i]))
        msgs.append(
            ChatMessage(
                role=Role.TOOL,
                content=json.dumps({"ok": True, "content": "body " * 200}),
                tool_call_id="t",
                name="vault_read",
            )
        )
        msgs.append(ChatMessage(role=Role.USER, content=f"turn {i} " + "x" * 300))
    return msgs


async def test_compact_and_summarize_reinjects_vault_paths(tmp_path: Path) -> None:
    nexus_home.set_user_home(tmp_path)
    try:
        provider = _MockSummarizer()
        history = _fat_history(paths=["alpha.md", "beta.md", "beta.md", "gamma.md"] * 10)
        result, report = await compact_and_summarize(
            history,
            context_window=32_000,
            session_id="sess-reinject",
            provider=provider,
            strategy="summarize_only",
            force_summarize=True,
        )
        assert report.summarized is True
        assert "gamma.md" in report.reinjected_paths
        summary_msg = result[0]
        assert summary_msg.role == Role.SYSTEM
        assert summary_msg.content.startswith(_SUMMARY_PREFIX)
        assert "Recently accessed files (re-read via vault_read):" in summary_msg.content
        assert "vault://gamma.md" in summary_msg.content
    finally:
        nexus_home.set_user_home(None)


# ── persistent session notes ───────────────────────────────────────────────


async def test_session_notes_round_trip(tmp_path: Path) -> None:
    nexus_home.set_user_home(tmp_path)
    try:
        assert load_session_summary("nope") is None
        path = persist_session_summary("s1", "## Session Memory\n- Goals: live", model_id="m")
        assert path is not None and Path(path).is_file()
        body = load_session_summary("s1")
        assert body is not None
        assert body.startswith("## Session Memory")
        assert "session_id" not in body  # frontmatter stripped
    finally:
        nexus_home.set_user_home(None)


async def test_compact_persists_notes(tmp_path: Path) -> None:
    nexus_home.set_user_home(tmp_path)
    try:
        provider = _MockSummarizer()
        _, report = await compact_and_summarize(
            _fat_history(40),
            context_window=32_000,
            session_id="sess-notes",
            provider=provider,
            strategy="summarize_only",
            force_summarize=True,
        )
        assert report.notes_path is not None
        assert load_session_summary("sess-notes") is not None
    finally:
        nexus_home.set_user_home(None)


def _seed_agent():
    """Bare Agent-like object exposing only _seed_session_notes' needs."""
    from nexus.agent.loop.agent import Agent

    return Agent.__new__(Agent)


def test_seed_session_notes_injects_when_absent(tmp_path: Path) -> None:
    nexus_home.set_user_home(tmp_path)
    try:
        persist_session_summary("s-seed", "- Goals: resumed work")
        agent = _seed_agent()
        history = [ChatMessage(role=Role.USER, content="continue")]
        out = agent._seed_session_notes(history, "s-seed")
        assert out is not history
        assert out[0].role == Role.SYSTEM
        assert out[0].content.startswith(_SUMMARY_PREFIX)
        assert "- Goals: resumed work" in out[0].content
        assert out[1] is history[0]
    finally:
        nexus_home.set_user_home(None)


def test_seed_session_notes_no_double_injection(tmp_path: Path) -> None:
    nexus_home.set_user_home(tmp_path)
    try:
        persist_session_summary("s-dup", "- Goals: x")
        agent = _seed_agent()
        history = [
            ChatMessage(role=Role.SYSTEM, content=f"{_SUMMARY_PREFIX} — already here]\nx"),
            ChatMessage(role=Role.USER, content="q"),
        ]
        out = agent._seed_session_notes(history, "s-dup")
        assert out is history  # untouched
    finally:
        nexus_home.set_user_home(None)


def test_seed_session_notes_noop_without_file(tmp_path: Path) -> None:
    nexus_home.set_user_home(tmp_path)
    try:
        agent = _seed_agent()
        history = [ChatMessage(role=Role.USER, content="q")]
        assert agent._seed_session_notes(history, "missing") is history
    finally:
        nexus_home.set_user_home(None)
