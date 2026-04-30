"""Tests for /auth/local/claude-code/claim.

The macOS Keychain shell-out is fully mocked via
``asyncio.create_subprocess_exec`` patching, so these tests run
identically on every platform.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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


# ── helpers ─────────────────────────────────────────────────────────────────


def _patch_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> None:
    """Replace asyncio.create_subprocess_exec inside local_creds with a
    canned (stdout, stderr, returncode) result."""
    from nexus.server.routes import local_creds as mod

    class _FakeProc:
        def __init__(self) -> None:
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return stdout, stderr

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProc:
        return _FakeProc()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", _fake_exec)


def _patch_platform(monkeypatch: pytest.MonkeyPatch, system: str) -> None:
    from nexus.server.routes import local_creds as mod

    monkeypatch.setattr(mod.platform, "system", lambda: system)


# ── tests ───────────────────────────────────────────────────────────────────


async def test_claim_happy_path_macos(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reads the keychain JSON, extracts claudeAiOauth, and persists it
    as an OAuthBundle under ANTHROPIC_CLAUDE_CODE."""
    _patch_platform(monkeypatch, "Darwin")
    bundle = json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "access-abc-very-long-jwt",
                "refreshToken": "refresh-xyz",
                "expiresAt": 1_900_000_000_000,  # ms
                "scopes": ["org:create_api_key", "user:profile", "user:inference"],
                "subscriptionType": "max",
            },
            "organizationUuid": "org-uuid-test",
        }
    ).encode("utf-8")
    _patch_subprocess(monkeypatch, stdout=bundle)

    res = await client.post("/auth/local/claude-code/claim")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["credential_ref"] == "ANTHROPIC_CLAUDE_CODE"
    assert body["subscription"] == "max"
    assert body["expires_at"] == 1_900_000_000  # ms → s

    # Bundle landed in the secret store.
    creds = (await client.get("/credentials")).json()
    by_name = {c["name"]: c for c in creds}
    assert "ANTHROPIC_CLAUDE_CODE" in by_name
    assert by_name["ANTHROPIC_CLAUDE_CODE"]["kind"] == "oauth"
    # OAuth bundles get a "OAuth · <account>" mask, never the raw token.
    assert "access-abc" not in by_name["ANTHROPIC_CLAUDE_CODE"]["masked"]
    assert "max" in by_name["ANTHROPIC_CLAUDE_CODE"]["masked"]


async def test_claim_returns_404_when_keychain_entry_missing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_platform(monkeypatch, "Darwin")
    _patch_subprocess(
        monkeypatch,
        stderr=b"security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.",
        returncode=44,
    )
    res = await client.post("/auth/local/claude-code/claim")
    assert res.status_code == 404
    assert "claude-code" in res.json()["detail"]


async def test_claim_handles_malformed_json(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Subprocess succeeded but the keychain entry isn't JSON — surface a
    502 with a clear message rather than crashing."""
    _patch_platform(monkeypatch, "Darwin")
    _patch_subprocess(monkeypatch, stdout=b"not-json-content")
    res = await client.post("/auth/local/claude-code/claim")
    assert res.status_code == 502
    assert "JSON" in res.json()["detail"]


async def test_claim_handles_missing_oauth_field(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bundle exists but Claude Code's storage format changed — refuse
    to silently store an empty bundle."""
    _patch_platform(monkeypatch, "Darwin")
    _patch_subprocess(monkeypatch, stdout=b'{"unrelated": "shape"}')
    res = await client.post("/auth/local/claude-code/claim")
    assert res.status_code == 502
    assert "claudeAiOauth" in res.json()["detail"]


async def test_claim_unsupported_platform_returns_501(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_platform(monkeypatch, "Windows")
    res = await client.post("/auth/local/claude-code/claim")
    assert res.status_code == 501
    assert "Windows" in res.json()["detail"]


async def test_claim_rejects_proxy_headers(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tunnel clients (with x-forwarded-for) must be rejected — local
    creds are by definition local. Either the tunnel middleware blocks
    with 401 or the route's own loopback check fires with 403."""
    _patch_platform(monkeypatch, "Darwin")
    res = await client.post(
        "/auth/local/claude-code/claim",
        headers={"x-forwarded-for": "203.0.113.5"},
    )
    assert res.status_code in (401, 403)


# ── Codex ───────────────────────────────────────────────────────────────────


async def test_claim_codex_api_key_mode(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nexus.server.routes import local_creds as mod

    auth_path = tmp_path / "codex-auth.json"
    auth_path.write_text(
        json.dumps({"auth_mode": "ApiKey", "OPENAI_API_KEY": "sk-codex-test-key"})
    )
    monkeypatch.setattr(mod, "_codex_auth_path", lambda: auth_path)

    res = await client.post("/auth/local/codex/claim")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["credential_ref"] == "OPENAI_CODEX_LOCAL"
    assert body["auth_mode"] == "ApiKey"

    # Stored as a plain provider credential (NOT an OAuth bundle).
    creds = (await client.get("/credentials")).json()
    by_name = {c["name"]: c for c in creds}
    assert "OPENAI_CODEX_LOCAL" in by_name
    assert by_name["OPENAI_CODEX_LOCAL"]["kind"] == "provider"
    assert "sk-codex-test-key" not in by_name["OPENAI_CODEX_LOCAL"]["masked"]


async def test_claim_codex_refuses_chatgpt_mode(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ChatGPT-mode tokens are session tokens scoped to /backend-api;
    refusing them up-front beats a confusing 401 on first chat."""
    from nexus.server.routes import local_creds as mod

    auth_path = tmp_path / "codex-auth.json"
    auth_path.write_text(
        json.dumps({"auth_mode": "ChatGPT", "OPENAI_API_KEY": "ey.jwt.token"})
    )
    monkeypatch.setattr(mod, "_codex_auth_path", lambda: auth_path)

    res = await client.post("/auth/local/codex/claim")
    assert res.status_code == 409
    assert "ChatGPT" in res.json()["detail"]


async def test_claim_codex_404_when_file_missing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nexus.server.routes import local_creds as mod

    monkeypatch.setattr(
        mod, "_codex_auth_path", lambda: tmp_path / "does-not-exist.json"
    )
    res = await client.post("/auth/local/codex/claim")
    assert res.status_code == 404


async def test_claim_codex_502_when_auth_mode_missing_key(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nexus.server.routes import local_creds as mod

    auth_path = tmp_path / "codex-auth.json"
    auth_path.write_text(json.dumps({"auth_mode": "ApiKey"}))
    monkeypatch.setattr(mod, "_codex_auth_path", lambda: auth_path)
    res = await client.post("/auth/local/codex/claim")
    assert res.status_code == 502


def test_local_codex_method_in_openai_catalog() -> None:
    from nexus.providers import find as find_catalog_entry, load_catalog

    load_catalog.cache_clear()
    openai = find_catalog_entry("openai")
    assert openai is not None
    assert any(m.id == "local_codex" for m in openai.auth_methods)


def test_local_claude_code_method_in_anthropic_catalog() -> None:
    """The catalog wires the method end-to-end — without this entry the
    wizard tile never appears."""
    from nexus.providers import find as find_catalog_entry, load_catalog

    load_catalog.cache_clear()
    anthropic = find_catalog_entry("anthropic")
    assert anthropic is not None
    method_ids = [m.id for m in anthropic.auth_methods]
    assert "local_claude_code" in method_ids
    # It should sort above oauth_device + api by priority (smaller =
    # earlier) so users see the easiest option first.
    method = next(m for m in anthropic.auth_methods if m.id == "local_claude_code")
    assert method.priority < 50
