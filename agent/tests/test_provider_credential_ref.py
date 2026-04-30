"""Provider key resolution honors ``credential_ref`` and the new endpoint
sets / clears the field correctly.

Resolution order (highest priority first):
  1. ``credential_ref`` (new, env-first via secrets.resolve)
  2. ``use_inline_key`` (legacy, file-only via secrets.get)
  3. ``api_key_env`` (legacy, env-only)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, StopReason, ToolSpec
from nexus.agent.loop import Agent
from nexus.agent.registry import _is_provider_functional as is_provider_functional
from nexus.config_schema import ProviderConfig
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


@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexus import secrets as _s

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")


def test_credential_ref_wins_over_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import secrets

    secrets.set("openai", "from-inline", kind="provider")
    secrets.set("OPENAI_KEY", "from-credential", kind="generic")
    monkeypatch.delenv("OPENAI_KEY", raising=False)

    pcfg = ProviderConfig(
        base_url="https://api.example.com/v1",
        api_key_env="",
        use_inline_key=True,  # legacy path is configured…
        credential_ref="OPENAI_KEY",  # …but credential_ref takes priority
        type="openai_compat",
    )
    assert is_provider_functional(pcfg, "openai") is True


def test_credential_ref_resolves_from_env_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus import secrets

    secrets.set("MY_KEY", "from-store")
    monkeypatch.setenv("MY_KEY", "from-env")

    pcfg = ProviderConfig(
        base_url="https://api.example.com/v1",
        credential_ref="MY_KEY",
        type="openai_compat",
    )
    assert is_provider_functional(pcfg, "p") is True


def test_credential_ref_unresolved_means_not_functional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    pcfg = ProviderConfig(
        base_url="https://api.example.com/v1",
        credential_ref="MISSING",
        type="openai_compat",
    )
    # Even if env has a fallback, credential_ref must resolve specifically
    assert is_provider_functional(pcfg, "p") is False


def test_legacy_use_inline_key_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import secrets

    secrets.set("legacy_provider", "from-inline", kind="provider")
    pcfg = ProviderConfig(
        base_url="https://api.example.com/v1",
        use_inline_key=True,
        type="openai_compat",
    )
    assert is_provider_functional(pcfg, "legacy_provider") is True


def test_legacy_api_key_env_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGACY_ENV_KEY", "from-env")
    pcfg = ProviderConfig(
        base_url="https://api.example.com/v1",
        api_key_env="LEGACY_ENV_KEY",
        type="openai_compat",
    )
    assert is_provider_functional(pcfg, "p") is True


# ─── HTTP-level tests for PUT /providers/{name}/credential ────────────────────


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, dict]]:
    from nexus import secrets as _s
    from nexus.config_file import save as save_cfg
    from nexus.config_schema import NexusConfig, default_config

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")
    # Steer the config writer at a temp file so the test doesn't clobber
    # the developer's ~/.nexus/config.toml.
    from nexus import config_file

    monkeypatch.setattr(config_file, "CONFIG_PATH", tmp_path / "config.toml")

    cfg: NexusConfig = default_config()
    cfg.providers["acme"] = ProviderConfig(
        base_url="https://api.acme.example.com/v1",
        api_key_env="ACME_KEY",
        use_inline_key=True,
        type="openai_compat",
    )
    save_cfg(cfg)

    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(
        agent=agent,
        registry=registry,
        sessions=sessions,
        settings_store=settings,
        nexus_cfg=cfg,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, {"cfg": cfg, "tmp_path": tmp_path}


async def test_put_credential_clears_legacy_fields(client) -> None:
    ac, ctx = client
    # Create the credential first
    res = await ac.put(
        "/credentials/ACME_TOKEN", json={"value": "tok-acme-1234567890"}
    )
    assert res.status_code == 204

    # Link the provider to it
    res = await ac.put(
        "/providers/acme/credential", json={"credential_ref": "ACME_TOKEN"}
    )
    assert res.status_code == 204, res.text

    # GET /providers reflects the link and reports key_source: credential
    res = await ac.get("/providers")
    rows = {r["name"]: r for r in res.json()}
    assert rows["acme"]["credential_ref"] == "ACME_TOKEN"
    assert rows["acme"]["key_source"] == "credential"
    # Legacy fields cleared so nothing silently masks future drift
    assert rows["acme"]["key_env"] == ""
    # The on-disk config also shows the cleared legacy fields
    p = ctx["cfg"].providers["acme"]
    assert p.credential_ref == "ACME_TOKEN"
    assert p.use_inline_key is False
    assert p.api_key_env == ""


async def test_put_credential_null_clears_link(client) -> None:
    ac, _ = client
    # Set then null
    await ac.put("/credentials/ACME_TOKEN", json={"value": "x" * 20})
    await ac.put("/providers/acme/credential", json={"credential_ref": "ACME_TOKEN"})
    res = await ac.put("/providers/acme/credential", json={"credential_ref": None})
    assert res.status_code == 204
    res = await ac.get("/providers")
    rows = {r["name"]: r for r in res.json()}
    assert rows["acme"]["credential_ref"] is None


async def test_put_credential_rejects_empty_string(client) -> None:
    ac, _ = client
    res = await ac.put("/providers/acme/credential", json={"credential_ref": ""})
    assert res.status_code == 422


async def test_put_credential_404_for_unknown_provider(client) -> None:
    ac, _ = client
    res = await ac.put(
        "/providers/does-not-exist/credential",
        json={"credential_ref": "ANYTHING"},
    )
    assert res.status_code == 404
