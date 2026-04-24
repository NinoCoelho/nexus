"""Planner-executor split: decompose complex tasks, dispatch sub-tasks,
synthesize final reply. Wraps an existing Agent as the executor — the
loop is unchanged."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .llm import ChatMessage, LLMProvider, Role
from .loop import Agent, AgentTurn
from .planner_prompt import PLANNER_SYSTEM_PROMPT, SYNTHESIS_SYSTEM_PROMPT

log = logging.getLogger(__name__)


@dataclass
class SubTask:
    id: str
    description: str
    skill_hint: str | None = None
    model_hint: str | None = None
    status: Literal["pending", "running", "done", "failed"] = "pending"
    result: str | None = None


@dataclass
class PlanResult:
    reply: str
    sub_tasks: list[SubTask]
    trace: list[dict[str, Any]] = field(default_factory=list)


class PlannerAgent:
    """Decomposes user messages into sub-tasks, runs each through the
    executor, synthesizes a final reply.

    Falls back to direct executor call when the LLM returns a single
    sub-task or fails to produce a plan — the planner is transparent
    for simple queries."""

    def __init__(
        self,
        executor: Agent,
        llm: LLMProvider,
        planner_model: str | None = None,
        *,
        on_trace: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._executor = executor
        self._llm = llm
        self._model = planner_model
        self._on_trace = on_trace or (lambda _: None)

    async def run_turn(
        self,
        message: str,
        *,
        history: list[ChatMessage] | None = None,
        context: str | None = None,
    ) -> PlanResult:
        sub_tasks = await self._plan(message, history=history, context=context)

        if len(sub_tasks) <= 1:
            # Fall back to direct executor for simple single-step queries
            turn: AgentTurn = await self._executor.run_turn(
                message,
                history=history,
                context=context,
            )
            return PlanResult(reply=turn.reply, sub_tasks=[], trace=turn.trace)

        self._on_trace({"type": "plan_start", "sub_tasks": [
            {"id": st.id, "description": st.description} for st in sub_tasks
        ]})

        results: list[str] = []
        for st in sub_tasks:
            st.status = "running"
            self._on_trace({"type": "subtask_start", "id": st.id, "description": st.description})
            try:
                turn = await self._executor.run_turn(
                    st.description,
                    history=history,
                    context=context,
                )
                st.result = (turn.reply or "")[:2000]
                st.status = "done"
                results.append(f"### Sub-task: {st.description}\n{st.result}")
                self._on_trace({
                    "type": "subtask_done",
                    "id": st.id,
                    "result_preview": st.result[:200],
                })
            except Exception as exc:
                st.status = "failed"
                st.result = str(exc)
                results.append(f"### Sub-task: {st.description}\nFAILED: {exc}")
                self._on_trace({
                    "type": "subtask_done",
                    "id": st.id,
                    "result_preview": f"FAILED: {exc}",
                })

        synthesis_reply = await self._synthesize(message, results)
        self._on_trace({"type": "plan_done", "sub_tasks": [
            {
                "id": st.id,
                "status": st.status,
                "result_preview": (st.result or "")[:200],
            }
            for st in sub_tasks
        ]})

        return PlanResult(reply=synthesis_reply, sub_tasks=sub_tasks, trace=[])

    async def _plan(
        self,
        message: str,
        *,
        history: list[ChatMessage] | None,
        context: str | None,
    ) -> list[SubTask]:
        """Call LLM with a decomposition prompt. Returns a list of SubTask.

        Returns a single-item list if the message is simple or the LLM
        fails to produce a plan — this triggers the direct-executor fallback.
        """
        messages = [
            ChatMessage(role=Role.SYSTEM, content=PLANNER_SYSTEM_PROMPT),
            ChatMessage(role=Role.USER, content=message),
        ]
        try:
            response = await self._llm.chat(messages, tools=[], model=self._model)
            raw = (response.content or "").strip()
            data = json.loads(raw)
            raw_tasks: list[dict[str, Any]] = data.get("sub_tasks", [])
            if not isinstance(raw_tasks, list) or len(raw_tasks) == 0:
                return [SubTask(id=str(uuid.uuid4()), description=message)]
            return [
                SubTask(
                    id=str(uuid.uuid4()),
                    description=str(t.get("description", message)),
                    skill_hint=t.get("skill_hint"),
                    model_hint=t.get("model_hint"),
                )
                for t in raw_tasks[:5]  # cap at 5
            ]
        except Exception:
            log.warning("planner: failed to parse plan; falling back to direct executor", exc_info=True)
            return [SubTask(id=str(uuid.uuid4()), description=message)]

    async def _synthesize(self, original_message: str, results: list[str]) -> str:
        """Compose a final reply from sub-task outputs."""
        combined = "\n\n".join(results)
        user_content = (
            f"Original request: {original_message}\n\n"
            f"Sub-task results:\n{combined}"
        )
        messages = [
            ChatMessage(role=Role.SYSTEM, content=SYNTHESIS_SYSTEM_PROMPT),
            ChatMessage(role=Role.USER, content=user_content),
        ]
        try:
            response = await self._llm.chat(messages, tools=[], model=self._model)
            return (response.content or "").strip()
        except Exception:
            log.warning("planner: synthesis failed; concatenating results", exc_info=True)
            return combined
