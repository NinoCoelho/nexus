"""Tests for the Nexus account integration.

Three layers covered:
  * ``auth/nexus_account.py`` — HTTP exchange + secret persistence
    (mocks the website with httpx MockTransport).
  * ``auth/status_watcher.py`` — model + default-model reconciliation on
    tier change. Wired against a fake registry rebuilder so we can assert
    the side effects without spinning up an actual provider.
  * ``server/routes/nexus_account.py`` — loopback gating + happy path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, StopReason, ToolSpec
from nexus.agent.loop import Agent
from nexus.config_schema import (
    AgentConfig,
    ModelEntry,
    NexusAccountConfig,
    NexusConfig,
    ProviderConfig,
)
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


@pytest.fixture
def isolated_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect both secrets.toml and account.json into the test's tmp dir."""
    from nexus import secrets as _s
    from nexus.auth import nexus_account as _na

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")
    monkeypatch.setattr(_na, "ACCOUNT_PATH", tmp_path / "account.json")
    return tmp_path


# ── 1. nexus_account.py ───────────────────────────────────────────────


def _verify_handler_factory(
    requests: list[dict[str, Any]],
    *,
    confirm_status: int = 200,
):
    """Build an httpx.MockTransport handler for the auth/verify + keys/confirm pair.

    Records each request into ``requests`` so callers can assert both
    endpoints were hit. ``confirm_status`` lets a test simulate a
    transient failure on the confirm step.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode()) if request.content else {}
        requests.append({"url": str(request.url), "path": path, "body": body})
        if path == "/api/auth/verify":
            return httpx.Response(
                200,
                json={
                    "apiKey": "sk-litellm-test",
                    "user": {
                        "uid": "u-123",
                        "email": "alice@example.com",
                        "displayName": "Alice",
                        "tier": "free",
                        "stripeCustomerId": None,
                        "stripeSubscriptionId": None,
                        "createdAt": "2026-05-01T00:00:00Z",
                    },
                    "isNew": True,
                },
            )
        if path == "/api/keys/confirm":
            if confirm_status == 200:
                return httpx.Response(200, json={"confirmed": True})
            return httpx.Response(confirm_status, json={"error": "boom"})
        return httpx.Response(404, json={"error": "unexpected path"})

    return handler


def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport,
) -> None:
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)


async def test_verify_id_token_stores_key_and_account(
    isolated_secrets: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus.auth import nexus_account
    from nexus import secrets

    requests: list[dict[str, Any]] = []
    _patch_async_client(monkeypatch, httpx.MockTransport(_verify_handler_factory(requests)))

    record = await nexus_account.verify_id_token(
        "fake-id-token", base_url="https://www.nexus-model.us",
    )

    # Both endpoints were called, in the right order, with the same idToken.
    assert [r["path"] for r in requests] == ["/api/auth/verify", "/api/keys/confirm"]
    assert all(r["body"] == {"idToken": "fake-id-token"} for r in requests)
    assert record["email"] == "alice@example.com"
    assert record["tier"] == "free"
    assert record["isNew"] is True
    # apiKey is *never* returned to the caller
    assert "apiKey" not in record
    # Stored in secrets, mirrored in account.json (no apiKey there).
    assert secrets.get(nexus_account.SECRET_NAME) == "sk-litellm-test"
    cached = nexus_account.load_account()
    assert cached is not None
    assert cached["email"] == "alice@example.com"
    assert "apiKey" not in cached


async def test_verify_id_token_swallows_confirm_failure(
    isolated_secrets: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing /api/keys/confirm must NOT invalidate the stored apiKey —
    sign-in still succeeds; the user's website page just shows
    'Not connected' until the next refresh.
    """
    from nexus.auth import nexus_account
    from nexus import secrets

    requests: list[dict[str, Any]] = []
    transport = httpx.MockTransport(
        _verify_handler_factory(requests, confirm_status=503),
    )
    _patch_async_client(monkeypatch, transport)

    record = await nexus_account.verify_id_token(
        "fake-id-token", base_url="https://www.nexus-model.us",
    )

    # Both endpoints were attempted, verify succeeded.
    assert [r["path"] for r in requests] == ["/api/auth/verify", "/api/keys/confirm"]
    assert record["email"] == "alice@example.com"
    # apiKey persists despite the confirm failure.
    assert secrets.get(nexus_account.SECRET_NAME) == "sk-litellm-test"


async def test_verify_id_token_rejects_empty_input(isolated_secrets: Path) -> None:
    from nexus.auth import nexus_account

    with pytest.raises(nexus_account.NexusAccountError) as exc:
        await nexus_account.verify_id_token("", base_url="https://example.test")
    assert exc.value.status == 400


async def test_verify_id_token_maps_401(
    isolated_secrets: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus.auth import nexus_account

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad token"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **kw: real_async_client(*a, **{**kw, "transport": transport}),
    )

    with pytest.raises(nexus_account.NexusAccountError) as exc:
        await nexus_account.verify_id_token("x", base_url="https://example.test")
    assert exc.value.status == 401


async def test_fetch_status_passes_apiKey_in_query(
    isolated_secrets: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus.auth import nexus_account

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "tier": "pro",
                "spend": 12.5,
                "maxBudget": 100,
                "remaining": 87.5,
                "budgetDuration": "30d",
                "models": ["nexus", "demo"],
                "rpmLimit": 100,
                "tpmLimit": 100000,
                "budgetResetAt": "2026-06-01T00:00:00Z",
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **kw: real_async_client(*a, **{**kw, "transport": transport}),
    )

    payload = await nexus_account.fetch_status(
        base_url="https://example.test", api_key="sk-test",
    )
    assert "apiKey=sk-test" in captured["url"]
    assert payload["tier"] == "pro"
    assert payload["models"] == ["nexus", "demo"]


async def test_clear_account_removes_key_and_file(isolated_secrets: Path) -> None:
    from nexus import secrets
    from nexus.auth import nexus_account

    secrets.set(nexus_account.SECRET_NAME, "sk-test", kind="provider")
    nexus_account.save_account({"email": "a@b.com", "tier": "free"})
    assert nexus_account.is_signed_in()
    nexus_account.clear_account()
    assert not nexus_account.is_signed_in()
    assert nexus_account.load_account() is None


# ── 2. status_watcher.py ──────────────────────────────────────────────


def _make_cfg() -> NexusConfig:
    return NexusConfig(
        agent=AgentConfig(default_model="demo"),
        providers={
            "nexus": ProviderConfig(
                base_url="https://gateway.test/v1",
                credential_ref="nexus_api_key",
                runtime_kind="nexus",
            ),
        },
        models=[
            ModelEntry(id="demo", provider="nexus", model_name="demo", tier="balanced"),
        ],
        nexus_account=NexusAccountConfig(
            base_url="https://app.nexus.test",
            gateway_url="https://gateway.test/v1",
            poll_seconds=60,
            auto_upgrade_default=True,
        ),
    )


def test_apply_status_upgrade_demo_to_nexus() -> None:
    """Free → pro: demo entry is replaced by nexus (single-model policy)."""
    from nexus.auth.status_watcher import StatusWatcher

    cfg = _make_cfg()
    saves: list[NexusConfig] = []
    rebuilds: list[tuple[NexusConfig, dict, Any]] = []

    watcher = StatusWatcher(
        mutable_state={"cfg": cfg, "prov_reg": None},
        agent=object(),
        sessions=None,
        rebuild_registry=lambda c, s, a: rebuilds.append((c, s, a)),
        save_config=lambda c: saves.append(c),
    )

    watcher._apply_status({"models": ["nexus", "demo"], "tier": "pro"})

    assert cfg.agent.default_model == "nexus"
    # Single-model policy: only the canonical (nexus for pro) is registered.
    model_ids = {m.id for m in cfg.models}
    assert model_ids == {"nexus"}
    assert len(saves) == 1
    assert len(rebuilds) == 1


def test_apply_status_downgrade_pro_to_free() -> None:
    """Pro → free: nexus is replaced by demo and default flips down."""
    from nexus.auth.status_watcher import StatusWatcher

    cfg = _make_cfg()
    cfg.agent.default_model = "nexus"
    cfg.models = [
        ModelEntry(id="nexus", provider="nexus", model_name="nexus", tier="heavy"),
    ]

    watcher = StatusWatcher(
        mutable_state={"cfg": cfg, "prov_reg": None},
        agent=object(),
        sessions=None,
        rebuild_registry=lambda c, s, a: None,
        save_config=lambda c: None,
    )
    # Prime "previous" state to pro so a transition fires.
    watcher._last_models = ("nexus", "demo")
    watcher._apply_status({"models": ["demo"], "tier": "free"})

    assert cfg.agent.default_model == "demo"
    model_ids = {m.id for m in cfg.models}
    assert model_ids == {"demo"}


def test_apply_status_revoked_drops_all_nexus_models() -> None:
    """API returns models=[] (revoked / unreachable): drop both demo and nexus."""
    from nexus.auth.status_watcher import StatusWatcher

    cfg = _make_cfg()
    cfg.agent.default_model = "demo"
    cfg.models = [
        ModelEntry(id="demo", provider="nexus", model_name="demo", tier="balanced"),
    ]

    watcher = StatusWatcher(
        mutable_state={"cfg": cfg, "prov_reg": None},
        agent=object(),
        sessions=None,
        rebuild_registry=lambda c, s, a: None,
        save_config=lambda c: None,
    )
    watcher._last_models = ("demo",)
    watcher._apply_status({"models": [], "tier": "free"})

    model_ids = {m.id for m in cfg.models if m.provider == "nexus"}
    assert model_ids == set()


def test_apply_status_preserves_byo_default() -> None:
    """A BYO default ('openai/gpt-4o') is left alone even if the watcher fires."""
    from nexus.auth.status_watcher import StatusWatcher

    cfg = _make_cfg()
    cfg.agent.default_model = "openai/gpt-4o"
    cfg.providers["openai"] = ProviderConfig(
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        type="openai_compat",
    )
    cfg.models.append(
        ModelEntry(
            id="openai/gpt-4o", provider="openai",
            model_name="gpt-4o", tier="balanced",
        ),
    )

    watcher = StatusWatcher(
        mutable_state={"cfg": cfg, "prov_reg": None},
        agent=object(),
        sessions=None,
        rebuild_registry=lambda *a, **k: None,
        save_config=lambda c: None,
    )
    watcher._apply_status({"models": ["nexus", "demo"], "tier": "pro"})

    # BYO default untouched even though pro just unlocked nexus.
    assert cfg.agent.default_model == "openai/gpt-4o"


# ── 3. routes / loopback gating ───────────────────────────────────────


@pytest_asyncio.fixture
async def client(
    isolated_secrets: Path, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    sessions = SessionStore(db_path=isolated_secrets / "sessions.sqlite")
    settings = SettingsStore(path=isolated_secrets / "settings.json")
    registry = SkillRegistry(isolated_secrets / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    cfg = _make_cfg()
    app = create_app(
        agent=agent, registry=registry, sessions=sessions,
        settings_store=settings, nexus_cfg=cfg,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_status_route_when_signed_out(client: httpx.AsyncClient) -> None:
    res = await client.get("/auth/nexus/status")
    assert res.status_code == 200
    body = res.json()
    assert body["signedIn"] is False
    assert body["email"] == ""


async def test_refresh_route_requires_sign_in(client: httpx.AsyncClient) -> None:
    res = await client.post("/auth/nexus/refresh")
    assert res.status_code == 401


async def test_logout_route_clears_state(
    client: httpx.AsyncClient, isolated_secrets: Path,
) -> None:
    from nexus import secrets
    from nexus.auth import nexus_account

    secrets.set(nexus_account.SECRET_NAME, "sk-x", kind="provider")
    nexus_account.save_account({"email": "u@x.test", "tier": "free"})

    res = await client.post("/auth/nexus/logout")
    assert res.status_code == 200
    assert res.json() == {"signedIn": False}
    assert not nexus_account.is_signed_in()


async def test_loopback_gate_rejects_proxied(client: httpx.AsyncClient) -> None:
    """Proxied requests must be rejected — at the middleware (401) when no
    access token is configured, or by the route's _require_loopback (403)
    when a token would otherwise let them through."""
    res = await client.get(
        "/auth/nexus/status",
        headers={"x-forwarded-for": "203.0.113.1"},
    )
    assert res.status_code in (401, 403)
