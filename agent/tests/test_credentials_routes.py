"""HTTP-level tests for /credentials routes."""

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
    from nexus import secrets as _s

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")

    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(agent=agent, registry=registry, sessions=sessions, settings_store=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_put_then_list_then_exists_then_delete(client: httpx.AsyncClient) -> None:
    res = await client.put(
        "/credentials/MY_TEST_KEY",
        json={"value": "sk-abcdefghijklmnop", "kind": "skill", "skill": "demo"},
    )
    assert res.status_code == 204

    res = await client.get("/credentials")
    assert res.status_code == 200
    items = {e["name"]: e for e in res.json()}
    assert "MY_TEST_KEY" in items
    assert items["MY_TEST_KEY"]["kind"] == "skill"
    assert items["MY_TEST_KEY"]["skill"] == "demo"
    assert items["MY_TEST_KEY"]["masked"] == "sk-…mnop"
    assert "sk-abcdefghijklmnop" not in res.text  # raw value never on the wire

    res = await client.get("/credentials/MY_TEST_KEY/exists")
    assert res.json() == {"exists": True}

    res = await client.delete("/credentials/MY_TEST_KEY")
    assert res.status_code == 204

    res = await client.get("/credentials/MY_TEST_KEY/exists")
    assert res.json() == {"exists": False}


async def test_put_rejects_bad_name(client: httpx.AsyncClient) -> None:
    res = await client.put("/credentials/lower-case", json={"value": "x"})
    assert res.status_code == 422


async def test_put_rejects_empty_value(client: httpx.AsyncClient) -> None:
    res = await client.put("/credentials/GOOD_NAME", json={"value": ""})
    assert res.status_code == 422


async def test_put_rejects_unknown_kind(client: httpx.AsyncClient) -> None:
    res = await client.put(
        "/credentials/GOOD_NAME", json={"value": "x", "kind": "weird"}
    )
    assert res.status_code == 422


async def test_exists_consults_env(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FROM_ENV_ONLY", "value-from-env")
    res = await client.get("/credentials/FROM_ENV_ONLY/exists")
    assert res.json() == {"exists": True}
    # ...but it does not show up in the listing — env-only is not Nexus-managed
    res = await client.get("/credentials")
    assert all(e["name"] != "FROM_ENV_ONLY" for e in res.json())
