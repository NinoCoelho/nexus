"""Tests for the per-model + global ``max_output_tokens`` setting.

Covers:
- ``OpenAIProvider`` conditionally forwards ``max_tokens`` in the JSON payload.
- ``AnthropicProvider`` swaps the legacy 4096 fallback for the caller-passed value.
- ``LoomProviderAdapter`` resolves per-call max_tokens via the injected callback.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from nexus.agent.llm.openai import OpenAIProvider
from nexus.agent.llm.types import ChatMessage, Role


def _capture_handler(captured: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    return handler


async def _provider_with_transport(transport: httpx.MockTransport) -> OpenAIProvider:
    p = OpenAIProvider(base_url="http://fake/v1", api_key="k", model="m")
    p._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]
    return p


async def test_openai_omits_max_tokens_when_unset() -> None:
    captured: list[dict[str, Any]] = []
    p = await _provider_with_transport(httpx.MockTransport(_capture_handler(captured)))
    await p.chat([ChatMessage(role=Role.USER, content="hi")])
    assert captured, "request did not reach mock transport"
    assert "max_tokens" not in captured[0]


async def test_openai_forwards_max_tokens_when_set() -> None:
    captured: list[dict[str, Any]] = []
    p = await _provider_with_transport(httpx.MockTransport(_capture_handler(captured)))
    await p.chat([ChatMessage(role=Role.USER, content="hi")], max_tokens=8000)
    assert captured[0].get("max_tokens") == 8000


async def test_anthropic_falls_back_to_4096(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic API requires max_tokens — verify 4096 fallback when unset, value forwarded otherwise."""
    from nexus.agent.llm.anthropic import AnthropicProvider

    captured: list[dict[str, Any]] = []

    class FakeMsg:
        def __init__(self) -> None:
            self.content = []
            self.stop_reason = "end_turn"
            self.usage = None

    class FakeMessages:
        async def create(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return FakeMsg()

    class FakeClient:
        def __init__(self, **_: Any) -> None:
            self.messages = FakeMessages()

    p = AnthropicProvider(api_key="k", model="claude-x")
    p._client = FakeClient()  # type: ignore[attr-defined]
    await p.chat([ChatMessage(role=Role.USER, content="hi")])
    await p.chat([ChatMessage(role=Role.USER, content="hi")], max_tokens=12000)
    assert captured[0]["max_tokens"] == 4096
    assert captured[1]["max_tokens"] == 12000


async def test_loom_adapter_resolves_max_tokens_per_call() -> None:
    from nexus.agent._loom_bridge.adapter import LoomProviderAdapter
    from nexus.agent.llm.types import ChatResponse, StopReason, Usage
    import loom.types as lt

    captured: list[dict[str, Any]] = []

    class FakeProvider:
        async def chat(self, messages, *, tools=None, model=None, max_tokens=None) -> ChatResponse:
            captured.append({"model": model, "max_tokens": max_tokens})
            return ChatResponse(
                content="ok",
                tool_calls=[],
                stop_reason=StopReason.STOP,
                usage=Usage(),
            )

    resolved: dict[str, int] = {"fast": 0, "long": 16384}

    adapter = LoomProviderAdapter(
        FakeProvider(),  # type: ignore[arg-type]
        max_tokens_for=lambda m: resolved.get(m or "", 0),
    )
    await adapter.chat([lt.ChatMessage(role=lt.Role.USER, content="hi")], model="fast")
    await adapter.chat([lt.ChatMessage(role=lt.Role.USER, content="hi")], model="long")
    assert captured[0]["max_tokens"] is None  # 0 → None (omit)
    assert captured[1]["max_tokens"] == 16384


def test_modelentry_persists_max_output_tokens(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ModelEntry round-trips max_output_tokens through TOML save/load."""
    from nexus import config_file

    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_file, "CONFIG_PATH", cfg_path)

    cfg = config_file.default_config()
    cfg.models.append(
        config_file.ModelEntry(
            id="x", provider="anthropic", model_name="claude", max_output_tokens=12000,
        )
    )
    cfg.agent.default_max_output_tokens = 8000
    config_file.save(cfg)

    reloaded = config_file.load()
    assert reloaded.agent.default_max_output_tokens == 8000
    assert reloaded.models[0].max_output_tokens == 12000
