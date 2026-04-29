"""Regression test for the petshop session HITL form-loss bug.

A parked ``ask_user`` returns ``__parked__:<rid>`` from the broker. The
agent loop's ``tool_exec_result`` handler must detect that string and
end the turn (no further LLM call) so a later ``continue_after_hitl``
can resume from a clean snapshot.

Before the fix, the registry closure wrapped the answer in the
``AskUserResult.to_text()`` JSON envelope, so ``parse_parked_sentinel``
never matched and the loop kept going — which is what dropped the form
payload in session ``5e22c7600409469ebb615ee1c140cddd``.

This test drives the loop with a stub ``run_turn_stream`` that emits a
parking ``tool_exec_result`` and asserts:

* a ``parked`` event is yielded,
* a ``done`` event terminates the turn,
* ``update_hitl_pending_snapshot`` was called with the assistant
  tool_call materialised in the snapshot.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from nexus.agent.ask_user_tool import parked_sentinel
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
            yield {}  # pragma: no cover — make this an async generator

    async def aclose(self) -> None:
        pass


class _RecordingSessions:
    """Minimal stand-in for SessionStore — captures the snapshot update."""

    def __init__(self) -> None:
        self.snapshot_calls: list[dict[str, Any]] = []
        self.cleared_pending: list[str] = []
        self.cleared_snapshot: list[str] = []

    def update_hitl_pending_snapshot(
        self,
        request_id: str,
        parked_messages_json: str,
        *,
        model_id: str | None = None,
    ) -> bool:
        self.snapshot_calls.append(
            {
                "request_id": request_id,
                "parked_messages_json": parked_messages_json,
                "model_id": model_id,
            }
        )
        return True

    def clear_pending_tool_call(self, session_id: str) -> None:
        self.cleared_pending.append(session_id)

    def clear_messages_snapshot(self, session_id: str) -> None:
        self.cleared_snapshot.append(session_id)


async def test_parked_sentinel_ends_turn_and_persists_snapshot(tmp_path: Path) -> None:
    """The parking sentinel must short-circuit the loop and backfill the
    parked-row snapshot with the assistant tool_call."""
    rid = "abc123"
    sentinel = parked_sentinel(rid)
    tool_call_id = "tc_form_1"

    agent = Agent(
        provider=_NoopProvider(),
        registry=SkillRegistry(tmp_path / "skills"),
    )
    sessions = _RecordingSessions()
    agent._sessions = sessions  # type: ignore[assignment]

    next_iter_started = False

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        nonlocal next_iter_started
        # Iteration 1: model emits a tool_call for ask_user, then we
        # dispatch and report the result as the parked sentinel.
        yield {
            "type": "tool_call_delta",
            "index": 0,
            "id": tool_call_id,
            "name": "ask_user",
            "arguments_delta": '{"prompt":"name?","kind":"form"}',
        }
        yield {
            "type": "tool_exec_start",
            "tool_call_id": tool_call_id,
            "name": "ask_user",
            "arguments": '{"prompt":"name?","kind":"form"}',
        }
        # The bug: agent treated this as a normal tool result and pressed
        # on. After the fix, the loop must NOT call us again after this.
        yield {
            "type": "tool_exec_result",
            "tool_call_id": tool_call_id,
            "name": "ask_user",
            "text": sentinel,
            "is_error": False,
        }
        # If the loop is still alive past this point, the bug regressed.
        next_iter_started = True
        yield {
            "type": "done",
            "stop_reason": "stop",
            "context": {"messages": []},
            "model": "test/model",
            "iterations": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 1,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events: list[dict[str, Any]] = []
    async for ev in agent.run_turn_stream(
        "Fatima maria has a Golden Retriever and a Shih Tzu",
        history=None,
        context=None,
        session_id="s_park",
        model_id="test/model",
    ):
        events.append(ev)

    # The loop emitted parked + done and stopped consuming the loom stream
    # before our stub could yield the next iteration.
    parked = [e for e in events if e.get("type") == "parked"]
    assert parked, f"expected a parked event, got {[e.get('type') for e in events]}"
    assert parked[0]["request_id"] == rid

    done = [e for e in events if e.get("type") == "done"]
    assert done, "expected a done event after parking"
    assert done[0].get("parked_request_id") == rid

    assert not next_iter_started, (
        "loop kept consuming after the parked sentinel — the bug regressed"
    )

    # Snapshot was persisted with the assistant tool_call materialised so
    # continue_after_hitl can resume from a sane history.
    assert len(sessions.snapshot_calls) == 1
    call = sessions.snapshot_calls[0]
    assert call["request_id"] == rid
    snapshot = json.loads(call["parked_messages_json"])
    # The parked snapshot must NOT include the sentinel TOOL message — only
    # the user message + the assistant message that issued the tool_call.
    roles = [m.get("role") for m in snapshot]
    assert "tool" not in roles, f"sentinel TOOL leaked into snapshot: {snapshot}"
    assert roles[-1] == "assistant", (
        f"snapshot must end with the assistant tool_call, got: {roles}"
    )
    last = snapshot[-1]
    assert last.get("tool_calls"), "assistant snapshot is missing tool_calls"
    assert last["tool_calls"][0].get("name") == "ask_user"

    await agent.aclose()


async def test_envelope_wrapped_sentinel_not_passed_to_llm(tmp_path: Path) -> None:
    """End-to-end through the registry closure: a parked AskUserResult
    must surface to the agent loop as the bare sentinel string, not the
    JSON envelope. This is the single line of defense protecting the
    parking flow from looking like a normal tool result.
    """
    from nexus.agent._loom_bridge.registry import (
        AgentHandlers,
        build_tool_registry,
    )
    from nexus.agent.ask_user_tool import AskUserResult

    class _StubAskUserHandler:
        async def invoke(self, args: dict) -> AskUserResult:
            return AskUserResult(
                ok=True,
                answer=parked_sentinel("rid-xyz"),
                kind="form",
                timed_out=False,
            )

    handlers = AgentHandlers(ask_user=_StubAskUserHandler())
    registry = build_tool_registry(
        skill_registry=SkillRegistry(tmp_path / "skills"),
        handlers=handlers,
    )

    result = await registry.dispatch(
        "ask_user",
        {"prompt": "name?", "kind": "form", "fields": [{"name": "x", "type": "text"}]},
    )

    assert result.text == parked_sentinel("rid-xyz"), (
        f"closure wrapped the parked sentinel in an envelope: {result.text!r}"
    )
