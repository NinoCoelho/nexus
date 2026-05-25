"""Loom-to-SSE event translation for the agent streaming loop.

Translates raw loom event dicts into the SSE-formatted event dicts consumed
by ``chat_stream.py``.  The translator manages per-iteration pending state
(content chunks, tool-call deltas, tool-call id mapping) and exposes a
``reset()`` method for retry cleanup.

Used directly by ``continue_after_hitl`` and partially by ``run_turn_stream``
(for state management and simple-event formatting).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import loom.types as lt

from ..ask_user_tool import parse_parked_sentinel
from ..llm import ChatMessage, Role
from .helpers import _from_loom_message
from .reasoning import ReasoningTracker

log = logging.getLogger(__name__)


class StreamTranslator:
    """Stateful translator from loom events to SSE event dicts.

    Manages pending content/tool-call state and provides
    :meth:`translate` for full event translation (used by
    ``continue_after_hitl``) and individual helpers for ``run_turn_stream``.
    """

    def __init__(
        self,
        *,
        on_event: Any,
        reasoning: ReasoningTracker,
        adapter: Any,
        history_snapshot: list[ChatMessage],
        user_msg_content: str,
        sessions: Any = None,
        session_id: str | None = None,
        trace_getter: Any | None = None,
        skills_touched_getter: Any | None = None,
        chosen_model: str | None = None,
        handlers: Any = None,
    ) -> None:
        self._on_event = on_event
        self._reasoning = reasoning
        self._adapter = adapter
        self._history_snapshot = history_snapshot
        self._user_msg_content = user_msg_content
        self._sessions = sessions
        self._session_id = session_id
        self._trace_getter = trace_getter or (lambda: [])
        self._skills_touched_getter = skills_touched_getter or (lambda: [])
        self._chosen_model = chosen_model
        self._handlers = handlers

        self.pending_content_chunks: list[str] = []
        self.pending_tcs: dict[int, dict[str, str]] = {}
        self._tc_id_by_index: dict[int, str] = {}
        self.materialised_for_iter: bool = False
        self.last_tool_exec_id: str | None = None
        self.last_tool_exec_name: str | None = None

        self.full_text: str = ""
        self.working_messages: list[lt.ChatMessage] = []

    def reset_iteration(self) -> None:
        self.pending_content_chunks.clear()
        self.pending_tcs.clear()
        self._tc_id_by_index.clear()
        self.materialised_for_iter = False

    def materialise_assistant_if_needed(self) -> None:
        if self.materialised_for_iter:
            return
        tcs: list[lt.ToolCall] = []
        for idx in sorted(self.pending_tcs.keys()):
            p = self.pending_tcs[idx]
            tcs.append(
                lt.ToolCall(
                    id=p.get("id") or f"tc_{idx}",
                    name=p.get("name") or "",
                    arguments=p.get("arguments") or "",
                )
            )
        content = "".join(self.pending_content_chunks) or None
        self.working_messages.append(
            lt.ChatMessage(
                role=lt.Role.ASSISTANT,
                content=content,
                tool_calls=tcs or None,
            )
        )
        self.materialised_for_iter = True
        self._reasoning.capture(self._adapter)

    def translate(self, ev: dict[str, Any], etype: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        if etype == "content_delta":
            delta = ev.get("delta", "")
            self.full_text += delta
            self.pending_content_chunks.append(delta)
            self.materialised_for_iter = False
            self._on_event("delta", {"text": delta})
            out.append({"type": "delta", "text": delta})

        elif etype == "tool_call_delta":
            idx = ev.get("index")
            if isinstance(idx, int):
                slot = self.pending_tcs.setdefault(
                    idx, {"id": "", "name": "", "arguments": ""},
                )
                if ev.get("id"):
                    slot["id"] = ev["id"]
                    self._tc_id_by_index[idx] = ev["id"]
                if ev.get("name"):
                    slot["name"] = ev["name"]
                args_delta = ev.get("arguments_delta")
                if args_delta:
                    slot["arguments"] = (slot.get("arguments") or "") + args_delta
                self.materialised_for_iter = False
            out.append({
                "type": "tool_call_delta",
                "index": ev.get("index"),
                "id": ev.get("id"),
                "name": ev.get("name"),
                "args_delta": ev.get("arguments_delta"),
            })

        elif etype == "tool_exec_start":
            out.extend(self._handle_tool_exec_start(ev))

        elif etype == "tool_exec_result":
            out.extend(self._handle_tool_exec_result(ev))

        elif etype == "limit_reached":
            out.append({"type": "limit_reached", "iterations": ev.get("iterations", 0)})

        elif etype == "context_overflow":
            out.append({
                "type": "error",
                "detail": ev.get("message", "context overflow"),
                "reason": "context_overflow",
                "retryable": False,
                "status_code": None,
                "estimated_input_tokens": ev.get("estimated_input_tokens", 0),
                "context_window": ev.get("context_window", 0),
                "actions": ["compact_history", "new_session"],
            })

        elif etype == "error":
            out.append({
                "type": "error",
                "detail": ev.get("message", ""),
                "reason": ev.get("reason"),
                "retryable": bool(ev.get("retryable", False)),
                "status_code": ev.get("status_code"),
            })

        elif etype == "done":
            out.extend(self._handle_done(ev))

        return out

    def format_error(self, ev: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "error",
            "detail": ev.get("message", ""),
            "reason": ev.get("reason"),
            "retryable": bool(ev.get("retryable", False)),
            "status_code": ev.get("status_code"),
        }

    def _handle_tool_exec_start(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        tool_name = ev.get("name", "")
        tool_args = ev.get("arguments", "")
        tool_call_id = ev.get("tool_call_id") or ""
        self.last_tool_exec_id = tool_call_id or None
        self.last_tool_exec_name = tool_name or None
        self.materialise_assistant_if_needed()
        if self._sessions is not None and self._session_id and tool_call_id:
            try:
                self._sessions.set_pending_tool_call(
                    self._session_id, tool_call_id,
                )
                self._sessions.set_messages_snapshot(
                    self._session_id,
                    [m.model_dump() for m in self.working_messages],
                )
            except Exception:  # noqa: BLE001
                log.exception("set_pending_tool_call failed")
        if (
            tool_name == "terminal"
            and self._handlers is not None
        ):
            th = self._handlers.terminal
            if (
                th is not None
                and hasattr(th, "_on_output")
                and th._on_output is not None
            ):
                th._on_output._call_id = tool_call_id
        self._on_event("tool_call", {"name": tool_name, "args": tool_args})
        return [{
            "type": "tool_exec_start",
            "name": tool_name,
            "args": tool_args,
            "call_id": tool_call_id,
        }]

    def _handle_tool_exec_result(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        tool_name = ev.get("name", "")
        result_text = ev.get("text") or ""
        preview = result_text[:200]

        parked_request_id = parse_parked_sentinel(result_text)
        if parked_request_id:
            snapshot_dump = [m.model_dump() for m in self.working_messages]
            if self._sessions is not None:
                try:
                    self._sessions.update_hitl_pending_snapshot(
                        parked_request_id,
                        json.dumps(snapshot_dump, ensure_ascii=False),
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "parked snapshot persist failed for %s",
                        parked_request_id,
                    )
                try:
                    self._sessions.clear_pending_tool_call(self._session_id or "")
                    self._sessions.clear_messages_snapshot(self._session_id or "")
                except Exception:  # noqa: BLE001
                    pass
            self._on_event("parked", {"request_id": parked_request_id})
            out.append({
                "type": "parked",
                "request_id": parked_request_id,
                "session_id": self._session_id,
            })
            persisted_messages = (
                self._history_snapshot
                + [ChatMessage(role=Role.USER, content=self._user_msg_content)]
                + (
                    [ChatMessage(
                        role=Role.ASSISTANT,
                        content=self.full_text,
                        reasoning_content=(
                            getattr(self._adapter, "_last_reasoning_content", None)
                            if self._adapter else None
                        ),
                    )]
                    if self.full_text
                    else []
                )
            )
            out.append({
                "type": "done",
                "session_id": self._session_id,
                "reply": self.full_text,
                "trace": self._trace_getter(),
                "skills_touched": self._skills_touched_getter(),
                "iterations": 0,
                "messages": persisted_messages,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tool_calls": 0,
                    "model": self._chosen_model,
                },
                "parked_request_id": parked_request_id,
            })
            return out

        tcid = ev.get("tool_call_id") or self.last_tool_exec_id or ""
        tc_name = ev.get("name") or self.last_tool_exec_name or tool_name
        self.working_messages.append(
            lt.ChatMessage(
                role=lt.Role.TOOL,
                content=result_text,
                tool_call_id=tcid,
                name=tc_name,
            )
        )
        self.reset_iteration()
        self._on_event("tool_result", {"name": tool_name, "preview": preview})
        out.append({
            "type": "tool_exec_result",
            "name": tool_name,
            "result_preview": preview,
        })
        return out

    def build_persisted_messages(
        self, ev: dict[str, Any],
    ) -> list[ChatMessage]:
        ctx = ev.get("context") or {}
        loom_msgs = ctx.get("messages")
        if loom_msgs:
            return [
                _from_loom_message(lt.ChatMessage(**m))
                for m in loom_msgs
                if m.get("role") != "system"
            ]
        return self._history_snapshot + [
            ChatMessage(role=Role.USER, content=self._user_msg_content),
            ChatMessage(role=Role.ASSISTANT, content=self.full_text),
        ]

    def format_done_event(
        self,
        ev: dict[str, Any],
        *,
        persisted_messages: list[ChatMessage] | None = None,
    ) -> dict[str, Any]:
        if persisted_messages is None:
            persisted_messages = self.build_persisted_messages(ev)
        model_used = ev.get("model") or self._chosen_model
        return {
            "type": "done",
            "session_id": self._session_id,
            "reply": self.full_text,
            "trace": self._trace_getter(),
            "skills_touched": (
                ev.get("skills_touched") or self._skills_touched_getter()
            ),
            "iterations": ev.get("iterations", 0),
            "messages": persisted_messages,
            "usage": {
                "input_tokens": ev.get("input_tokens", 0),
                "output_tokens": ev.get("output_tokens", 0),
                "tool_calls": ev.get("tool_calls", 0),
                "model": model_used,
            },
        }

    def _handle_done(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.materialised_for_iter:
            self._reasoning.capture(self._adapter)
        persisted_messages = self.build_persisted_messages(ev)
        self._reasoning.stamp_onto(persisted_messages, self._history_snapshot)
        return [self.format_done_event(ev, persisted_messages=persisted_messages)]
