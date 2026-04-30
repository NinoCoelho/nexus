"""Tests for POST /vault/dashboard/run-operation.

Covers the happy path (returns session_id, marks hidden, kicks the agent
loop) plus the input validation and 404/422 branches. The agent provider
is a no-op LLM that just returns an empty assistant turn so the helper
finishes deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio  # noqa: F401

import nexus.vault as vault_module
from nexus import vault_dashboard
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


class _NoopProvider(LLMProvider):
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        return ChatResponse(content="ok", stop_reason=StopReason.STOP)


@pytest_asyncio.fixture
async def app_ctx(tmp_path: Path):
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(agent=agent, registry=registry, sessions=sessions, settings_store=settings)
    return app, sessions


@pytest_asyncio.fixture
async def client(app_ctx) -> AsyncIterator[httpx.AsyncClient]:
    app, _ = app_ctx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _seed_chat_operation() -> None:
    vault_dashboard.upsert_operation(
        "shop",
        {
            "id": "op_add_customer",
            "label": "Add customer",
            "kind": "chat",
            "prompt": "Add a new customer named Sample.",
        },
    )


async def test_run_operation_creates_hidden_session(
    client: httpx.AsyncClient, app_ctx
) -> None:
    _seed_chat_operation()
    _, sessions = app_ctx

    resp = await client.post(
        "/vault/dashboard/run-operation",
        json={"folder": "shop", "op_id": "op_add_customer"},
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert body["folder"] == "shop"
    assert body["op_id"] == "op_add_customer"
    sid = body["session_id"]
    assert isinstance(sid, str) and sid

    # Session must exist and be hidden so it doesn't pollute the sidebar.
    sess = sessions.get_or_create(sid)
    assert sess is not None
    visible = [s for s in sessions.list() if s.id == sid]
    assert visible == [], "hidden session leaked into list()"

    # Yield to give the background task a moment so it doesn't tear down
    # mid-publish during fixture cleanup.
    await asyncio.sleep(0.05)


async def test_run_operation_404_when_op_missing(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/vault/dashboard/run-operation",
        json={"folder": "shop", "op_id": "nope"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


async def test_run_operation_422_when_form_kind(client: httpx.AsyncClient) -> None:
    vault_dashboard.upsert_operation(
        "shop",
        {
            "id": "op_form",
            "label": "Quick add",
            "kind": "form",
            "table": "shop/customers.md",
        },
    )
    resp = await client.post(
        "/vault/dashboard/run-operation",
        json={"folder": "shop", "op_id": "op_form"},
    )
    assert resp.status_code == 422
    assert "chat" in resp.json()["detail"].lower()


async def test_run_operation_422_when_required_fields_missing(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/vault/dashboard/run-operation",
        json={"folder": "shop"},
    )
    assert resp.status_code == 422


# ── /vault/dashboard/run-history ─────────────────────────────────────────────


async def test_run_history_empty_for_unknown_folder(client: httpx.AsyncClient) -> None:
    resp = await client.get("/vault/dashboard/run-history", params={"folder": "shop"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"folder": "shop", "runs": []}


async def test_run_history_surfaces_failed_runs_and_groups_by_op(
    client: httpx.AsyncClient, app_ctx,
) -> None:
    """Failed runs come back as `failed`; only the latest run per op is returned."""
    import time

    from nexus.agent.llm import ChatMessage, Role

    _, sessions = app_ctx
    _seed_chat_operation()

    # Two runs of the same op: an older failure, then a newer (also-failure).
    # Both stay in the DB until the UI acknowledges them. Run-history must
    # only surface the newest one per op. SQLite ``CURRENT_TIMESTAMP`` has
    # 1-second resolution, so we sleep > 1s between the two creates to make
    # ``updated_at`` ordering deterministic — production runs are spaced by
    # seconds of agent loop work, so this is realistic.
    older = sessions.create(context="Dashboard op: shop#op_add_customer")
    sessions.mark_hidden(older.id)
    sessions.replace_history(
        older.id,
        [
            ChatMessage(role=Role.USER, content="seed"),
            ChatMessage(role=Role.ASSISTANT, content="[crashed] boom"),
        ],
    )
    time.sleep(1.05)
    newer = sessions.create(context="Dashboard op: shop#op_add_customer")
    sessions.mark_hidden(newer.id)
    sessions.replace_history(
        newer.id,
        [
            ChatMessage(role=Role.USER, content="seed"),
            ChatMessage(role=Role.ASSISTANT, content="[llm_error] still broken"),
        ],
    )

    resp = await client.get("/vault/dashboard/run-history", params={"folder": "shop"})
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["op_id"] == "op_add_customer"
    assert runs[0]["session_id"] == newer.id
    assert runs[0]["status"] == "failed"
    assert "still broken" in (runs[0]["error"] or "")


async def test_run_history_distinguishes_done_from_failed(
    client: httpx.AsyncClient, app_ctx,
) -> None:
    from nexus.agent.llm import ChatMessage, Role

    _, sessions = app_ctx

    ok_run = sessions.create(context="Dashboard op: shop#op_a")
    sessions.mark_hidden(ok_run.id)
    sessions.replace_history(
        ok_run.id,
        [
            ChatMessage(role=Role.USER, content="seed"),
            ChatMessage(role=Role.ASSISTANT, content="Created customer."),
        ],
    )
    bad_run = sessions.create(context="Dashboard op: shop#op_b")
    sessions.mark_hidden(bad_run.id)
    sessions.replace_history(
        bad_run.id,
        [
            ChatMessage(role=Role.USER, content="seed"),
            ChatMessage(role=Role.ASSISTANT, content="[interrupted] user cancelled"),
        ],
    )

    resp = await client.get("/vault/dashboard/run-history", params={"folder": "shop"})
    by_op = {r["op_id"]: r for r in resp.json()["runs"]}
    assert by_op["op_a"]["status"] == "done"
    assert by_op["op_a"]["error"] is None
    assert by_op["op_b"]["status"] == "failed"


async def test_run_history_ignores_other_folders_and_visible_sessions(
    client: httpx.AsyncClient, app_ctx,
) -> None:
    from nexus.agent.llm import ChatMessage, Role

    _, sessions = app_ctx

    # Different folder — must not leak.
    other = sessions.create(context="Dashboard op: warehouse#op_x")
    sessions.mark_hidden(other.id)
    sessions.replace_history(
        other.id,
        [
            ChatMessage(role=Role.USER, content="seed"),
            ChatMessage(role=Role.ASSISTANT, content="ok"),
        ],
    )
    # Visible (non-hidden) session — even if context matches, it must not show.
    visible = sessions.create(context="Dashboard op: shop#op_visible")
    sessions.replace_history(
        visible.id,
        [
            ChatMessage(role=Role.USER, content="seed"),
            ChatMessage(role=Role.ASSISTANT, content="[crashed] should not appear"),
        ],
    )

    resp = await client.get("/vault/dashboard/run-history", params={"folder": "shop"})
    assert resp.json()["runs"] == []


async def test_run_history_prefix_match_does_not_eat_neighbouring_folder(
    client: httpx.AsyncClient, app_ctx,
) -> None:
    """``LIKE 'Dashboard op: shop#%'`` must not match ``shop2#…`` etc."""
    from nexus.agent.llm import ChatMessage, Role

    _, sessions = app_ctx
    sibling = sessions.create(context="Dashboard op: shop2#op_x")
    sessions.mark_hidden(sibling.id)
    sessions.replace_history(
        sibling.id,
        [
            ChatMessage(role=Role.USER, content="seed"),
            ChatMessage(role=Role.ASSISTANT, content="[crashed] sibling"),
        ],
    )

    resp = await client.get("/vault/dashboard/run-history", params={"folder": "shop"})
    assert resp.json()["runs"] == []
