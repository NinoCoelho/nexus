"""Tests for PlannerAgent — planner-executor split."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.agent.planner import PlannerAgent, PlanResult, SubTask
from nexus.agent.loop import AgentTurn
from nexus.agent.llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    Role,
    StopReason,
    Usage,
    ToolCall,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_llm_provider(plan_json: str, synthesis_text: str = "Final synthesized reply.") -> LLMProvider:
    """Return a mock LLMProvider that alternates between plan JSON and synthesis."""
    call_count = 0

    async def _chat(messages, *, tools=None, model=None) -> ChatResponse:
        nonlocal call_count
        call_count += 1
        # First call is the planner (returns JSON); subsequent calls are synthesis
        if call_count == 1:
            content = plan_json
        else:
            content = synthesis_text
        return ChatResponse(
            message=ChatMessage(role=Role.ASSISTANT, content=content),
            stop_reason=StopReason.STOP,
            usage=Usage(),
            model="",
        )

    provider = MagicMock(spec=LLMProvider)
    provider.chat = AsyncMock(side_effect=_chat)
    return provider


def _make_executor(reply: str = "executor reply") -> MagicMock:
    """Return a mock Agent whose run_turn always returns canned reply."""
    executor = MagicMock()
    executor.run_turn = AsyncMock(
        return_value=AgentTurn(
            reply=reply,
            skills_touched=[],
            iterations=1,
            trace=[],
            messages=[],
            input_tokens=0,
            output_tokens=0,
            tool_calls=0,
            model=None,
        )
    )
    return executor


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_task_dispatch() -> None:
    """PlannerAgent correctly dispatches 3 sub-tasks and synthesizes a final reply."""
    plan_json = json.dumps({
        "sub_tasks": [
            {"description": "Find top Python web frameworks"},
            {"description": "Compare FastAPI vs Flask performance"},
            {"description": "Summarize recommendations"},
        ]
    })
    llm = _make_llm_provider(plan_json, synthesis_text="Here is your synthesized answer.")
    executor = _make_executor("sub-task result")

    trace_events: list[dict[str, Any]] = []
    planner = PlannerAgent(executor=executor, llm=llm, on_trace=trace_events.append)

    result = await planner.run_turn("Compare Python web frameworks", history=None, context=None)

    assert isinstance(result, PlanResult)
    assert result.reply == "Here is your synthesized answer."
    assert len(result.sub_tasks) == 3
    assert all(st.status == "done" for st in result.sub_tasks)
    # Executor called once per sub-task
    assert executor.run_turn.call_count == 3

    event_types = [e["type"] for e in trace_events]
    assert "plan_start" in event_types
    assert event_types.count("subtask_start") == 3
    assert event_types.count("subtask_done") == 3
    assert "plan_done" in event_types


@pytest.mark.asyncio
async def test_single_task_fallback() -> None:
    """When the plan contains exactly 1 sub-task, PlannerAgent calls executor directly."""
    plan_json = json.dumps({"sub_tasks": [{"description": "Answer the question"}]})
    llm = _make_llm_provider(plan_json)
    executor = _make_executor("direct answer")

    planner = PlannerAgent(executor=executor, llm=llm)

    result = await planner.run_turn("What is 2+2?", history=None, context=None)

    assert result.reply == "direct answer"
    # No sub-tasks in result (fallback path)
    assert result.sub_tasks == []
    # Executor called once directly
    assert executor.run_turn.call_count == 1


@pytest.mark.asyncio
async def test_failed_subtask_does_not_halt_plan() -> None:
    """A failing sub-task marks status=failed but the plan continues."""
    plan_json = json.dumps({
        "sub_tasks": [
            {"description": "Step A"},
            {"description": "Step B (will fail)"},
            {"description": "Step C"},
        ]
    })

    call_count = 0

    async def _chat(messages, *, tools=None, model=None) -> ChatResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ChatResponse(
                message=ChatMessage(role=Role.ASSISTANT, content=plan_json),
                stop_reason=StopReason.STOP,
                usage=Usage(),
                model="",
            )
        return ChatResponse(
            message=ChatMessage(role=Role.ASSISTANT, content="Synthesized despite failure."),
            stop_reason=StopReason.STOP,
            usage=Usage(),
            model="",
        )

    llm = MagicMock(spec=LLMProvider)
    llm.chat = AsyncMock(side_effect=_chat)

    executor_call = 0

    async def _run_turn(msg, *, history=None, context=None, model_id=None):
        nonlocal executor_call
        executor_call += 1
        if executor_call == 2:  # second sub-task fails
            raise RuntimeError("upstream unavailable")
        return AgentTurn(
            reply=f"result for: {msg}",
            skills_touched=[],
            iterations=1,
            trace=[],
            messages=[],
            input_tokens=0,
            output_tokens=0,
            tool_calls=0,
            model=None,
        )

    executor = MagicMock()
    executor.run_turn = AsyncMock(side_effect=_run_turn)

    trace_events: list[dict[str, Any]] = []
    planner = PlannerAgent(executor=executor, llm=llm, on_trace=trace_events.append)

    result = await planner.run_turn("Multi-step task", history=None, context=None)

    assert result.reply == "Synthesized despite failure."
    assert len(result.sub_tasks) == 3

    statuses = {st.description: st.status for st in result.sub_tasks}
    assert statuses["Step A"] == "done"
    assert statuses["Step B (will fail)"] == "failed"
    assert statuses["Step C"] == "done"

    done_events = [e for e in trace_events if e["type"] == "subtask_done"]
    assert len(done_events) == 3


@pytest.mark.asyncio
async def test_plan_parse_failure_falls_back() -> None:
    """If LLM returns invalid JSON for the plan, fall back to direct executor."""
    llm = _make_llm_provider("not valid json at all")
    executor = _make_executor("fallback reply")

    planner = PlannerAgent(executor=executor, llm=llm)

    result = await planner.run_turn("Do something", history=None, context=None)

    assert result.reply == "fallback reply"
    assert result.sub_tasks == []
    assert executor.run_turn.call_count == 1


@pytest.mark.asyncio
async def test_subtask_ids_are_unique() -> None:
    """Each sub-task gets a unique UUID."""
    plan_json = json.dumps({
        "sub_tasks": [
            {"description": "Task 1"},
            {"description": "Task 2"},
            {"description": "Task 3"},
        ]
    })
    llm = _make_llm_provider(plan_json, synthesis_text="done")
    executor = _make_executor("ok")

    planner = PlannerAgent(executor=executor, llm=llm)
    result = await planner.run_turn("multi", history=None, context=None)

    ids = [st.id for st in result.sub_tasks]
    assert len(ids) == len(set(ids)), "sub-task IDs must be unique"
