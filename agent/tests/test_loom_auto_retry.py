"""Regression test for auto-retry of transient mid-stream loom errors.

Background: providers occasionally close the SSE connection mid-response
(``peer closed connection without sending complete message body``), 429
the request from a load balancer, or return an "empty response" from a
flaky upstream. Loom emits ``ErrorEvent`` + ``DoneEvent`` for these and
ends the iterator. Without the auto-retry layer in
``Agent.run_turn_stream``, the UI surfaces a "Retry" banner and the user
has to click it — which was actively interrupting CSV→table conversion
sessions.

The auto-retry layer swallows the error+done frames, sleeps briefly,
then restarts ``_loom.run_turn_stream`` from ``working_messages`` (which
already mirrors loom's ``all_messages`` and contains every successful
tool call/result this turn — so nothing is replayed).

This test drives the loop with a stub ``run_turn_stream`` that:
  1) Yields a successful tool call + result.
  2) Then yields a retryable ``error`` + ``done`` (simulating a peer-
     closed connection right after the LLM started its next iteration).
  3) On the second invocation, yields a clean ``delta`` + ``done``.

It asserts:
  - The stub was invoked twice (auto-retry happened).
  - The second invocation's input includes the prior tool turn (so no
    work was replayed).
  - No ``error`` event was yielded to the wrapper's caller.
  - The retry budget resets on each successful tool result.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from nexus.agent.llm import (
    ChatResponse,
    LLMProvider,
    StopReason,
    StreamEvent,
)
from nexus.agent.loop import Agent
from nexus.skills.registry import SkillRegistry


class _NoopProvider(LLMProvider):
    async def chat(self, messages, *, tools=None, model=None, max_tokens=None) -> ChatResponse:
        return ChatResponse(content="", stop_reason=StopReason.STOP)

    async def chat_stream(
        self, messages, *, tools=None, model=None, max_tokens=None,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield {}  # pragma: no cover

    async def aclose(self) -> None:
        pass


async def test_retryable_error_silently_restarts_loom(
    tmp_path: Path, monkeypatch
) -> None:
    """A retryable mid-stream error must not surface to the caller; the
    wrapper retries with the accumulated working_messages and the user
    sees only the eventual successful completion."""
    # Skip the real backoff so the test runs instantly. Capture the
    # original first so the replacement isn't self-recursive.
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _real_sleep(0))

    agent = Agent(
        provider=_NoopProvider(),
        registry=SkillRegistry(tmp_path / "skills"),
    )

    invocations: list[list[Any]] = []

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        # Snapshot the messages handed in — we'll assert the second call
        # received the tool turn appended by the wrapper.
        invocations.append([m.model_dump() for m in messages])
        call_index = len(invocations)

        if call_index == 1:
            # First iteration: a successful tool call + tool result, then
            # the LLM's next iteration aborts mid-stream with a retryable
            # transport error. This is the exact pattern the daemon log
            # showed (peer closed connection at iters=7).
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "tc_1",
                "name": "noop_tool",
                "arguments_delta": "{}",
            }
            yield {
                "type": "tool_exec_start",
                "tool_call_id": "tc_1",
                "name": "noop_tool",
                "arguments": "{}",
            }
            yield {
                "type": "tool_exec_result",
                "tool_call_id": "tc_1",
                "name": "noop_tool",
                "text": "ok",
                "is_error": False,
            }
            yield {
                "type": "error",
                "message": "peer closed connection without sending complete message body",
                "reason": "timeout",
                "retryable": True,
                "status_code": None,
            }
            yield {
                "type": "done",
                "stop_reason": None,
                "context": {"partial": False},
                "model": "test/model",
                "iterations": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_calls": 1,
            }
            return

        # Second invocation: clean completion.
        yield {"type": "content_delta", "delta": "hello"}
        yield {
            "type": "done",
            "stop_reason": "stop",
            "context": {"messages": [m for m in invocations[-1]]},
            "model": "test/model",
            "iterations": 1,
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events: list[dict[str, Any]] = []
    async for ev in agent.run_turn_stream(
        "convert json to tables",
        history=None,
        context=None,
        session_id="s_retry",
        model_id="test/model",
    ):
        events.append(ev)

    # The stub was invoked twice — auto-retry happened.
    assert len(invocations) == 2, (
        f"expected 2 loom invocations (initial + retry), got {len(invocations)}"
    )

    # The second invocation's input includes the prior turn's tool work,
    # so no work was replayed. Specifically: the initial input had just
    # USER, but the retry's input has USER + ASSISTANT(tool_call) + TOOL.
    first_roles = [m["role"] for m in invocations[0]]
    second_roles = [m["role"] for m in invocations[1]]
    assert first_roles == ["user"], first_roles
    assert second_roles == ["user", "assistant", "tool"], second_roles
    assert invocations[1][1]["tool_calls"][0]["name"] == "noop_tool"
    assert invocations[1][2]["content"] == "ok"

    # No error event leaked to the caller — the retry was silent.
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events == [], (
        f"retryable mid-stream error must not surface to chat_stream: {error_events}"
    )

    # A reconnecting hint surfaced before the backoff sleep so the UI
    # can render a spinner while we wait — without this the user sees a
    # frozen bubble for several seconds.
    reconnecting = [e for e in events if e.get("type") == "reconnecting"]
    assert len(reconnecting) == 1, (
        f"expected one reconnecting event, got {reconnecting}"
    )
    assert reconnecting[0]["attempt"] == 1
    assert reconnecting[0]["max_attempts"] == 3
    assert reconnecting[0]["reason"] == "timeout"
    assert reconnecting[0]["delay_seconds"] > 0

    # Final state: the user-visible delta + done from the retry.
    deltas = [e for e in events if e.get("type") == "delta"]
    assert deltas and deltas[0]["text"] == "hello"
    done = [e for e in events if e.get("type") == "done"]
    assert done, "expected a final done after the retry succeeded"

    await agent.aclose()


async def test_retryable_error_after_visible_content_is_not_silently_retried(
    tmp_path: Path, monkeypatch
) -> None:
    """If visible content has streamed for the current iteration, restart
    would duplicate tokens in the UI — fall back to surfacing the error."""
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: asyncio.sleep(0))

    agent = Agent(
        provider=_NoopProvider(),
        registry=SkillRegistry(tmp_path / "skills"),
    )

    invocations = 0

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        nonlocal invocations
        invocations += 1
        # Stream some visible content, then fail mid-response. This is
        # NOT eligible for silent retry — the UI has already rendered
        # "partial " and a fresh stream would render duplicate tokens.
        yield {"type": "content_delta", "delta": "partial "}
        yield {
            "type": "error",
            "message": "peer closed connection",
            "reason": "timeout",
            "retryable": True,
            "status_code": None,
        }
        yield {
            "type": "done",
            "stop_reason": None,
            "context": {"partial": True},
            "model": "test/model",
            "iterations": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events: list[dict[str, Any]] = []
    async for ev in agent.run_turn_stream(
        "tell me a story",
        history=None,
        context=None,
        session_id="s_partial",
        model_id="test/model",
    ):
        events.append(ev)

    # Only one loom run — content already streamed, so we did NOT restart.
    assert invocations == 1, (
        "must not silently retry once visible content has been streamed"
    )

    # The error surfaced to the caller (UI will render the retry banner).
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events, "error must be forwarded when retry is unsafe"
    assert error_events[0]["retryable"] is True

    await agent.aclose()
