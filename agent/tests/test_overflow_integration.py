"""End-to-end: pre-flight context_overflow short-circuits the turn before
the LLM is dialed, and post-flight empty_response gets annotated when the
request was already at >70% of the window."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from nexus.agent.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    Role,
    StopReason,
    StreamEvent,
    ToolSpec,
)
from nexus.agent.loop import Agent
from nexus.config_schema import AgentConfig, ModelEntry, NexusConfig, ProviderConfig
from nexus.skills.registry import SkillRegistry


class _SpyProvider(LLMProvider):
    """Records whether chat / chat_stream were called. Used to prove the
    pre-flight check refused the turn before any HTTP traffic."""

    def __init__(self) -> None:
        self.chat_calls = 0
        self.stream_calls = 0

    async def chat(self, messages, *, tools=None, model=None) -> ChatResponse:
        self.chat_calls += 1
        return ChatResponse(content="ignored", stop_reason=StopReason.STOP)

    async def chat_stream(self, messages, *, tools=None, model=None) -> AsyncIterator[StreamEvent]:
        self.stream_calls += 1
        # Echo a tiny content delta + finish so a non-overflowing call still
        # completes a turn cleanly.
        yield {"type": "delta", "text": "ok"}
        yield {
            "type": "finish",
            "finish_reason": "stop",
            "content": "ok",
            "tool_calls": [],
            "usage": {},
        }

    async def aclose(self) -> None:
        pass


def _cfg_with_window(window: int) -> NexusConfig:
    return NexusConfig(
        agent=AgentConfig(default_model="test/model", max_iterations=4),
        providers={"test": ProviderConfig(base_url="http://x", type="openai_compat")},
        models=[
            ModelEntry(
                id="test/model",
                provider="test",
                model_name="model",
                context_window=window,
            )
        ],
    )


@pytest.fixture
def agent_with_window(tmp_path: Path):
    cfg = _cfg_with_window(window=200_000)
    spy = _SpyProvider()
    agent = Agent(
        provider=spy,
        registry=SkillRegistry(tmp_path / "skills"),
        nexus_cfg=cfg,
    )
    return agent, spy


async def test_preflight_aborts_before_llm_when_overflowed(agent_with_window) -> None:
    agent, spy = agent_with_window
    # 1.4 MB of history → ~350K tokens, way above the 200K window.
    blob = "x" * 1_400_000
    history = [ChatMessage(role=Role.USER, content=blob)]

    events = []
    async for ev in agent.run_turn_stream(
        "go",
        history=history,
        context=None,
        session_id="s1",
        model_id="test/model",
    ):
        events.append(ev)

    # The provider must NOT have been called — the whole point of the check.
    assert spy.chat_calls == 0
    assert spy.stream_calls == 0

    error = next((e for e in events if e["type"] == "error"), None)
    assert error is not None
    assert error["reason"] == "context_overflow"
    assert error["retryable"] is False
    assert error["context_window"] == 200_000
    assert error["estimated_input_tokens"] > 200_000
    assert "compact_history" in (error["actions"] or [])

    done = next((e for e in events if e["type"] == "done"), None)
    assert done is not None
    assert done["reply"] == ""
    await agent.aclose()


async def test_preflight_passes_when_history_fits(agent_with_window) -> None:
    agent, spy = agent_with_window
    history = [ChatMessage(role=Role.USER, content="small message")]

    events = []
    async for ev in agent.run_turn_stream(
        "go", history=history, context=None, session_id="s2", model_id="test/model",
    ):
        events.append(ev)

    # Provider was called normally
    assert spy.stream_calls >= 1
    error = next((e for e in events if e["type"] == "error"), None)
    assert error is None
    done = next((e for e in events if e["type"] == "done"), None)
    assert done is not None
    assert done["reply"] == "ok"
    await agent.aclose()


async def test_preflight_skipped_when_window_unconfigured(tmp_path: Path) -> None:
    """A model without ``context_window`` (the default for new entries) must
    not be falsely flagged — the safety net is opt-in."""
    cfg = _cfg_with_window(window=0)  # disabled
    spy = _SpyProvider()
    agent = Agent(
        provider=spy,
        registry=SkillRegistry(tmp_path / "skills"),
        nexus_cfg=cfg,
    )
    history = [ChatMessage(role=Role.USER, content="x" * 5_000_000)]
    events = []
    async for ev in agent.run_turn_stream(
        "go", history=history, context=None, session_id="s3", model_id="test/model",
    ):
        events.append(ev)
    # Provider WAS dialed even though history is gigantic — by design.
    assert spy.stream_calls >= 1
    overflow = next(
        (e for e in events if e.get("type") == "error" and e.get("reason") == "context_overflow"),
        None,
    )
    assert overflow is None
    await agent.aclose()
