"""HTTP-level test for POST /sessions/{id}/compact."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, Role, StopReason, ToolSpec
from nexus.agent.loop import Agent
from nexus.server.app import create_app
from nexus.server.session_store import SessionStore
from nexus.server.settings import SettingsStore
from nexus.skills.registry import SkillRegistry


class _NoopProvider(LLMProvider):
    async def chat(
        self, messages: list[ChatMessage], *, tools: list[ToolSpec] | None = None, model: str | None = None, max_tokens: int | None = None,
    ) -> ChatResponse:
        return ChatResponse(content="", stop_reason=StopReason.STOP)


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, SessionStore]]:
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(agent=agent, registry=registry, sessions=sessions, settings_store=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, sessions


async def test_compact_endpoint_shrinks_session(client) -> None:
    ac, sessions = client
    sess = sessions.create()

    huge_csv = "col_a,col_b,col_c\n" + "\n".join(f"{i},{i*2},{i*3}" for i in range(20_000))
    huge_payload = json.dumps({"ok": True, "path": "data.csv", "content": huge_csv})

    sessions.replace_history(
        sess.id,
        [
            ChatMessage(role=Role.USER, content="analise os dados"),
            ChatMessage(role=Role.ASSISTANT, content="Vou ler o csv."),
            ChatMessage(role=Role.TOOL, content=huge_payload, tool_call_id="t1", name="vault_read"),
        ],
    )

    res = await ac.post(f"/sessions/{sess.id}/compact")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["compacted"] == 1
    assert body["bytes_after"] < body["bytes_before"] // 100
    assert body["saved_bytes"] > 0

    # History was rewritten in place; the assistant + user messages stayed put
    sess2 = sessions.get(sess.id)
    assert sess2 is not None
    assert sess2.history[0].role == Role.USER
    assert sess2.history[1].role == Role.ASSISTANT
    assert sess2.history[2].role == Role.TOOL
    assert sess2.history[2].tool_call_id == "t1"
    summary = json.loads((sess2.history[2].content or "").split("\n\n[Full result saved to")[0])
    assert summary["nx:compacted"] is True
    assert summary["format"] == "csv"


async def test_compact_endpoint_404_for_missing_session(client) -> None:
    ac, _ = client
    res = await ac.post("/sessions/does-not-exist/compact")
    assert res.status_code == 404


async def test_compact_endpoint_idempotent(client) -> None:
    ac, sessions = client
    sess = sessions.create()
    huge = json.dumps({"ok": True, "content": "x" * 200_000})
    sessions.replace_history(
        sess.id,
        [ChatMessage(role=Role.TOOL, content=huge, tool_call_id="t1", name="vault_read")],
    )
    r1 = (await ac.post(f"/sessions/{sess.id}/compact")).json()
    r2 = (await ac.post(f"/sessions/{sess.id}/compact")).json()
    assert r1["compacted"] == 1
    assert r2["compacted"] == 0
