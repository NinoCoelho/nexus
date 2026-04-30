"""Live tests against AWS Bedrock.

Auto-skips when boto3 isn't installed OR when no credentials are
available. Set ``NEXUS_LIVE_BEDROCK_PROFILE`` (named profile) and
optionally ``NEXUS_LIVE_BEDROCK_REGION`` to enable.

The default model is the cheapest Claude Haiku variant on Bedrock —
``anthropic.claude-3-5-haiku-20241022-v1:0``. Override via
``NEXUS_LIVE_BEDROCK_MODEL`` if your account has a different one.
"""

from __future__ import annotations

import os

import pytest

# boto3 may not be installed (it's an optional extra)
boto3 = pytest.importorskip("boto3", reason="bedrock extra not installed")

from nexus.agent.llm.bedrock import BedrockProvider
from nexus.agent.llm.types import ChatMessage, Role

from .conftest import echo_tool_spec


_DEFAULT_HAIKU = "anthropic.claude-3-5-haiku-20241022-v1:0"


def _resolve_profile() -> str:
    p = os.environ.get("NEXUS_LIVE_BEDROCK_PROFILE", "").strip()
    if not p:
        pytest.skip("NEXUS_LIVE_BEDROCK_PROFILE not set")
    return p


@pytest.fixture
def provider() -> BedrockProvider:
    profile = _resolve_profile()
    region = os.environ.get("NEXUS_LIVE_BEDROCK_REGION", "us-east-1")
    return BedrockProvider(
        region=region,
        profile=profile,
        model=os.environ.get("NEXUS_LIVE_BEDROCK_MODEL", _DEFAULT_HAIKU),
    )


async def test_authentication(provider: BedrockProvider) -> None:
    """Verifies the AWS profile resolves AND has bedrock:InvokeModel
    permission against the configured model + region."""
    resp = await provider.chat(
        [ChatMessage(role=Role.USER, content="Reply with exactly: OK")],
        max_tokens=20,
    )
    assert resp.content, f"empty content: stop_reason={resp.stop_reason}"
    assert "OK" in resp.content.upper()
    assert resp.usage.input_tokens > 0


async def test_tool_calling_round_trip(provider: BedrockProvider) -> None:
    tools = [echo_tool_spec()]
    resp = await provider.chat(
        [ChatMessage(
            role=Role.USER,
            content="Please call the `echo` tool with text='hello-from-test'.",
        )],
        tools=tools,
        max_tokens=300,
    )
    assert resp.tool_calls, (
        f"model didn't invoke any tool. content={resp.content!r}, "
        f"stop_reason={resp.stop_reason}"
    )
    tc = resp.tool_calls[0]
    assert tc.name == "echo"
    assert isinstance(tc.arguments, dict)
    assert "text" in tc.arguments


async def test_streaming_yields_content_deltas(provider: BedrockProvider) -> None:
    """Bedrock's converse_stream is internally synchronous in boto3;
    our adapter drains it via asyncio.to_thread. Verify the wrapping
    actually produces the loom event shape."""
    deltas: list[str] = []
    finish_seen = False
    async for ev in provider.chat_stream(
        [ChatMessage(role=Role.USER, content="Count from 1 to 3.")],
        max_tokens=50,
    ):
        if ev.get("type") == "delta":
            deltas.append(ev.get("text", ""))
        elif ev.get("type") == "finish":
            finish_seen = True
    assert deltas, "stream yielded no delta events"
    assert finish_seen
