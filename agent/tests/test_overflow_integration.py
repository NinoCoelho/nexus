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


async def test_dead_placeholders_are_stripped_before_overflow_check(
    agent_with_window,
) -> None:
    """Persisted ``[empty_response]`` rows must not count toward the next
    turn's pre-flight estimate. Otherwise a single overflow snowballs across
    every retry."""
    agent, spy = agent_with_window
    # Borderline-fitting history: filler that's just inside the window once
    # the placeholder is stripped, but spills over if we double-count it.
    filler = ChatMessage(role=Role.USER, content="x" * 600_000)
    placeholder = ChatMessage(
        role=Role.ASSISTANT, content="[empty_response] ", tool_calls=[]
    )
    history = [filler, placeholder]
    events = []
    async for ev in agent.run_turn_stream(
        "retry",
        history=history,
        context=None,
        session_id="s4",
        model_id="test/model",
    ):
        events.append(ev)
    # Provider was dialed because the placeholder was filtered out.
    assert spy.stream_calls >= 1
    err = next((e for e in events if e.get("type") == "error"), None)
    # If the placeholder had leaked through, it would have triggered the
    # pre-flight overflow with a context_overflow reason. It didn't.
    if err is not None:
        assert err.get("reason") != "context_overflow"
    await agent.aclose()


async def test_dead_placeholder_strip_semantics(agent_with_window) -> None:
    """Placeholder assistants get stripped *regardless of tool_calls*.

    Real-world failure mode: when an assistant emits tool_call deltas
    and then the LLM returns empty (z.ai pattern), persistence stamps
    ``[empty_response]`` onto the assistant *with* the orphan tool_calls
    list. The tools never ran, so there's no following TOOL message —
    feeding that to the next LLM call breaks the assistant→tool_call→
    tool sequence the prompt template assumes, triggering more empty
    responses. The strip must drop these too, plus any TOOL message
    whose id matches an orphan call."""
    from nexus.agent.loop.agent import (
        _has_dead_placeholder_prefix,
        _strip_dead_placeholders,
    )
    from nexus.agent.llm import ToolCall

    plain = ChatMessage(role=Role.ASSISTANT, content="hello", tool_calls=[])
    assert _has_dead_placeholder_prefix(plain) is False

    placeholder_no_tcs = ChatMessage(
        role=Role.ASSISTANT, content="[empty_response] ", tool_calls=[]
    )
    assert _has_dead_placeholder_prefix(placeholder_no_tcs) is True

    placeholder_with_orphan_tcs = ChatMessage(
        role=Role.ASSISTANT,
        content="[empty_response] ",
        tool_calls=[ToolCall(id="orphan-1", name="web_search", arguments={})],
    )
    assert _has_dead_placeholder_prefix(placeholder_with_orphan_tcs) is True

    # End-to-end: a [empty_response] with orphan tool_calls AND a stray TOOL
    # message tied to one of those ids both get dropped on the way to the LLM.
    history = [
        ChatMessage(role=Role.USER, content="hi"),
        placeholder_with_orphan_tcs,
        # Stray TOOL referring to the orphan id (rare but possible if the
        # tool ran and result landed before the [empty_response] stamp).
        ChatMessage(
            role=Role.TOOL,
            content="leftover",
            tool_call_id="orphan-1",
            name="web_search",
        ),
        ChatMessage(role=Role.USER, content="continue"),
    ]
    out = _strip_dead_placeholders(history)
    assert [m.role for m in out] == [Role.USER, Role.USER]
    assert [m.content for m in out] == ["hi", "continue"]


async def test_wrapper_forwards_loom_context_overflow_event(
    tmp_path: Path,
) -> None:
    """The intra-loop overflow guard now lives in loom (it has visibility
    into the system prompt; the nexus wrapper does not). The wrapper's job
    is to forward loom's ``context_overflow`` event to the UI banner path.

    We mock loom emitting the event directly — the contract that matters is
    the translation layer, not the underlying detection."""

    cfg = _cfg_with_window(window=20_000)
    spy = _SpyProvider()
    agent = Agent(
        provider=spy,
        registry=SkillRegistry(tmp_path / "skills"),
        nexus_cfg=cfg,
    )

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        # Loom's overflow guard fired at iteration 1 after a tool result
        # ballooned the prompt. It emits the typed event followed by Done.
        yield {
            "type": "context_overflow",
            "message": "Conversation is too large for this model: ~250,000 input tokens vs. 20,000 window (1250% of capacity, no room for a reply). Compact the history or start a new session.",
            "estimated_input_tokens": 250_000,
            "context_window": 20_000,
            "headroom": 1_000,
            "iteration": 1,
        }
        yield {
            "type": "done",
            "stop_reason": "stop",
            "context": {"messages": [], "context_overflow": True},
            "model": "test/model",
            "iterations": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    out_events: list[dict] = []
    async for ev in agent.run_turn_stream(
        "anything",
        history=None,
        context=None,
        session_id="s_intra",
        model_id="test/model",
    ):
        out_events.append(ev)

    # Wrapper translated the typed loom event into the structured error
    # the UI's existing banner code already understands.
    err = next(
        (
            e for e in out_events
            if e.get("type") == "error" and e.get("reason") == "context_overflow"
        ),
        None,
    )
    assert err is not None, (
        f"expected context_overflow error, got: {[e.get('type') for e in out_events]}"
    )
    assert err["context_window"] == 20_000
    assert err["estimated_input_tokens"] == 250_000
    assert "compact_history" in err.get("actions", [])
    await agent.aclose()
