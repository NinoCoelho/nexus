"""HTTP-level tests for /site-credentials routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, StopReason, ToolSpec
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
        max_tokens: int | None = None,
    ) -> ChatResponse:
        return ChatResponse(content="", stop_reason=StopReason.STOP)


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    from nexus import site_credentials as _sc

    monkeypatch.setattr(_sc, "SITE_CREDS_PATH", tmp_path / "site_credentials.db")
    monkeypatch.setattr(_sc, "SITE_CREDS_KEY_PATH", tmp_path / "keys" / "site_credentials.key")

    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(agent=agent, registry=registry, sessions=sessions, settings_store=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_put_list_exists_delete_roundtrip(client: httpx.AsyncClient) -> None:
    res = await client.put(
        "/site-credentials/www.example.com",
        json={"username": "alice", "password": "hunter2"},
    )
    assert res.status_code == 204

    res = await client.get("/site-credentials")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["site"] == "example.com"  # normalized
    assert items[0]["username"] == "alice"
    assert "password" not in items[0]

    res = await client.get("/site-credentials/example.com/exists")
    assert res.status_code == 200
    assert res.json() == {"exists": True}

    res = await client.delete("/site-credentials/example.com")
    assert res.status_code == 204

    res = await client.get("/site-credentials/example.com/exists")
    assert res.json() == {"exists": False}


async def test_put_rejects_empty_fields(client: httpx.AsyncClient) -> None:
    res = await client.put(
        "/site-credentials/example.com",
        json={"username": "", "password": ""},
    )
    assert res.status_code == 422
