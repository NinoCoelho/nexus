from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..agent.context import CURRENT_SESSION_ID, SUBAGENT_DEPTH
from ..agent.loop import Agent
from .events import SessionEvent

log = logging.getLogger(__name__)


def _truncate_preview(val: Any, limit: int = 200) -> str:
    s = str(val) if val is not None else ""
    return s[:limit] + ("\u2026" if len(s) > limit else "")


def create_subagent_runner(
    *,
    agent: Agent,
    store_proxy: Any,
    job_tracker: Any,
    publish_job_event: Any,
):
    async def _run_one_subagent(
        task: dict[str, Any],
        *,
        parent_session_id: str,
        depth: int,
        task_index: int,
        total_tasks: int,
    ) -> dict[str, Any]:
        task_name = task.get("name") or f"Task {task_index + 1}"
        prompt = task.get("prompt") or ""
        model_id = task.get("model_id") or None
        sub = Agent(
            provider=agent._nexus_provider,
            registry=agent._registry,
            provider_registry=agent._provider_registry,
            nexus_cfg=agent._nexus_cfg,
            home=agent._home,
            permissions=agent._permissions,
        )
        parent_store = store_proxy._resolve(parent_session_id)
        sub._sessions = parent_store
        child = parent_store.create_child(parent_session_id=parent_session_id, hidden=True)
        child_id = child.id

        job_id = job_tracker.start(
            type="subagent",
            label=task_name,
            session_id=parent_session_id,
            extra={"child_session_id": child_id, "index": task_index, "total": total_tasks},
            publish_fn=publish_job_event,
        )

        parent_store.publish(
            parent_session_id,
            SessionEvent(
                kind="subagent_start",
                data={"name": task_name, "index": task_index, "total": total_tasks, "child_session_id": child_id},
            ),
        )

        depth_token = SUBAGENT_DEPTH.set(depth + 1)
        sid_token = CURRENT_SESSION_ID.set(child_id)
        final_text = ""
        final_messages: Any = None
        error: str | None = None
        try:
            try:
                async for event in sub.run_turn_stream(
                    prompt,
                    history=[],
                    session_id=child_id,
                    model_id=model_id,
                ):
                    etype = event.get("type")
                    if etype == "delta":
                        chunk = event.get("text", "") or ""
                        final_text += chunk
                        parent_store.publish(
                            parent_session_id,
                            SessionEvent(
                                kind="subagent_delta",
                                data={"child_session_id": child_id, "text": chunk},
                            ),
                        )
                    elif etype in ("tool_exec_start", "tool_exec_result"):
                        tool_data: dict[str, Any] = {"child_session_id": child_id}
                        if etype == "tool_exec_start":
                            tool_data["name"] = event.get("name", "")
                            tool_data["args_preview"] = _truncate_preview(event.get("args"))
                            tool_data["status"] = "pending"
                            if event.get("call_id"):
                                tool_data["call_id"] = event["call_id"]
                        else:
                            tool_data["name"] = event.get("name", "")
                            tool_data["result_preview"] = _truncate_preview(event.get("result_preview"))
                            tool_data["status"] = "done"
                        parent_store.publish(
                            parent_session_id,
                            SessionEvent(kind="subagent_tool", data=tool_data),
                        )
                        if etype == "done":
                            final_messages = event.get("messages")
                    elif etype == "done":
                        final_messages = event.get("messages")
                    elif etype == "error":
                        error = str(event.get("message") or "subagent error")
                        parent_store.publish(
                            parent_session_id,
                            SessionEvent(
                                kind="subagent_done",
                                data={"child_session_id": child_id, "error": error},
                            ),
                        )
            except Exception as exc:
                log.exception("subagent run crashed (child=%s)", child_id)
                error = f"subagent crashed: {exc!r}"
        finally:
            CURRENT_SESSION_ID.reset(sid_token)
            SUBAGENT_DEPTH.reset(depth_token)
            if final_messages is not None:
                try:
                    parent_store.replace_history(child_id, final_messages)
                except Exception:
                    log.exception("subagent: persist final history failed (child=%s)", child_id)
            job_tracker.done(job_id, publish_fn=publish_job_event)
            if error is None:
                parent_store.publish(
                    parent_session_id,
                    SessionEvent(
                        kind="subagent_done",
                        data={"child_session_id": child_id, "result_preview": final_text[:200]},
                    ),
                )
        return {"session_id": child_id, "result": final_text, "error": error}

    async def _subagent_runner(
        tasks: list[dict[str, Any]],
        *,
        parent_session_id: str,
        depth: int,
    ) -> list[dict[str, Any]]:
        total = len(tasks)
        return await asyncio.gather(*[
            _run_one_subagent(
                t,
                parent_session_id=parent_session_id,
                depth=depth,
                task_index=i,
                total_tasks=total,
            )
            for i, t in enumerate(tasks)
        ])

    return _subagent_runner
