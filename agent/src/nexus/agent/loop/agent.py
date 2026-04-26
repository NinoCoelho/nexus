"""Agent façade — wraps loom.Agent with Nexus-specific hooks."""

from __future__ import annotations

import asyncio
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
from .overflow import check_overflow

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

    def _context_window_for(self, model_id: str | None) -> int:
        """Lookup the configured context window for a model id.

        Returns 0 when unknown — the overflow checker treats that as
        "skip the check" so this stays a strictly opt-in safety net.
        """
        cfg = self._nexus_cfg
        if not (cfg and model_id):
            return 0
        for m in getattr(cfg, "models", []) or []:
            if getattr(m, "id", None) == model_id:
                return int(getattr(m, "context_window", 0) or 0)
        return 0

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

        # Pre-flight overflow check — refuse the turn now if the request can't
        # fit in the chosen model's context window. Without this, providers
        # like z.ai accept the request and reply with HTTP 200 + empty content,
        # which surfaces as the generic "empty_response" error and triggers an
        # endless retry loop.
        ctx_window = self._context_window_for(model_id or self._chosen_model)
        if ctx_window > 0:
            check = check_overflow(loom_messages, context_window=ctx_window)
            if check.overflowed:
                yield {
                    "type": "error",
                    "detail": check.detail,
                    "reason": "context_overflow",
                    "retryable": False,
                    "status_code": None,
                    "estimated_input_tokens": check.estimated_input_tokens,
                    "context_window": check.context_window,
                    "actions": ["compact_history", "new_session"],
                }
                yield {
                    "type": "done",
                    "session_id": session_id,
                    "reply": "",
                    "trace": [{"event": "context_overflow",
                               "estimated_input_tokens": check.estimated_input_tokens,
                               "context_window": check.context_window}],
                    "skills_touched": [],
                    "iterations": 0,
                    "messages": list(history or []) + [
                        ChatMessage(role=Role.USER, content=user_msg_content),
                    ],
                    "usage": {
                        "input_tokens": check.estimated_input_tokens,
                        "output_tokens": 0,
                        "tool_calls": 0,
                        "model": model_id or self._chosen_model,
                    },
                }
                return

        # loom yields serialized dicts (via serialize_event=model_dump) but the
        # event structure differs from what app.py expects.  We translate here.
        full_text = ""
        # Snapshot inbound history so we can rebuild the persisted message list
        # for app.py's `store.replace_history`. loom's streaming DoneEvent does
        # not carry the assembled message list.
        _history_snapshot = list(history or [])

        # Wire a per-turn thinking sink on the loom adapter. Reasoning chunks
        # from thinking models (Ollama GLM-4.7-flash, DeepSeek-R1, …) flow
        # through here as they arrive and we multiplex them into the output
        # stream alongside loom events. Reset in `finally` so concurrent turns
        # can't see each other's CoT.
        thinking_q: asyncio.Queue[str] = asyncio.Queue()
        adapter = getattr(self._loom, "_provider", None)
        had_sink_attr = adapter is not None and hasattr(adapter, "_thinking_sink")
        if had_sink_attr:
            adapter._thinking_sink = thinking_q.put_nowait  # type: ignore[attr-defined]

        loom_iter = self._loom.run_turn_stream(loom_messages, model_id=model_id).__aiter__()
        loom_task: asyncio.Task[Any] | None = asyncio.ensure_future(loom_iter.__anext__())
        q_task: asyncio.Task[str] = asyncio.ensure_future(thinking_q.get())
        try:
          while loom_task is not None:
            done, _pending = await asyncio.wait(
                {loom_task, q_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if q_task in done:
                text = q_task.result()
                if text:
                    yield {"type": "thinking", "text": text}
                q_task = asyncio.ensure_future(thinking_q.get())
            if loom_task not in done:
                continue
            try:
                raw = loom_task.result()
            except StopAsyncIteration:
                loom_task = None
                break
            loom_task = asyncio.ensure_future(loom_iter.__anext__())

            # raw is a dict because serialize_event=model_dump
            etype = raw.get("type") if isinstance(raw, dict) else getattr(raw, "type", None)
            if isinstance(raw, dict):
                ev = raw
            else:
                ev = raw.model_dump()

            if etype == "content_delta":
                delta = ev.get("delta", "")
                full_text += delta
                # Mirror to the trace bus so /chat/{sid}/events subscribers
                # (e.g. CardActivityModal) can render typing live, not only
                # after the post-turn `reply` event.
                self._on_event("delta", {"text": delta})
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
                tool_name = ev.get("name", "")
                tool_args = ev.get("arguments", "")
                # Mirror to the trace bus so off-stream subscribers see
                # tool steps live, not only on turn completion.
                self._on_event("tool_call", {"name": tool_name, "args": tool_args})
                yield {
                    "type": "tool_exec_start",
                    "name": tool_name,
                    "args": tool_args,
                }

            elif etype == "tool_exec_result":
                tool_name = ev.get("name", "")
                preview = (ev.get("text") or "")[:200]
                self._on_event("tool_result", {"name": tool_name, "preview": preview})
                yield {
                    "type": "tool_exec_result",
                    "name": tool_name,
                    "result_preview": preview,
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
                    # Heuristic: if the request was already large, the most
                    # likely cause of a 200-with-empty-content is upstream
                    # context truncation. Surface that so the UI can offer
                    # "compact history" instead of just "retry".
                    est_in = check_overflow(
                        loom_messages, context_window=ctx_window or 0
                    ).estimated_input_tokens
                    likely_overflow = ctx_window > 0 and est_in > ctx_window * 70 // 100
                    err: dict[str, Any] = {
                        "type": "error",
                        "detail": "The model returned an empty response.",
                        "reason": "empty_response",
                        "retryable": True,
                        "status_code": None,
                    }
                    if likely_overflow:
                        err["detail"] += (
                            f" Likely cause: history is at ~{est_in:,}/{ctx_window:,} "
                            f"tokens — compact the session and try again."
                        )
                        err["likely_cause"] = "context_overflow"
                        err["estimated_input_tokens"] = est_in
                        err["context_window"] = ctx_window
                        err["actions"] = ["compact_history", "new_session"]
                    yield err
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
        finally:
            # Detach the per-turn sink so background tasks / future turns
            # never inherit a closure pointing at this turn's queue.
            if had_sink_attr:
                adapter._thinking_sink = None  # type: ignore[attr-defined]
            for t in (loom_task, q_task):
                if t is not None and not t.done():
                    t.cancel()
            # Drain any thinking events that arrived after the loom DoneEvent
            # (rare, but possible if the model emitted reasoning-only chunks
            # at the very tail of the stream).
            while not thinking_q.empty():
                try:
                    text = thinking_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if text:
                    yield {"type": "thinking", "text": text}

    async def aclose(self) -> None:
        """Shut down the LLM provider and provider registry, releasing HTTP connections."""
        await self._nexus_provider.aclose()
        if self._provider_registry:
            await self._provider_registry.aclose()
