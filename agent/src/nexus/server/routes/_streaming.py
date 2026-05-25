"""Shared streaming utilities for agent-turn event processing.

Provides a stateful accumulator that tracks deltas, tool calls, and done/error
state across the three streaming sites: chat_stream, chat_hitl_answer, and
vault_dispatch_helpers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnAccumulator:
    accumulated_text: str = ""
    accumulated_tools: list[dict[str, Any]] = field(default_factory=list)
    final_messages: list[Any] | None = None
    partial_status: str = "interrupted"

    def reset_partial_status(self, reason: str | None) -> None:
        if reason and reason not in ("interrupted", "cancelled"):
            self.partial_status = reason

    def process_event(self, event: dict[str, Any]) -> list[str]:
        etype = event.get("type")
        sse_frames: list[str] = []

        if etype == "delta":
            self.accumulated_text += event.get("text", "")
            sse_frames.append(
                _sse("delta", {"text": event["text"]})
            )

        elif etype == "thinking":
            sse_frames.append(
                _sse("thinking", {"text": event.get("text", "")})
            )

        elif etype == "limit_reached":
            self.partial_status = "iteration_limit"
            sse_frames.append(
                _sse("limit_reached", {"iterations": event.get("iterations", 0)})
            )

        elif etype == "reconnecting":
            sse_frames.append(
                _sse("reconnecting", {
                    "attempt": event.get("attempt", 1),
                    "max_attempts": event.get("max_attempts", 1),
                    "delay_seconds": event.get("delay_seconds", 0),
                    "reason": event.get("reason", ""),
                })
            )

        elif etype == "paused_for_cooldown":
            sse_frames.append(
                _sse("paused_for_cooldown", {
                    "retry_after": event.get("retry_after", ""),
                    "estimated_seconds": event.get("estimated_seconds", 60),
                    "reason": event.get("reason", ""),
                })
            )

        elif etype in ("tool_exec_start", "tool_exec_result"):
            payload: dict[str, Any] = {"name": event.get("name", "")}
            if "args" in event:
                payload["args"] = event["args"]
            if "result_preview" in event:
                payload["result_preview"] = event["result_preview"]
            if "call_id" in event:
                payload["call_id"] = event["call_id"]
            if etype == "tool_exec_start":
                self.accumulated_tools.append({
                    "name": event.get("name", ""),
                    "args": event.get("args"),
                    "status": "pending",
                })
            else:
                for t in reversed(self.accumulated_tools):
                    if t.get("name") == event.get("name") and t.get("status") == "pending":
                        t["status"] = "done"
                        t["result_preview"] = event.get("result_preview")
                        break
            sse_frames.append(_sse("tool", payload))

        elif etype == "done":
            self.final_messages = event.get("messages")
            usage = event.get("usage") or {}
            done_payload = {
                "session_id": event.get("session_id", ""),
                "reply": event.get("reply", ""),
                "trace": event.get("trace", []),
                "skills_touched": event.get("skills_touched", []),
                "iterations": event.get("iterations", 0),
                "usage": usage,
                "model": usage.get("model"),
            }
            sse_frames.append(_sse("done", done_payload))

        elif etype == "error":
            reason = event.get("reason")
            self.reset_partial_status(reason)
            err_payload: dict[str, Any] = {
                "detail": event.get("detail", ""),
                "reason": reason,
                "retryable": event.get("retryable"),
                "status_code": event.get("status_code"),
            }
            for k in ("likely_cause", "estimated_input_tokens", "context_window", "actions"):
                if k in event:
                    err_payload[k] = event[k]
            sse_frames.append(_sse("error", err_payload))

        return sse_frames


def build_done_sse(
    *,
    session_id: str,
    reply: str = "",
    trace: list[Any] | None = None,
    skills_touched: list[Any] | None = None,
    iterations: int = 0,
    usage: dict[str, Any] | None = None,
    model: str | None = None,
) -> str:
    return _sse("done", {
        "session_id": session_id,
        "reply": reply,
        "trace": trace or [],
        "skills_touched": skills_touched or [],
        "iterations": iterations,
        "usage": usage or {},
        "model": model or "",
    })


def build_error_sse(
    *,
    detail: str,
    reason: str | None = None,
    retryable: bool | None = None,
    status_code: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "detail": detail,
        "reason": reason,
        "retryable": retryable,
        "status_code": status_code,
    }
    if extra:
        payload.update(extra)
    return _sse("error", payload)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def drain_events_to_sse(
    source: AsyncIterator[dict[str, Any] | None],
    acc: TurnAccumulator,
    *,
    keepalive_interval: float = 0,
) -> AsyncIterator[str]:
    """Consume agent events and yield SSE frames.

    Handles keepalive pings when *keepalive_interval* > 0 and the source
    is already wrapped in :func:`_sse.keepalive`.
    """
    async for event in source:
        if event is None:
            yield ": ping\n\n"
            continue
        for frame in acc.process_event(event):
            yield frame
