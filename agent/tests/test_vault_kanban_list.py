"""Tests for vault_kanban.list_boards() and GET /vault/kanban/boards."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio  # noqa: F401

import nexus.vault as vault_module
from nexus import vault_kanban
from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, StopReason, ToolSpec
from nexus.agent.loop import Agent
from nexus.server.app import create_app
from nexus.server.session_store import SessionStore
from nexus.server.settings import SettingsStore
from nexus.skills.registry import SkillRegistry


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


def _seed_kanban(path: str, *, h1: str | None = None) -> None:
    """Write a minimal kanban file. If ``h1`` is None the file has no H1."""
    head = "---\nkanban-plugin: basic\n---\n\n"
    body = f"# {h1}\n\n## Todo\n" if h1 else "## Todo\n"
    vault_module.write_file(path, head + body)


def test_list_boards_filters_to_kanban_only():
    _seed_kanban("nested/dir/Roadmap.md", h1="Roadmap")
    _seed_kanban("flat.md", h1="Flat Board")
    vault_module.write_file("notes/plain.md", "# Just a note\n\nbody\n")
    vault_module.write_file("data.csv", "a,b\n1,2\n")

    boards = vault_kanban.list_boards()

    assert [b["path"] for b in boards] == ["flat.md", "nested/dir/Roadmap.md"]
    assert [b["title"] for b in boards] == ["Flat Board", "Roadmap"]


def test_list_boards_title_falls_back_to_filename_stem():
    # No H1 in body — parser would default to "Kanban"; we want the filename stem instead.
    _seed_kanban("projects/sprint-14.md", h1=None)

    boards = vault_kanban.list_boards()

    assert boards == [{"path": "projects/sprint-14.md", "title": "sprint-14"}]


def test_list_boards_sorted_case_insensitive():
    _seed_kanban("a.md", h1="zebra")
    _seed_kanban("b.md", h1="Apple")
    _seed_kanban("c.md", h1="banana")

    titles = [b["title"] for b in vault_kanban.list_boards()]

    assert titles == ["Apple", "banana", "zebra"]


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


async def test_vault_kanban_boards_route(client: httpx.AsyncClient) -> None:
    _seed_kanban("Roadmap.md", h1="Roadmap")
    _seed_kanban("inbox/triage.md", h1="Triage")

    resp = await client.get("/vault/kanban/boards")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["count"] == 2
    assert payload["boards"] == [
        {"path": "Roadmap.md", "title": "Roadmap"},
        {"path": "inbox/triage.md", "title": "Triage"},
    ]
