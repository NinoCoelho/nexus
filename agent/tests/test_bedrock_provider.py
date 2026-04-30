"""Bedrock provider + IAM wizard path (PR 5).

The actual boto3 round-trip is mocked — we just verify the request
shape we hand to ``client.converse`` matches Bedrock's Converse API
and that the response decoder lifts content + tool calls correctly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, StopReason, ToolSpec
from nexus.agent.llm.types import Role
from nexus.agent.loop import Agent
from nexus.providers import find as find_catalog_entry, load_catalog
from nexus.server.app import create_app
from nexus.server.session_store import SessionStore
from nexus.server.settings import SettingsStore
from nexus.skills.registry import SkillRegistry

# boto3 is an optional install — skip the live-provider tests when it's
# not present. The wizard / catalog tests still run.
boto3 = pytest.importorskip("boto3", reason="bedrock extra not installed")


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


# ── catalog ─────────────────────────────────────────────────────────────────


def test_bedrock_catalog_entry_declares_iam_aws() -> None:
    load_catalog.cache_clear()
    bedrock = find_catalog_entry("bedrock")
    assert bedrock is not None
    assert bedrock.runtime_kind == "bedrock"
    methods = {m.id: m for m in bedrock.auth_methods}
    assert "iam_aws" in methods
    assert methods["iam_aws"].requires_extra == "bedrock"
    # Catalog prompts collect profile + region + the AWS region choices.
    prompt_names = {p.name for p in methods["iam_aws"].prompts}
    assert "iam_profile" in prompt_names
    assert "iam_region" in prompt_names


# ── wizard validation ─────────────────────────────────────────────────────


async def test_wizard_accepts_iam_aws_with_profile_and_region(
    client: httpx.AsyncClient,
) -> None:
    """The wizard route accepts iam_aws and persists iam_profile +
    iam_region on the ProviderConfig. Runtime startup is gated by
    boto3 install (registry skips with a hint when missing)."""
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "bedrock",
            "catalog_id": "bedrock",
            "auth_method_id": "iam_aws",
            "runtime_kind": "bedrock",
            "base_url": "",
            "credential_ref": None,
            "credentials": {},
            "iam_profile": "default",
            "iam_region": "us-east-1",
            "iam_extra": {},
            "models": ["anthropic.claude-sonnet-4-5-20250929-v1:0"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["auth_kind"] == "iam"
    assert body["runtime_kind"] == "bedrock"

    provs = {p["name"]: p for p in (await client.get("/providers")).json()}
    assert "bedrock" in provs


async def test_wizard_rejects_iam_gcp_until_pr6(client: httpx.AsyncClient) -> None:
    """Vertex / Azure stay placeholder — the wizard must refuse them
    cleanly so the user knows to wait."""
    res = await client.post(
        "/providers/wizard",
        json={
            "name": "vertex-ai",
            "catalog_id": "vertex-ai",
            "auth_method_id": "iam_gcp",
            "runtime_kind": "vertex",
            "base_url": "",
            "credentials": {},
            "iam_profile": "",
            "iam_region": "us-central1",
            "iam_extra": {"project": "test"},
            "models": [],
        },
    )
    assert res.status_code == 422
    assert "later release" in res.json()["detail"]


# ── provider construction ─────────────────────────────────────────────────


def test_bedrock_provider_construction_uses_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify our adapter constructs a boto3 Session with the right
    region + profile and asks for the bedrock-runtime client."""
    from nexus.agent.llm.bedrock import BedrockProvider

    captured: dict[str, Any] = {}

    class _FakeClient:
        pass

    class _FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            captured["session_kwargs"] = kwargs

        def client(self, name: str) -> _FakeClient:
            captured["client_name"] = name
            return _FakeClient()

    monkeypatch.setattr("boto3.Session", _FakeSession)

    p = BedrockProvider(region="us-west-2", profile="dev", model="claude")
    assert captured["client_name"] == "bedrock-runtime"
    assert captured["session_kwargs"]["region_name"] == "us-west-2"
    assert captured["session_kwargs"]["profile_name"] == "dev"
    assert isinstance(p._client, _FakeClient)


async def test_bedrock_chat_translates_request_and_decodes_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end translation: Nexus messages → Converse messages and
    Converse output → Nexus ChatResponse."""
    from nexus.agent.llm.bedrock import BedrockProvider

    class _FakeClient:
        def __init__(self) -> None:
            self.last_kwargs: dict[str, Any] | None = None

        def converse(self, **kwargs: Any) -> dict[str, Any]:
            self.last_kwargs = kwargs
            return {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"text": "hello there"}],
                    }
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 10, "outputTokens": 5},
            }

    class _FakeSession:
        def __init__(self, **_: Any) -> None:
            pass

        def client(self, _name: str) -> _FakeClient:
            return _fake_client

    _fake_client = _FakeClient()
    monkeypatch.setattr("boto3.Session", _FakeSession)

    p = BedrockProvider(region="us-east-1", model="x")
    msgs = [
        ChatMessage(role=Role.SYSTEM, content="be brief"),
        ChatMessage(role=Role.USER, content="hi"),
    ]
    resp = await p.chat(msgs, model="anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert resp.content == "hello there"
    assert resp.stop_reason == StopReason.STOP
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 5

    # Sent with system as a separate field; user msg in messages.
    sent = _fake_client.last_kwargs
    assert sent is not None
    assert sent["modelId"].startswith("anthropic.claude-sonnet-4-5")
    assert sent["system"] == [{"text": "be brief"}]
    assert sent["messages"][0]["role"] == "user"
    assert sent["messages"][0]["content"] == [{"text": "hi"}]


async def test_bedrock_chat_surfaces_clienterror_as_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from botocore.exceptions import ClientError  # type: ignore[import-not-found]

    from nexus.agent.llm.bedrock import BedrockProvider
    from nexus.agent.llm.types import LLMTransportError

    class _FakeClient:
        def converse(self, **_: Any) -> dict[str, Any]:
            raise ClientError(
                {
                    "Error": {
                        "Code": "AccessDeniedException",
                        "Message": "User: arn:... is not authorized",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "Converse",
            )

    class _FakeSession:
        def __init__(self, **_: Any) -> None:
            pass

        def client(self, _name: str) -> _FakeClient:
            return _FakeClient()

    monkeypatch.setattr("boto3.Session", _FakeSession)

    p = BedrockProvider(region="us-east-1", model="x")
    with pytest.raises(LLMTransportError) as exc_info:
        await p.chat(
            [ChatMessage(role=Role.USER, content="hi")],
            model="anthropic.claude-sonnet-4-5-20250929-v1:0",
        )
    assert "AccessDeniedException" in str(exc_info.value)
    assert exc_info.value.status_code == 403
