"""Slash-command tests: typing ``/compact`` in chat must compact the session
in place WITHOUT calling the LLM, and stream a status reply that shows the
savings. The bug this exists to prevent: ``/compact`` was being routed to
the LLM as a normal user message, the LLM helpfully wrote a "summary of the
conversation" — but the session history was never actually compacted, so
the next turn hit the same context_overflow."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    Role,
    StopReason,
    StreamEvent,
    ToolSpec,
)
from nexus.agent.loop import Agent
from nexus.server.app import create_app
from nexus.server.routes.chat_slash import is_slash_command
from nexus.server.session_store import SessionStore
from nexus.server.settings import SettingsStore
from nexus.skills.registry import SkillRegistry


class _AssertNeverProvider(LLMProvider):
    """If the slash-command fast path works, the LLM is never dialed.
    These methods raise to make a leak loud — they replace the real provider
    so a regression that routes /compact through the agent loop fails the
    test instead of silently mocking out."""

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResponse:
        raise AssertionError("LLM must not be called for /compact")

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        raise AssertionError("LLM stream must not be called for /compact")
        yield  # pragma: no cover — generator type discipline only

    async def aclose(self) -> None:
        pass


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, SessionStore]]:
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_AssertNeverProvider(), registry=registry)
    app = create_app(
        agent=agent, registry=registry, sessions=sessions, settings_store=settings
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, sessions


def test_is_slash_command_recognises_compact() -> None:
    assert is_slash_command("/compact") == "compact"
    assert is_slash_command("  /compact  ") == "compact"
    assert is_slash_command("/compact aggressive") == "compact"
    assert is_slash_command("/COMPACT") == "compact"


def test_is_slash_command_rejects_non_commands() -> None:
    assert is_slash_command("compact") is None
    assert is_slash_command("hi /compact later") is None
    assert is_slash_command("/unknown") is None
    assert is_slash_command("") is None
    assert is_slash_command("/") is None


def _seed_overflowed_history(sessions: SessionStore, sid: str) -> int:
    """Plant a session with one giant tool result that compaction will
    shrink. Returns the original total content size in bytes."""
    huge_csv = "col_a,col_b\n" + "\n".join(f"{i},{i*2}" for i in range(50_000))
    huge_tool = json.dumps({"ok": True, "path": "data.csv", "content": huge_csv})
    history = [
        ChatMessage(role=Role.USER, content="lê o csv"),
        ChatMessage(role=Role.ASSISTANT, content="Lendo."),
        ChatMessage(
            role=Role.TOOL, content=huge_tool, tool_call_id="t1", name="vault_read"
        ),
        ChatMessage(role=Role.ASSISTANT, content="50K rows. O que você quer?"),
    ]
    sessions.replace_history(sid, history)
    return sum(len(m.content or "") for m in history)


async def _read_stream(res: httpx.Response) -> list[tuple[str, dict]]:
    """Parse SSE frames into [(event_name, json_payload), ...]."""
    events: list[tuple[str, dict]] = []
    text = (await res.aread()).decode("utf-8")
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if name and data:
            try:
                events.append((name, json.loads(data)))
            except json.JSONDecodeError:
                events.append((name, {"_raw": data}))
    return events


async def test_compact_slash_compacts_history_without_llm(client) -> None:
    ac, sessions = client
    sess = sessions.create()
    bytes_before = _seed_overflowed_history(sessions, sess.id)

    res = await ac.post(
        "/chat/stream",
        json={"session_id": sess.id, "message": "/compact"},
    )
    assert res.status_code == 200
    events = await _read_stream(res)

    # The slash handler streams exactly one delta + one done. No tool / error.
    kinds = [name for name, _ in events]
    assert "delta" in kinds
    assert "done" in kinds
    assert "error" not in kinds
    assert "tool" not in kinds

    done_payload = next(p for n, p in events if n == "done")
    report = done_payload.get("compact_report")
    assert report is not None, "done event must include compact_report"
    assert report["compacted"] >= 1
    assert report["saved_bytes"] > 0

    # History was actually rewritten in place. Confirm by re-reading.
    sess2 = sessions.get(sess.id)
    assert sess2 is not None
    bytes_after = sum(len(m.content or "") for m in sess2.history)
    assert bytes_after < bytes_before // 10, (
        f"expected ≥10x shrink; before={bytes_before} after={bytes_after}"
    )

    # The user's /compact line + assistant status should be appended.
    last_two = sess2.history[-2:]
    assert last_two[0].role == Role.USER
    assert last_two[0].content == "/compact"
    assert last_two[1].role == Role.ASSISTANT
    assert "Compacted" in (last_two[1].content or "")


async def test_compact_slash_reports_no_op_when_nothing_oversized(client) -> None:
    ac, sessions = client
    sess = sessions.create()
    sessions.replace_history(
        sess.id,
        [
            ChatMessage(role=Role.USER, content="oi"),
            ChatMessage(role=Role.ASSISTANT, content="oi"),
        ],
    )

    res = await ac.post(
        "/chat/stream", json={"session_id": sess.id, "message": "/compact"}
    )
    events = await _read_stream(res)
    done = next(p for n, p in events if n == "done")
    assert done["compact_report"]["compacted"] == 0
    assert "No oversized" in done["reply"] or "Nothing new" in done["reply"]


async def test_compact_slash_aggressive_uses_smaller_threshold(client) -> None:
    """`/compact aggressive` catches medium-sized tool results that the
    default 32KB threshold leaves alone. Crucial for sessions with many
    10-20KB results that collectively overflow."""
    ac, sessions = client
    sess = sessions.create()
    medium = json.dumps({"ok": True, "content": "x" * 12_000})  # 12KB — under default
    sessions.replace_history(
        sess.id,
        [ChatMessage(role=Role.TOOL, content=medium, tool_call_id="t1", name="x")],
    )

    # Default threshold leaves it alone.
    r_default = await ac.post(
        "/chat/stream", json={"session_id": sess.id, "message": "/compact"}
    )
    e_default = await _read_stream(r_default)
    rep_default = next(p for n, p in e_default if n == "done")["compact_report"]
    assert rep_default["compacted"] == 0

    # Reset history (the no-op call still appended user/assistant turns).
    sessions.replace_history(
        sess.id,
        [ChatMessage(role=Role.TOOL, content=medium, tool_call_id="t1", name="x")],
    )

    # Aggressive shrinks it.
    r_agg = await ac.post(
        "/chat/stream",
        json={"session_id": sess.id, "message": "/compact aggressive"},
    )
    e_agg = await _read_stream(r_agg)
    rep_agg = next(p for n, p in e_agg if n == "done")["compact_report"]
    assert rep_agg["compacted"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Registry-driven commands: /clear, /title, /usage, /help
# ─────────────────────────────────────────────────────────────────────────────

def test_is_slash_command_recognises_all_registered() -> None:
    from nexus.server.routes.chat_slash import SLASH_COMMANDS
    for c in SLASH_COMMANDS:
        assert is_slash_command(f"/{c.name}") == c.name
        assert is_slash_command(f"/{c.name} extra args") == c.name


def test_get_commands_endpoint_returns_registry() -> None:
    """The picker fetches /commands once on mount; the response shape must be
    stable so client-side code can lean on it without a runtime guard."""
    import asyncio
    from nexus.server.routes.chat_slash import SLASH_COMMANDS, list_commands

    out = asyncio.get_event_loop().run_until_complete(list_commands())
    assert isinstance(out, list)
    assert len(out) == len(SLASH_COMMANDS)
    names = [c["name"] for c in out]
    assert "compact" in names and "help" in names
    for entry in out:
        assert set(entry.keys()) == {"name", "description", "args_hint"}


async def test_clear_slash_wipes_history(client) -> None:
    ac, sessions = client
    sess = sessions.create()
    sessions.replace_history(
        sess.id,
        [
            ChatMessage(role=Role.USER, content="oi"),
            ChatMessage(role=Role.ASSISTANT, content="oi"),
            ChatMessage(role=Role.USER, content="continue"),
            ChatMessage(role=Role.ASSISTANT, content="ok"),
        ],
    )
    res = await ac.post(
        "/chat/stream", json={"session_id": sess.id, "message": "/clear"}
    )
    events = await _read_stream(res)
    done = next(p for n, p in events if n == "done")
    assert done["cleared_messages"] == 4

    sess2 = sessions.get(sess.id)
    assert sess2 is not None
    # Only the /clear command + confirmation should remain.
    assert len(sess2.history) == 2
    assert sess2.history[0].role == Role.USER
    assert sess2.history[0].content == "/clear"
    assert sess2.history[1].role == Role.ASSISTANT
    assert "wiped 4" in (sess2.history[1].content or "")


async def test_title_slash_renames_session(client) -> None:
    ac, sessions = client
    sess = sessions.create()
    res = await ac.post(
        "/chat/stream",
        json={"session_id": sess.id, "message": "/title Pesquisa de IA — abril"},
    )
    events = await _read_stream(res)
    done = next(p for n, p in events if n == "done")
    assert done["new_title"] == "Pesquisa de IA — abril"

    sess2 = sessions.get(sess.id)
    assert sess2 is not None
    assert sess2.title == "Pesquisa de IA — abril"


async def test_title_slash_with_no_args_shows_current(client) -> None:
    ac, sessions = client
    sess = sessions.create()
    sessions.rename(sess.id, "Existing title")
    res = await ac.post(
        "/chat/stream", json={"session_id": sess.id, "message": "/title"}
    )
    events = await _read_stream(res)
    done = next(p for n, p in events if n == "done")
    assert done["new_title"] is None
    assert "Existing title" in done["reply"]
    # Title was NOT changed by an empty arg.
    assert sessions.get(sess.id).title == "Existing title"


async def test_usage_slash_streams_session_stats(client) -> None:
    ac, sessions = client
    sess = sessions.create()
    sessions.bump_usage(
        sess.id, model="zai/glm-5.1", input_tokens=12345, output_tokens=678, tool_calls=9
    )
    res = await ac.post(
        "/chat/stream", json={"session_id": sess.id, "message": "/usage"}
    )
    events = await _read_stream(res)
    done = next(p for n, p in events if n == "done")
    body = done["reply"]
    assert "zai/glm-5.1" in body
    # Numeric values appear in the markdown table (formatted with _ separators)
    assert "12_345" in body
    assert "678" in body
    assert "9" in body


async def test_help_slash_lists_all_commands(client) -> None:
    from nexus.server.routes.chat_slash import SLASH_COMMANDS
    ac, sessions = client
    sess = sessions.create()
    res = await ac.post(
        "/chat/stream", json={"session_id": sess.id, "message": "/help"}
    )
    events = await _read_stream(res)
    done = next(p for n, p in events if n == "done")
    body = done["reply"]
    # Every registered command should appear in the help output.
    for c in SLASH_COMMANDS:
        assert f"/{c.name}" in body, f"missing {c.name} in /help output"
