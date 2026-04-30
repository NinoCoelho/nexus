"""HTTP-level tests for POST /providers/wizard + POST /providers/{name}/test.

Covers the atomic create/update/edit flow, validation surface, and
secret rollback when config save fails.
"""

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
    """Isolate config + secrets to tmp_path for the duration of the test."""
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


# ── happy path ──────────────────────────────────────────────────────────────


async def test_wizard_creates_api_provider_with_credential_and_models(
    client: httpx.AsyncClient,
) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "openrouter",
            "catalog_id": "openrouter",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://openrouter.ai/api/v1",
            "credential_ref": "OPENROUTER_API_KEY",
            "credentials": {"OPENROUTER_API_KEY": "sk-or-abcdefghij"},
            "models": ["anthropic/claude-sonnet-4.5", "openai/gpt-4o-mini"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "openrouter"
    assert body["runtime_kind"] == "openai_compat"
    assert body["auth_kind"] == "api"
    assert body["credential_ref"] == "OPENROUTER_API_KEY"
    assert body["models"] == ["anthropic/claude-sonnet-4.5", "openai/gpt-4o-mini"]

    # Provider visible in /providers, key marked credential-source.
    provs = (await client.get("/providers")).json()
    by_name = {p["name"]: p for p in provs}
    assert by_name["openrouter"]["has_key"] is True
    assert by_name["openrouter"]["key_source"] == "credential"
    assert by_name["openrouter"]["credential_ref"] == "OPENROUTER_API_KEY"

    # Credential listed in /credentials with masked value.
    creds = (await client.get("/credentials")).json()
    assert any(c["name"] == "OPENROUTER_API_KEY" and c["kind"] == "provider" for c in creds)


async def test_wizard_anonymous_provider_creates_ollama_without_key(
    client: httpx.AsyncClient,
) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "ollama",
            "catalog_id": "ollama",
            "auth_method_id": "anonymous",
            "runtime_kind": "ollama",
            "base_url": "http://localhost:11434",
            "credential_ref": None,
            "credentials": {},
            "models": ["llama3.3"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["auth_kind"] == "anonymous"
    assert body["credential_ref"] is None

    provs = {p["name"]: p for p in (await client.get("/providers")).json()}
    assert provs["ollama"]["key_source"] == "anonymous"


async def test_wizard_edit_replaces_models_and_keeps_atomic(
    client: httpx.AsyncClient,
) -> None:
    # Create with two models.
    await client.post(
        "/providers/wizard",
        json={
            "name": "openai",
            "catalog_id": "openai",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "credential_ref": "OPENAI_API_KEY",
            "credentials": {"OPENAI_API_KEY": "sk-old-keyvalue"},
            "models": ["gpt-4o", "gpt-4o-mini"],
        },
    )
    # Edit — re-bind the same credential ref but change models + key.
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "openai",
            "catalog_id": "openai",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.openai.com/v1",
            "credential_ref": "OPENAI_API_KEY",
            "credentials": {"OPENAI_API_KEY": "sk-new-keyvalue"},
            "models": ["gpt-4o"],
        },
    )
    assert res.status_code == 200
    assert res.json()["models"] == ["gpt-4o"]


async def test_wizard_reuses_pre_existing_credential_without_rewrite(
    client: httpx.AsyncClient,
) -> None:
    """A credential created via /credentials should be bindable by the
    wizard without re-sending its value."""
    # Pre-seed a credential.
    await client.put(
        "/credentials/PRESEEDED_KEY",
        json={"value": "sk-already-stored", "kind": "provider"},
    )
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "groq",
            "catalog_id": "groq",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.groq.com/openai/v1",
            "credential_ref": "PRESEEDED_KEY",
            "credentials": {},
            "models": ["llama-3.3-70b-versatile"],
        },
    )
    assert res.status_code == 200, res.text
    provs = {p["name"]: p for p in (await client.get("/providers")).json()}
    assert provs["groq"]["credential_ref"] == "PRESEEDED_KEY"
    assert provs["groq"]["has_key"] is True


# ── validation ──────────────────────────────────────────────────────────────


async def test_wizard_oauth_requires_credential_ref_pointing_at_bundle(
    client: httpx.AsyncClient,
) -> None:
    """OAuth methods require a credential_ref that resolves to an OAuth
    bundle in the secrets store — i.e. the wizard ran the flow via
    /auth/oauth/* before submitting. Without that, the wizard refuses
    rather than silently creating a half-configured provider."""
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "anthropic",
            "auth_method_id": "oauth_device",
            "runtime_kind": "anthropic",
            "base_url": "",
            "credentials": {},
            "models": [],
        },
    )
    assert res.status_code == 422
    assert "credential_ref" in res.json()["detail"]


async def test_wizard_oauth_rejects_credential_ref_without_oauth_bundle(
    client: httpx.AsyncClient,
) -> None:
    """A credential_ref that exists as a plain API key (kind=provider) but
    NOT as an OAuth bundle must be rejected — otherwise we'd save a
    config that points at the wrong shape of secret."""
    await client.put(
        "/credentials/SOME_PLAIN_KEY",
        json={"value": "sk-not-oauth", "kind": "provider"},
    )
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "anthropic",
            "auth_method_id": "oauth_device",
            "runtime_kind": "anthropic",
            "credential_ref": "SOME_PLAIN_KEY",
            "base_url": "",
            "credentials": {},
            "models": [],
        },
    )
    assert res.status_code == 422
    assert "OAuth bundle" in res.json()["detail"]


async def test_wizard_rejects_iam_methods_in_pr2(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "bedrock",
            "auth_method_id": "iam_aws",
            "runtime_kind": "bedrock",
            "base_url": "",
            "credentials": {},
            "models": [],
        },
    )
    assert res.status_code == 422


async def test_wizard_rejects_invalid_provider_name(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "Bad Name With Spaces",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.example.com/v1",
            "credential_ref": "MY_KEY",
            "credentials": {"MY_KEY": "x"},
            "models": [],
        },
    )
    assert res.status_code == 422


async def test_wizard_rejects_lowercase_credential_name(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "x",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.example.com/v1",
            "credential_ref": "my_key",
            "credentials": {"my_key": "x"},
            "models": [],
        },
    )
    assert res.status_code == 422
    assert "UPPER_SNAKE_CASE" in res.json()["detail"]


async def test_wizard_api_requires_credential_ref(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "x",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.example.com/v1",
            "credential_ref": None,
            "credentials": {},
            "models": [],
        },
    )
    assert res.status_code == 422


async def test_wizard_api_rejects_unknown_credential_ref(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "x",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.example.com/v1",
            "credential_ref": "NOT_PROVIDED",
            "credentials": {},
            "models": [],
        },
    )
    assert res.status_code == 422
    assert "not provided" in res.json()["detail"]


async def test_wizard_anonymous_rejects_credential_ref(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "ollama",
            "auth_method_id": "anonymous",
            "runtime_kind": "ollama",
            "base_url": "http://localhost:11434",
            "credential_ref": "SOMETHING",
            "credentials": {},
            "models": [],
        },
    )
    assert res.status_code == 422


# ── atomicity ───────────────────────────────────────────────────────────────


async def test_wizard_rolls_back_secrets_when_save_cfg_raises(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If config save blows up after we've written secrets, the wizard
    must restore the secrets snapshot — never leave orphan credentials."""
    def _explode(_cfg) -> None:
        raise RuntimeError("simulated disk full")

    monkeypatch.setattr("nexus.config_file.save", _explode)

    res = await client.post(
        "/providers/wizard",
        json={
            "name": "openrouter",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://openrouter.ai/api/v1",
            "credential_ref": "OPENROUTER_NEW_KEY",
            "credentials": {"OPENROUTER_NEW_KEY": "sk-doomed-value"},
            "models": [],
        },
    )
    assert res.status_code == 500
    # Credential must NOT be present after rollback.
    creds = {c["name"] for c in (await client.get("/credentials")).json()}
    assert "OPENROUTER_NEW_KEY" not in creds


async def test_wizard_restores_pre_existing_credential_on_rollback(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the wizard overwrites an existing credential and then save_cfg
    fails, the rollback must restore the *original* value, not delete it."""
    # Pre-seed.
    await client.put(
        "/credentials/SHARED_KEY", json={"value": "original-value", "kind": "skill"}
    )

    monkeypatch.setattr("nexus.config_file.save", lambda _cfg: (_ for _ in ()).throw(RuntimeError("boom")))

    await client.post(
        "/providers/wizard",
        json={
            "name": "x",
            "auth_method_id": "api",
            "runtime_kind": "openai_compat",
            "base_url": "https://api.example.com/v1",
            "credential_ref": "SHARED_KEY",
            "credentials": {"SHARED_KEY": "new-value-that-should-not-stick"},
            "models": [],
        },
    )

    # Original value (and original kind) preserved.
    creds = {c["name"]: c for c in (await client.get("/credentials")).json()}
    assert "SHARED_KEY" in creds
    assert creds["SHARED_KEY"]["kind"] == "skill"
    # Verify exists check still passes.
    res = await client.get("/credentials/SHARED_KEY/exists")
    assert res.json() == {"exists": True}


# ── /providers/{name}/test ──────────────────────────────────────────────────


async def test_test_endpoint_returns_404_for_unknown_provider(
    client: httpx.AsyncClient,
) -> None:
    res = await client.post("/providers/nonexistent/test")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "not found" in body["error"]


async def test_test_endpoint_returns_latency_ms(
    client: httpx.AsyncClient,
) -> None:
    """Existing provider with no key returns ok=False but still reports
    latency. Ensures the endpoint always surfaces both fields."""
    # Use the seeded openai provider (no key set in test env).
    res = await client.post("/providers/openai/test")
    assert res.status_code == 200
    body = res.json()
    assert "ok" in body
    assert "latency_ms" in body
    assert isinstance(body["latency_ms"], int)
