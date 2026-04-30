"""HTTP-level test for GET /catalog/providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
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
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(agent=agent, registry=registry, sessions=sessions, settings_store=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_catalog_endpoint_returns_entries(client: httpx.AsyncClient) -> None:
    res = await client.get("/catalog/providers")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) >= 20
    ids = {entry["id"] for entry in body}
    for known in ("openai", "anthropic", "ollama", "groq", "openrouter"):
        assert known in ids


async def test_catalog_entry_shape(client: httpx.AsyncClient) -> None:
    res = await client.get("/catalog/providers")
    body = res.json()
    openai = next(e for e in body if e["id"] == "openai")
    assert openai["display_name"] == "OpenAI"
    assert openai["runtime_kind"] == "openai_compat"
    # Locate the API-key method by id rather than position — the catalog
    # ordering shifts as new auth methods (local_codex, OAuth flavors)
    # land in the same entry.
    api_method = next(m for m in openai["auth_methods"] if m["id"] == "api")
    prompt = api_method["prompts"][0]
    assert prompt["secret"] is True
    assert prompt["kind"] == "password"


async def test_catalog_includes_oauth_and_iam_methods(client: httpx.AsyncClient) -> None:
    res = await client.get("/catalog/providers")
    body = res.json()
    by_id = {e["id"]: e for e in body}
    # Anthropic has both OAuth (Pro/Max) and API key auth methods.
    anthropic_methods = {m["id"] for m in by_id["anthropic"]["auth_methods"]}
    assert "oauth_device" in anthropic_methods
    assert "api" in anthropic_methods
    # Bedrock advertises iam_aws and declares its optional extra.
    bedrock = by_id["bedrock"]
    assert any(m["id"] == "iam_aws" for m in bedrock["auth_methods"])
    iam = next(m for m in bedrock["auth_methods"] if m["id"] == "iam_aws")
    assert iam["requires_extra"] == "bedrock"
