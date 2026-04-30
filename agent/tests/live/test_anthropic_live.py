"""Live tests against api.anthropic.com using an API key.

Skipped automatically unless ``NEXUS_LIVE_ANTHROPIC_KEY`` is set.
"""

from __future__ import annotations

import os

import pytest

from nexus.agent.llm.anthropic import AnthropicProvider
from nexus.agent.llm.types import ChatMessage, Role, StopReason

from .conftest import echo_tool_spec, env_or_skip


_HAIKU = "claude-haiku-4-5-20251001"
_OPUS_REASONING = "claude-opus-4-7"


@pytest.fixture
def provider() -> AnthropicProvider:
    api_key = env_or_skip("NEXUS_LIVE_ANTHROPIC_KEY")
    return AnthropicProvider(api_key=api_key, model=_HAIKU)


async def test_authentication(provider: AnthropicProvider) -> None:
    """Minimal chat — proves the key works and the SDK round-trips
    through our message encoder/decoder."""
    resp = await provider.chat(
        [ChatMessage(role=Role.USER, content="Reply with exactly: OK")],
        max_tokens=20,
    )
    assert resp.content, f"empty content: stop_reason={resp.stop_reason}"
    assert "OK" in resp.content.upper()
    assert resp.stop_reason in (StopReason.STOP, StopReason.LENGTH)
    assert resp.usage.input_tokens > 0
    assert resp.usage.output_tokens > 0


async def test_streaming_yields_content_deltas(provider: AnthropicProvider) -> None:
    """chat_stream must yield delta events, not just a single finish."""
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
    assert deltas, "stream yielded no delta events — likely the silent-200 bug"
    assert finish_seen, "stream finished without a 'finish' event"


async def test_tool_calling_round_trip(provider: AnthropicProvider) -> None:
    """Pass a tool, expect the model to invoke it. Verifies our tool
    spec encoder and tool_use response decoder."""
    tools = [echo_tool_spec()]
    resp = await provider.chat(
        [ChatMessage(
            role=Role.USER,
            content="Please call the `echo` tool with text='hello-from-test'.",
        )],
        tools=tools,
        max_tokens=200,
    )
    assert resp.tool_calls, (
        f"model didn't invoke any tool. content={resp.content!r}, "
        f"stop_reason={resp.stop_reason}"
    )
    tc = resp.tool_calls[0]
    assert tc.name == "echo"
    assert isinstance(tc.arguments, dict)
    assert "text" in tc.arguments


async def test_reasoning_model_streams_content(provider: AnthropicProvider) -> None:
    """Verify a reasoning-tagged model (Opus 4.7) actually streams.

    We don't assert anything about extended-thinking format here —
    only that content arrives. Anthropic's reasoning is opt-in via
    the ``thinking`` parameter; without it Opus still streams normally
    and that's enough for our 'reasoning provider plumbing works' bar.
    """
    if os.environ.get("NEXUS_LIVE_SKIP_REASONING") == "1":
        pytest.skip("NEXUS_LIVE_SKIP_REASONING=1")
    deltas: list[str] = []
    async for ev in provider.chat_stream(
        [ChatMessage(role=Role.USER, content="Reply with exactly: OK")],
        model=_OPUS_REASONING,
        max_tokens=30,
    ):
        if ev.get("type") == "delta":
            deltas.append(ev.get("text", ""))
    text = "".join(deltas)
    assert "OK" in text.upper(), f"reasoning model returned no readable content: {text!r}"
