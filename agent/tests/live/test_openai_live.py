"""Live tests against api.openai.com.

Auto-skips when no API key is available. Auto-discovers a key from any of:

* ``NEXUS_LIVE_OPENAI_KEY`` env var.
* ``~/.codex/auth.json`` when ``auth_mode == "ApiKey"``.

So if you've signed in to ``codex``, the OpenAI live tests run for free.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nexus.agent.llm.auth import StaticBearerAuth
from nexus.agent.llm.openai import OpenAIProvider
from nexus.agent.llm.types import ChatMessage, Role, StopReason

from .conftest import echo_tool_spec


_GPT4O_MINI = "gpt-4o-mini"
_O1_MINI = "o1-mini"


def _resolve_openai_key() -> str:
    """Auto-discover an OpenAI key, in order of preference:

    1. ``NEXUS_LIVE_OPENAI_KEY`` env var.
    2. The Nexus secrets store at ``~/.nexus/secrets.toml`` — when the
       user has configured OpenAI through the wizard, the key lives
       under ``OPENAI_API_KEY`` (the catalog's default credential name).
    3. ``~/.codex/auth.json`` when ``auth_mode == "ApiKey"``.
    """
    direct = os.environ.get("NEXUS_LIVE_OPENAI_KEY", "").strip()
    if direct:
        return direct

    # Nexus secrets store — what the wizard writes when the user runs
    # the OpenAI tile with an API key. We avoid importing nexus.secrets
    # directly so this fixture stays simple; tomllib + the canonical
    # path is enough.
    import tomllib
    nexus_secrets = Path("~/.nexus/secrets.toml").expanduser()
    if nexus_secrets.exists():
        try:
            with open(nexus_secrets, "rb") as f:
                data = tomllib.load(f)
            keys = (data.get("keys") or {})
            for name in ("OPENAI_API_KEY", "OPENAI_LIVE_KEY"):
                v = keys.get(name)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        except (OSError, tomllib.TOMLDecodeError):
            pass

    p = Path("~/.codex/auth.json").expanduser()
    if p.exists():
        try:
            d = json.loads(p.read_text())
            if d.get("auth_mode") == "ApiKey" and d.get("OPENAI_API_KEY"):
                return str(d["OPENAI_API_KEY"])
        except (OSError, json.JSONDecodeError):
            pass
    pytest.skip(
        "no OpenAI key — set NEXUS_LIVE_OPENAI_KEY, configure OpenAI via the "
        "Nexus wizard, or sign in to codex with auth_mode=ApiKey"
    )


@pytest.fixture
def provider() -> OpenAIProvider:
    api_key = _resolve_openai_key()
    return OpenAIProvider(
        base_url="https://api.openai.com/v1",
        auth=StaticBearerAuth(api_key),
        model=_GPT4O_MINI,
    )


async def test_authentication(provider: OpenAIProvider) -> None:
    resp = await provider.chat(
        [ChatMessage(role=Role.USER, content="Reply with exactly: OK")],
        max_tokens=20,
    )
    assert resp.content, f"empty content: stop_reason={resp.stop_reason}"
    assert "OK" in resp.content.upper()
    assert resp.stop_reason in (StopReason.STOP, StopReason.LENGTH)
    assert resp.usage.input_tokens > 0
    assert resp.usage.output_tokens > 0


async def test_streaming_yields_content_deltas(provider: OpenAIProvider) -> None:
    deltas: list[str] = []
    finish_seen = False
    async for ev in provider.chat_stream(
        [ChatMessage(role=Role.USER, content="Count from 1 to 3.")],
        max_tokens=30,
    ):
        if ev.get("type") == "delta":
            deltas.append(ev.get("text", ""))
        elif ev.get("type") == "finish":
            finish_seen = True
    assert deltas, "stream yielded no delta events"
    assert finish_seen


async def test_tool_calling_round_trip(provider: OpenAIProvider) -> None:
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


async def test_gpt4_turbo_with_many_tools_streams(provider: OpenAIProvider) -> None:
    """Replicate the daemon's failure scenario for OpenAI: gpt-4-turbo
    with 29 tools and a heavy message history. Most likely to fail with
    context-overflow, model-not-available, or rate-limit. Diagnostic —
    on failure the assertion message contains the actual upstream error."""
    from loom.types import ToolSpec

    tools: list[ToolSpec] = []
    for i in range(29):
        tools.append(
            ToolSpec(
                name=f"tool_{i}",
                description=f"Test tool number {i}.",
                parameters={
                    "type": "object",
                    "properties": {"arg1": {"type": "string"}},
                    "required": ["arg1"],
                },
            )
        )

    deltas: list[str] = []
    finish_event: dict | None = None
    raw_events: list[str] = []
    async for ev in provider.chat_stream(
        [ChatMessage(role=Role.USER, content="Reply with exactly: OK")],
        model="gpt-4-turbo",
        tools=tools,
        max_tokens=20,
    ):
        raw_events.append(ev.get("type", "?"))
        if ev.get("type") == "delta":
            deltas.append(ev.get("text", ""))
        elif ev.get("type") == "finish":
            finish_event = ev

    assert raw_events, (
        "gpt-4-turbo+29 tools yielded ZERO events. event types: []. "
        "The exception was either swallowed by the SDK or Anthropic-style "
        "200-with-empty applies here too."
    )
    assert deltas, (
        f"streamed but no delta events. types: {raw_events}"
    )
    assert finish_event is not None


@pytest.mark.skip(
    reason="o1-mini retired from public OpenAI API — re-enable with a "
    "currently-available reasoning model when one is selected.",
)
async def test_reasoning_model_returns_content(provider: OpenAIProvider) -> None:
    """o1-mini uses a different path internally — assert it still gets
    content through our adapter."""
    if os.environ.get("NEXUS_LIVE_SKIP_REASONING") == "1":
        pytest.skip("NEXUS_LIVE_SKIP_REASONING=1")
    resp = await provider.chat(
        [ChatMessage(
            role=Role.USER,
            content="Reply with exactly the two characters: OK",
        )],
        model=_O1_MINI,
        # o1 doesn't accept temperature; OpenAIProvider sends it but the
        # API ignores it for o-series. max_tokens still required.
        max_tokens=200,
    )
    assert resp.content, (
        f"reasoning model {_O1_MINI!r} returned no content. "
        f"stop_reason={resp.stop_reason}, usage={resp.usage}"
    )
    # o1 emits chain-of-thought tokens that we count in output_tokens.
    assert resp.usage.output_tokens > 0
