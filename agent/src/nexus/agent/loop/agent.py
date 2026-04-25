"""Agent façade — wraps loom.Agent with Nexus-specific hooks."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import loom.types as lt
from ..ask_user_tool import AskUserHandler
from ..llm import ChatMessage, LLMProvider, Role, StreamEvent
from ..terminal_tool import TerminalHandler
from ...skills.registry import SkillRegistry
from ._builder import build_loom_agent
from .helpers import (
    AgentTurn,
    _annotate_short_reply,
    _from_loom_message,
    _to_loom_message,
)

log = logging.getLogger(__name__)

TraceCallback = Callable[[str, dict[str, Any]], None]


class Agent:
    """Nexus façade over loom.Agent.

    Manages the LLM provider lifecycle, tool registry, HITL handlers
    (ask_user, terminal), and per-session trace logic. Exposes two execution
    modes — blocking (``run_turn``) and streaming (``run_turn_stream``) —
    translating internal loom events into the format expected by FastAPI routes.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        registry: SkillRegistry,
        trace: TraceCallback | None = None,
        provider_registry: Any | None = None,
        nexus_cfg: Any | None = None,
        ask_user_handler: AskUserHandler | None = None,
    ) -> None:
        from .._loom_bridge import AgentHandlers

        self._nexus_provider = provider
        self._registry = registry
        self._trace = trace
        self._provider_registry = provider_registry
        self._nexus_cfg = nexus_cfg
        # _handlers is a mutable container shared with the tool registry
        # so late-binding by app.py is reflected at dispatch time.
        self._handlers = AgentHandlers(ask_user=ask_user_handler)
        # Accumulated trace for the current turn (rebuilt per turn)
        self._turn_trace: list[dict[str, Any]] = []
        # Skills touched in the current turn
        self._skills_touched: list[str] = []
        # Chosen model for the current turn
        self._chosen_model: str | None = None

        self._loom = build_loom_agent(
            nexus_provider=self._nexus_provider,
            registry=self._registry,
            handlers=self._handlers,
            provider_registry=self._provider_registry,
            nexus_cfg=self._nexus_cfg,
            get_chosen_model=lambda: self._chosen_model,
            get_turn_trace=lambda: self._turn_trace,
            on_trace_event=self._on_event,
        )

    def _on_event(self, kind: str, payload: dict[str, Any]) -> None:
        entry = {"event": kind, **payload}
        self._turn_trace.append(entry)
        if self._trace:
            self._trace(kind, payload)

    # app.py sets these attributes directly after construction; we intercept
    # via properties so the mutable handler container stays in sync.
    @property
    def _ask_user_handler(self) -> AskUserHandler | None:
        return self._handlers.ask_user

    @_ask_user_handler.setter
    def _ask_user_handler(self, value: AskUserHandler | None) -> None:
        self._handlers.ask_user = value
        # Also update terminal handler when ask_user changes
        if value is not None and self._handlers.terminal is None:
            self._handlers.terminal = TerminalHandler(ask_user_handler=value)

    @property
    def _terminal_handler(self) -> Any:
        return self._handlers.terminal

    @_terminal_handler.setter
    def _terminal_handler(self, value: Any) -> None:
        self._handlers.terminal = value

    @property
    def _dispatcher(self) -> Any:
        return self._handlers.dispatcher

    @_dispatcher.setter
    def _dispatcher(self, value: Any) -> None:
        self._handlers.dispatcher = value

    def _resolve_provider(self, model_id: str | None) -> tuple[LLMProvider, str | None]:
        """Return (nexus_provider, upstream_model_name). Kept for app.py compat."""
        if self._provider_registry and model_id:
            try:
                provider, upstream = self._provider_registry.get_for_model(model_id)
                return provider, upstream
            except KeyError:
                pass
        return self._nexus_provider, None

    async def run_turn(
        self,
        user_message: str,
        *,
        history: list[ChatMessage] | None = None,
        context: str | None = None,
        model_id: str | None = None,
    ) -> AgentTurn:
        """Execute a complete turn in blocking mode and return the result.

        Args:
            user_message: The user's message for this turn.
            history: Prior message history for the session.
            context: Optional additional context injected into the turn.
            model_id: Force a specific model; None uses the configured default.

        Returns:
            AgentTurn containing the reply, token usage, event trace, and the
            message list that should replace the persisted history.
        """
        self._turn_trace = []
        self._skills_touched = []
        self._chosen_model = model_id

        # Build loom message list
        loom_messages: list[lt.ChatMessage] = []
        if history:
            loom_messages = [_to_loom_message(m) for m in history]

        # Annotate terse yes/no using loom agent's pending question
        pending = self._loom._pending_question
        annotated = _annotate_short_reply(user_message, pending)
        loom_messages.append(
            lt.ChatMessage(role=lt.Role.USER, content=annotated or user_message)
        )

        loom_turn = await self._loom.run_turn(loom_messages, model_id=model_id)

        # Convert loom messages back to Nexus messages
        nexus_messages = [_from_loom_message(m) for m in loom_turn.messages]

        return AgentTurn(
            reply=loom_turn.reply,
            skills_touched=loom_turn.skills_touched,
            iterations=loom_turn.iterations,
            trace=list(self._turn_trace),
            messages=nexus_messages,
            input_tokens=loom_turn.input_tokens,
            output_tokens=loom_turn.output_tokens,
            tool_calls=loom_turn.tool_calls,
            model=loom_turn.model or self._chosen_model,
        )

    async def run_turn_stream(
        self,
        user_message: str,
        *,
        history: list[ChatMessage] | None = None,
        context: str | None = None,
        session_id: str | None = None,
        model_id: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Execute a turn in streaming mode, yielding typed SSE events.

        Translates internal loom events (content_delta, tool_exec_start,
        done, error, etc.) into the dictionary format consumed by
        ``chat_stream.py``. The ``done`` event includes the message list
        assembled by loom for replacing the persisted history.

        Args:
            user_message: The user's message for this turn.
            history: Prior message history for the session.
            context: Optional additional context (not used by loom directly).
            session_id: Session ID; forwarded in the ``done`` event to the client.
            model_id: Force a specific model; None uses the configured default.

        Yields:
            Dicts typed by ``type`` (delta, tool_exec_start,
            tool_exec_result, limit_reached, error, done).
        """
        self._turn_trace = []
        self._skills_touched = []
        self._chosen_model = model_id

        loom_messages: list[lt.ChatMessage] = []
        if history:
            loom_messages = [_to_loom_message(m) for m in history]

        pending = self._loom._pending_question
        annotated = _annotate_short_reply(user_message, pending)
        user_msg_content = annotated or user_message
        loom_messages.append(
            lt.ChatMessage(role=lt.Role.USER, content=user_msg_content)
        )

        # loom yields serialized dicts (via serialize_event=model_dump) but the
        # event structure differs from what app.py expects.  We translate here.
        full_text = ""
        # Snapshot inbound history so we can rebuild the persisted message list
        # for app.py's `store.replace_history`. loom's streaming DoneEvent does
        # not carry the assembled message list.
        _history_snapshot = list(history or [])

        async for raw in self._loom.run_turn_stream(loom_messages, model_id=model_id):
            # raw is a dict because serialize_event=model_dump
            etype = raw.get("type") if isinstance(raw, dict) else getattr(raw, "type", None)
            if isinstance(raw, dict):
                ev = raw
            else:
                ev = raw.model_dump()

            if etype == "content_delta":
                delta = ev.get("delta", "")
                full_text += delta
                yield {"type": "delta", "text": delta}

            elif etype == "tool_call_delta":
                # Forward tool streaming deltas (UI progress)
                yield {
                    "type": "tool_call_delta",
                    "index": ev.get("index"),
                    "id": ev.get("id"),
                    "name": ev.get("name"),
                    "args_delta": ev.get("arguments_delta"),
                }

            elif etype == "tool_exec_start":
                yield {
                    "type": "tool_exec_start",
                    "name": ev.get("name", ""),
                    "args": ev.get("arguments", ""),
                }

            elif etype == "tool_exec_result":
                yield {
                    "type": "tool_exec_result",
                    "name": ev.get("name", ""),
                    "result_preview": (ev.get("text") or "")[:200],
                }

            elif etype == "limit_reached":
                yield {"type": "limit_reached", "iterations": ev.get("iterations", 0)}

            elif etype == "error":
                yield {
                    "type": "error",
                    "detail": ev.get("message", ""),
                    "reason": ev.get("reason"),
                    "retryable": ev.get("retryable", False),
                    "status_code": ev.get("status_code"),
                }

            elif etype == "done":
                # loom RFC-0004: model/iterations/tokens are top-level typed
                # fields on DoneEvent. Only "messages" stays inside context.
                ctx = ev.get("context") or {}
                model_used = ev.get("model") or self._chosen_model
                reply_text = full_text
                stop_reason = ev.get("stop_reason")
                # Surface truncation + empty response as a retryable error
                # BEFORE the done frame so the UI renders an actionable banner.
                if stop_reason == "length":
                    log.info(
                        "turn ended truncated (stop_reason=length, reply_len=%d)",
                        len(reply_text),
                    )
                    yield {
                        "type": "error",
                        "detail": "Response was truncated — the model hit its output limit.",
                        "reason": "length",
                        "retryable": True,
                        "status_code": None,
                    }
                elif not reply_text and stop_reason not in ("tool_use",):
                    log.info(
                        "turn ended with empty reply (stop_reason=%s)", stop_reason,
                    )
                    yield {
                        "type": "error",
                        "detail": "The model returned an empty response.",
                        "reason": "empty_response",
                        "retryable": True,
                        "status_code": None,
                    }
                # Prefer the assembled message list from loom (includes
                # tool_calls + TOOL role messages). Strip system messages
                # (re-built each turn by before_llm_call). Fall back to a
                # plain user+assistant synthesis if loom didn't provide one.
                loom_msgs = ctx.get("messages")
                if loom_msgs:
                    persisted_messages = [
                        _from_loom_message(lt.ChatMessage(**m))
                        for m in loom_msgs
                        if m.get("role") != "system"
                    ]
                else:
                    persisted_messages = _history_snapshot + [
                        ChatMessage(role=Role.USER, content=user_msg_content),
                        ChatMessage(role=Role.ASSISTANT, content=reply_text),
                    ]
                yield {
                    "type": "done",
                    "session_id": session_id,
                    "reply": reply_text,
                    "trace": list(self._turn_trace),
                    "skills_touched": ev.get("skills_touched") or list(self._skills_touched),
                    "iterations": ev.get("iterations", 0),
                    "messages": persisted_messages,
                    "usage": {
                        "input_tokens": ev.get("input_tokens", 0),
                        "output_tokens": ev.get("output_tokens", 0),
                        "tool_calls": ev.get("tool_calls", 0),
                        "model": model_used,
                    },
                }

    async def aclose(self) -> None:
        """Shut down the LLM provider and provider registry, releasing HTTP connections."""
        await self._nexus_provider.aclose()
        if self._provider_registry:
            await self._provider_registry.aclose()
