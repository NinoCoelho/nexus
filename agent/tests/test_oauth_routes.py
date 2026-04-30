"""Tests for /auth/oauth/* and /auth/callback (PR 4).

The upstream OAuth provider is fully mocked via httpx.MockTransport
patched into ``oauth.httpx.AsyncClient``. Network never leaves the
test process.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, StopReason, ToolSpec
from nexus.agent.loop import Agent
from nexus.providers import find as find_catalog_entry
from nexus.providers import load_catalog
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
    from nexus import config_file as _cfg
    from nexus import secrets as _s

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")
    monkeypatch.setattr(_cfg, "CONFIG_PATH", tmp_path / "config.toml")

    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(agent=agent, registry=registry, sessions=sessions, settings_store=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── helpers ─────────────────────────────────────────────────────────────────


def _ensure_anthropic_oauth_in_catalog() -> None:
    """Sanity-check: catalog ships an oauth_device method for Anthropic.
    The OAuth route uses this entry to look up auth_url / token_url etc."""
    entry = find_catalog_entry("anthropic")
    assert entry is not None
    assert any(m.id == "oauth_device" for m in entry.auth_methods)


def _patch_oauth_httpx(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Replace the httpx.AsyncClient inside routes/oauth.py with a
    MockTransport-backed client driven by ``handler``."""
    from nexus.server.routes import oauth as oauth_mod

    original = oauth_mod.httpx.AsyncClient

    class _MockedAsyncClient(original):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(oauth_mod.httpx, "AsyncClient", _MockedAsyncClient)


# ── tests ───────────────────────────────────────────────────────────────────


def test_catalog_anthropic_oauth_device_present() -> None:
    """Pre-flight: the catalog wiring this PR depends on is in place."""
    load_catalog.cache_clear()
    _ensure_anthropic_oauth_in_catalog()


async def test_oauth_start_rejects_unknown_catalog_id(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/auth/oauth/start",
        json={"catalog_id": "made-up-provider", "auth_method_id": "oauth_device"},
    )
    assert res.status_code == 404


async def test_oauth_start_rejects_non_oauth_method(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/auth/oauth/start",
        json={"catalog_id": "openai", "auth_method_id": "api"},
    )
    assert res.status_code == 422
    assert "not OAuth" in res.json()["detail"]


async def test_oauth_start_rejects_missing_client_id(client: httpx.AsyncClient) -> None:
    """Anthropic ships in the catalog with an empty client_id (we don't
    have a registered OAuth app yet). Starting the flow there must
    surface a clear configuration error rather than silently 502 the
    upstream call."""
    res = await client.post(
        "/auth/oauth/start",
        json={"catalog_id": "anthropic", "auth_method_id": "oauth_device"},
    )
    assert res.status_code == 422
    assert "client" in res.json()["detail"].lower()


async def test_oauth_device_flow_round_trip(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full happy path: start → poll(pending) → poll(ok) → bundle in store."""
    # Patch the catalog's anthropic oauth method to have a client_id so
    # _resolve_method accepts it. We also need to redirect device_url +
    # token_url at our mock.
    from nexus.providers import catalog as catalog_mod

    real_load = catalog_mod.load_catalog
    catalog_mod.load_catalog.cache_clear()

    entries = real_load()
    anthropic = next(e for e in entries if e.id == "anthropic")
    method = next(m for m in anthropic.auth_methods if m.id == "oauth_device")
    assert method.oauth is not None
    method.oauth.client_id = "test-client-id"
    method.oauth.device_url = "https://upstream.test/device"
    method.oauth.token_url = "https://upstream.test/token"

    poll_calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/device":
            return httpx.Response(
                200,
                json={
                    "device_code": "DEVICE-CODE-X",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://upstream.test/login",
                    "interval": 1,
                },
            )
        if req.url.path == "/token":
            poll_calls["n"] += 1
            if poll_calls["n"] == 1:
                return httpx.Response(200, json={"error": "authorization_pending"})
            return httpx.Response(
                200,
                json={
                    "access_token": "access-xyz",
                    "refresh_token": "refresh-xyz",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(404)

    _patch_oauth_httpx(monkeypatch, handler)

    res = await client.post(
        "/auth/oauth/start",
        json={"catalog_id": "anthropic", "auth_method_id": "oauth_device"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    sid = body["session_id"]
    assert body["flow"] == "device"
    assert body["user_code"] == "ABCD-1234"

    # First poll: still pending.
    res = await client.post("/auth/oauth/poll", json={"session_id": sid})
    assert res.json() == {"status": "pending"}

    # Second poll: tokens come back, bundle gets stored.
    res = await client.post("/auth/oauth/poll", json={"session_id": sid})
    body = res.json()
    assert body["status"] == "ok"
    assert body["credential_ref"] == "ANTHROPIC_OAUTH"

    # Bundle is in the secrets store.
    creds = (await client.get("/credentials")).json()
    assert any(c["name"] == "ANTHROPIC_OAUTH" and c["kind"] == "oauth" for c in creds)


async def test_oauth_poll_returns_404_for_unknown_session(
    client: httpx.AsyncClient,
) -> None:
    res = await client.post("/auth/oauth/poll", json={"session_id": "does-not-exist"})
    assert res.status_code == 404


async def test_oauth_callback_rejects_non_loopback(client: httpx.AsyncClient) -> None:
    """The callback must be unreachable from a tunnel — either the
    auth middleware blocks the request (401, no cookie) or the route's
    own loopback check fires (403). Either rejection is correct; the
    point is a malicious tunnel client cannot complete someone else's
    OAuth flow."""
    res = await client.get(
        "/auth/callback?state=x&code=y",
        headers={"x-forwarded-for": "203.0.113.5"},
    )
    assert res.status_code in (401, 403)


async def test_oauth_callback_404s_unknown_state(client: httpx.AsyncClient) -> None:
    """No matching session → 404, not silent success."""
    res = await client.get("/auth/callback?state=nope&code=abc")
    assert res.status_code == 404


async def test_oauth_session_expires_after_ttl(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sessions older than the TTL are GC'd lazily on next access."""
    from nexus.providers import catalog as catalog_mod
    from nexus.server.routes import oauth as oauth_mod

    catalog_mod.load_catalog.cache_clear()
    entries = catalog_mod.load_catalog()
    anthropic = next(e for e in entries if e.id == "anthropic")
    method = next(m for m in anthropic.auth_methods if m.id == "oauth_device")
    assert method.oauth is not None
    method.oauth.client_id = "test-client-id"
    method.oauth.device_url = "https://upstream.test/device"
    method.oauth.token_url = "https://upstream.test/token"

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "device_code": "D",
                "user_code": "ABCD-1234",
                "verification_uri": "https://upstream.test/login",
                "interval": 1,
            },
        )

    _patch_oauth_httpx(monkeypatch, handler)

    res = await client.post(
        "/auth/oauth/start",
        json={"catalog_id": "anthropic", "auth_method_id": "oauth_device"},
    )
    sid = res.json()["session_id"]

    # Forge an expired created_at so the GC sweep on next poll drops it.
    monkeypatch.setattr(oauth_mod, "_SESSION_TTL_SECONDS", 1)
    # Wait long enough that the GC sees it as expired.
    time.sleep(1.2)
    res = await client.post("/auth/oauth/poll", json={"session_id": sid})
    assert res.status_code == 404
