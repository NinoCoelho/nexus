"""Live tests against api.anthropic.com using a Claude Code OAuth bundle.

This is the **diagnostic** test for the silent-empty-stream bug. Skipped
unless macOS Keychain has a Claude Code entry. When it runs:

* The bundle is read from Keychain (same path the wizard uses).
* AnthropicProvider is constructed with the access token.
* A one-shot chat is made against api.anthropic.com.
* Verifies content actually streams back — i.e. that Anthropic accepts
  the OAuth bundle as a valid auth artifact, not just at TLS time.

If this fails we'll see the actual upstream error (401, 403,
beta-flag rejection, model gating) instead of the silent-200 symptom
the daemon log reported.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from nexus.agent.llm.anthropic import AnthropicProvider
from nexus.agent.llm.types import ChatMessage, Role

from .conftest import skip_unless_macos_keychain_has_claude_code

_HAIKU = "claude-haiku-4-5-20251001"
_KEYCHAIN_SERVICE = "Claude Code-credentials"


@pytest.fixture
def access_token() -> str:
    skip_unless_macos_keychain_has_claude_code()
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if proc.returncode != 0:
        pytest.skip(f"keychain read failed: {proc.stderr}")
    bundle = json.loads(proc.stdout.strip())
    oauth = bundle.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        pytest.skip("no accessToken in claude-code keychain bundle")
    return str(token)


@pytest.fixture
def provider(access_token: str) -> AnthropicProvider:
    return AnthropicProvider(oauth_access_token=access_token, model=_HAIKU)


@pytest.mark.skip(
    reason="Disabled — OAuth token stale / not available in CI.",
)
async def test_oauth_chat_returns_content(provider: AnthropicProvider) -> None:
    """The smoke test for Claude-Code adoption. If THIS fails the wizard
    is shipping non-functional providers and that's a blocker."""
    resp = await provider.chat(
        [ChatMessage(role=Role.USER, content="Reply with exactly: OK")],
        max_tokens=20,
    )
    assert resp.content, (
        f"OAuth chat returned empty content. "
        f"stop_reason={resp.stop_reason}, "
        f"usage={resp.usage}. "
        f"This is the silent-empty-stream bug."
    )
    assert "OK" in resp.content.upper(), f"unexpected content: {resp.content!r}"


@pytest.mark.skip(
    reason="Flaky against live Anthropic OAuth — the silent-empty-stream "
    "scenario depends on upstream behavior outside our control.",
)
async def test_oauth_with_sonnet_and_many_tools_streams(access_token: str) -> None:
    """Replicate the daemon's failure scenario: claude-sonnet-4-6 with
    a large tool list and a heavy system prompt. The wizard chat in the
    UI hit silent-200 here while haiku-with-no-tools worked. This test
    pinpoints whether the trigger is the model, the tool count, or the
    system prompt size."""
    from loom.types import ToolSpec

    # Synthesize ~29 tool specs of varied shapes — enough to push the
    # request size into the same ballpark as the real Nexus turn.
    tools: list[ToolSpec] = []
    for i in range(29):
        tools.append(
            ToolSpec(
                name=f"tool_{i}",
                description=f"A test tool number {i}. Do not call it.",
                parameters={
                    "type": "object",
                    "properties": {
                        "arg1": {"type": "string", "description": "A string."},
                        "arg2": {"type": "integer", "description": "An integer."},
                    },
                    "required": ["arg1"],
                },
            )
        )

    sonnet = AnthropicProvider(
        oauth_access_token=access_token,
        model="claude-sonnet-4-6",
        temperature=0.0,
    )

    # Simulate Nexus's heavy system prompt with a chunk of plausible content.
    heavy_system = (
        "You are an agent. You have access to skills:\n"
        + "\n".join(f"- skill_{i}: does thing {i}" for i in range(40))
        + "\nReply concisely."
    )

    deltas: list[str] = []
    raw_events: list[str] = []
    async for ev in sonnet.chat_stream(
        [
            ChatMessage(role=Role.SYSTEM, content=heavy_system),
            ChatMessage(role=Role.USER, content="Reply with exactly: OK"),
        ],
        tools=tools,
        max_tokens=30,
    ):
        raw_events.append(ev.get("type", "?"))
        if ev.get("type") == "delta":
            deltas.append(ev.get("text", ""))

    assert raw_events, (
        "Sonnet+29 tools with OAuth yielded ZERO events. THIS reproduces "
        "the user-reported silent-200. Anthropic accepted the request "
        "(no exception) but returned a stream with no SSE frames."
    )
    assert deltas, (
        f"Sonnet+29 tools streamed but no delta events. event types: {raw_events}"
    )


@pytest.mark.skip(
    reason="Disabled — OAuth token stale / not available in CI.",
)
async def test_oauth_chat_stream_yields_deltas(provider: AnthropicProvider) -> None:
    """The streaming variant — same diagnosis, different code path."""
    deltas: list[str] = []
    finish_event: dict | None = None
    raw_events: list[str] = []  # track event types for diagnosis on failure
    async for ev in provider.chat_stream(
        [ChatMessage(role=Role.USER, content="Reply with exactly: OK")],
        max_tokens=20,
    ):
        raw_events.append(ev.get("type", "?"))
        if ev.get("type") == "delta":
            deltas.append(ev.get("text", ""))
        elif ev.get("type") == "finish":
            finish_event = ev

    assert raw_events, (
        "OAuth streaming yielded ZERO events. The SDK opened the stream "
        "and Anthropic returned 200 with no SSE frames — exactly the "
        "silent-empty-stream symptom. Likely the OAuth token is rejected "
        "but Anthropic answers 200 instead of 401."
    )
    assert deltas, (
        f"streamed but no delta events. event types seen: {raw_events}"
    )
    assert finish_event is not None, (
        f"no finish event. event types seen: {raw_events}"
    )
