"""Read-only share-link endpoints: mint, read, rotate, revoke."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio  # noqa: F401  (asyncio_mode=auto needs the plugin)

from nexus.agent.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    Role,
    StopReason,
    ToolSpec,
)
from nexus.agent.loop import Agent
from nexus.server.app import create_app
from nexus.server.session_store import SessionStore
from nexus.server.settings import SettingsStore
from nexus.skills.registry import SkillRegistry


class _NoopProvider(LLMProvider):
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResponse:
        return ChatResponse(content="", stop_reason=StopReason.STOP)


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[httpx.AsyncClient, SessionStore]]:
    monkeypatch.setattr(
        "nexus.server.routes.share._SECRET_PATH",
        tmp_path / "share_secret",
    )
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(
        agent=agent,
        registry=registry,
        sessions=sessions,
        settings_store=settings,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, sessions


async def _seed_session(sessions: SessionStore) -> str:
    sess = sessions.create()
    sessions.replace_history(
        sess.id,
        [
            ChatMessage(role=Role.USER, content="hi"),
            ChatMessage(role=Role.ASSISTANT, content="hello there"),
        ],
    )
    return sess.id


async def test_share_mint_and_read(client) -> None:
    ac, sessions = client
    sid = await _seed_session(sessions)

    res = await ac.post(f"/sessions/{sid}/share")
    assert res.status_code == 200, res.text
    body = res.json()
    token = body["token"]
    assert token.startswith(f"{sid}.")
    assert body["path"].startswith("#/share/")

    res = await ac.get(f"/share/{token}")
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["title"] in ("New session", "hi")
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["user", "assistant"]


async def test_invalid_signature_rejected(client) -> None:
    ac, sessions = client
    sid = await _seed_session(sessions)
    res = await ac.post(f"/sessions/{sid}/share")
    token = res.json()["token"]
    # Tamper the signature segment.
    tampered = token[:-2] + ("ab" if not token.endswith("ab") else "cd")
    res = await ac.get(f"/share/{tampered}")
    assert res.status_code == 404


async def test_revoke_invalidates_link(client) -> None:
    ac, sessions = client
    sid = await _seed_session(sessions)
    token = (await ac.post(f"/sessions/{sid}/share")).json()["token"]

    res = await ac.delete(f"/sessions/{sid}/share")
    assert res.status_code == 204

    res = await ac.get(f"/share/{token}")
    assert res.status_code == 404


async def test_rotate_changes_token(client) -> None:
    ac, sessions = client
    sid = await _seed_session(sessions)
    t1 = (await ac.post(f"/sessions/{sid}/share")).json()["token"]
    t2 = (await ac.post(f"/sessions/{sid}/share")).json()["token"]
    assert t1 != t2
    # Old token no longer matches the stored nonce.
    assert (await ac.get(f"/share/{t1}")).status_code == 404
    assert (await ac.get(f"/share/{t2}")).status_code == 200
