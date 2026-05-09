"""Agent façade — wraps loom.Agent with Nexus-specific hooks."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

import loom.types as lt
from ..ask_user_tool import AskUserHandler, parse_parked_sentinel
from ..llm import ChatMessage, ContentPart, LLMProvider, Role, StreamEvent
from ...skills.registry import SkillRegistry
from ._builder import build_loom_agent
from .helpers import (
    AgentTurn,
    _annotate_short_reply,
    _build_user_message,
    _from_loom_message,
    _to_loom_message,
)
from .overflow import check_overflow, known_context_window, _DEFAULT_FALLBACK_WINDOW

if TYPE_CHECKING:
    from loom.home import AgentHome
    from loom.permissions import AgentPermissions

log = logging.getLogger(__name__)

TraceCallback = Callable[[str, dict[str, Any]], None]

# Assistant placeholders persisted by chat_stream.py when a turn ended with no
# usable content. Keeping them in the LLM prompt on the next turn just bloats
# context (and on retry, accelerates the very overflow that caused them).
# They stay in the SQLite history so the UI can still render the partial-turn
# banner — only the LLM ingress filters them out.
#
# ``[interrupted]`` is included because mid-stream interruptions persist with
# an aspirational tool_calls list whose tools never actually ran — which
# leaves the LLM staring at an assistant→tool_call→<no result>→user
# sequence that confuses many providers (z.ai GLM among them) into another
# empty response on the next turn. The tool_calls are noise without their
# results; drop the whole message and any TOOL stragglers tied to those
# orphan call ids.
_DROP_ASSISTANT_PLACEHOLDERS = (
    "[empty_response]",
    "[llm_error]",
    "[upstream_timeout]",
    "[crashed]",
    "[interrupted]",
    "[cancelled]",
    "[rate_limited]",
    "[budget_exceeded]",
)


def _has_dead_placeholder_prefix(msg: ChatMessage) -> bool:
    """True when ``msg``'s content begins with a no-signal status prefix.
    Tool_calls are deliberately ignored: an assistant that emitted a
    placeholder produced no usable content for the model to build on,
    and any tool_calls it carries were never executed (no TOOL message
    follows them in history) — feeding them to the LLM corrupts the
    assistant→tool_call→tool sequence the prompt template assumes."""
    if msg.role != Role.ASSISTANT:
        return False
    text = (msg.content or "").strip()
    if not text:
        return False
    for prefix in _DROP_ASSISTANT_PLACEHOLDERS:
        if text == prefix or text.startswith(prefix + " "):
            return True
    return False


def _strip_dead_placeholders(history: list[ChatMessage]) -> list[ChatMessage]:
    """Drop assistant placeholders + their orphan tool messages before
    feeding history to the LLM.

    Two-pass:
      1. Collect the tool_call_ids on each dead-placeholder assistant.
      2. Drop both those assistants and any TOOL message keyed to one of
         the collected ids (catches the rare case where a tool DID run
         but its parent assistant got marked partial after the fact).
    """
    orphan_tc_ids: set[str] = set()
    for m in history:
        if _has_dead_placeholder_prefix(m) and m.tool_calls:
            for tc in m.tool_calls:
                if tc.id:
                    orphan_tc_ids.add(tc.id)
    out: list[ChatMessage] = []
    for m in history:
        if _has_dead_placeholder_prefix(m):
            continue
        if (
            m.role == Role.TOOL
            and m.tool_call_id
            and m.tool_call_id in orphan_tc_ids
        ):
            continue
        out.append(m)
    return out


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
        home: "AgentHome | None" = None,
        permissions: "AgentPermissions | None" = None,
    ) -> None:
        from .._loom_bridge import AgentHandlers

        self._nexus_provider = provider
        self._registry = registry
        self._trace = trace
        self._provider_registry = provider_registry
        self._nexus_cfg = nexus_cfg
        self._home = home
        self._permissions = permissions
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
            home=self._home,
            permissions=self._permissions,
        )
        # SessionStore is wired in by app.py so the streaming loop can
        # update the parked-tool-call snapshot used by ask_user_tool when
        # it parks a request. None during tests that drive the agent
        # directly without a server.
        self._sessions: Any | None = None

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
        # Terminal tool is wired separately by app.py — it depends on the
        # session_store's HitlBroker, not on AskUserHandler directly. No
        # auto-wire here.

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

    @property
    def _notify_user_handler(self) -> Any:
        return self._handlers.notify_user

    @_notify_user_handler.setter
    def _notify_user_handler(self, value: Any) -> None:
        self._handlers.notify_user = value

    def _context_window_for(self, model_id: str | None) -> int:
        cfg = self._nexus_cfg
        if cfg and model_id:
            for m in getattr(cfg, "models", []) or []:
                if getattr(m, "id", None) == model_id:
                    return int(getattr(m, "context_window", 0) or 0)
            fallback = known_context_window(model_id)
            if fallback > 0:
                return fallback
        return 0

    def _log_llm_error(
        self,
        *,
        session_id: str | None,
        error_type: str,
        message: str | None = None,
        retryable: bool = False,
        retry_attempt: int | None = None,
        model_id: str | None = None,
        tokens_est: int | None = None,
        ctx_window: int | None = None,
    ) -> None:
        if self._sessions is None or not session_id:
            return
        try:
            self._sessions.log_error(
                session_id,
                error_type,
                message=message,
                model=model_id,
                retryable=retryable,
                retry_attempt=retry_attempt,
                tokens_in=tokens_est,
                context_window=ctx_window,
            )
        except Exception:  # noqa: BLE001
            pass

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
        attachments: list[ContentPart] | None = None,
    ) -> AgentTurn:
        """Execute a complete turn in blocking mode and return the result.

        Args:
            user_message: The user's message for this turn.
            history: Prior message history for the session.
            context: Optional additional context injected into the turn.
            model_id: Force a specific model; None uses the configured default.
            attachments: Optional non-text parts (image/audio/document) to send
                alongside ``user_message``. When present, the user message is
                built as a multipart ``content`` list rather than a plain
                string. Multimodal-capable models receive them natively;
                others get a transcribed/extracted text breadcrumb at encode
                time (see ``materialize_messages`` in ``multimodal.py``).

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
            loom_messages = [
                _to_loom_message(m) for m in _strip_dead_placeholders(history)
            ]

        # Annotate terse yes/no using loom agent's pending question
        pending = self._loom._pending_question
        annotated = _annotate_short_reply(user_message, pending)
        user_msg = _build_user_message(annotated or user_message, attachments)
        loom_messages.append(_to_loom_message(user_msg))

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
        attachments: list[ContentPart] | None = None,
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
        stripped_history: list[ChatMessage] = []
        if history:
            stripped_history = _strip_dead_placeholders(history)

        ctx_window = self._context_window_for(model_id or self._chosen_model)
        effective_window = ctx_window if ctx_window > 0 else _DEFAULT_FALLBACK_WINDOW

        loom_messages = [_to_loom_message(m) for m in stripped_history]

        from ..context import CURRENT_HISTORY, CURRENT_CONTEXT_WINDOW
        CURRENT_HISTORY.set(stripped_history)
        CURRENT_CONTEXT_WINDOW.set(effective_window)

        pending = self._loom._pending_question
        annotated = _annotate_short_reply(user_message, pending)
        user_msg_text = annotated or user_message
        user_msg = _build_user_message(user_msg_text, attachments)
        user_msg_content = user_msg.content
        loom_messages.append(_to_loom_message(user_msg))

        # Pre-flight overflow check — refuse the turn now if the request can't
        # fit in the chosen model's context window. Without this, providers
        # like z.ai accept the request and reply with HTTP 200 + empty content,
        # which surfaces as the generic "empty_response" error and triggers an
        # endless retry loop.
        ctx_window = self._context_window_for(model_id or self._chosen_model)
        check = check_overflow(loom_messages, context_window=ctx_window)
        if check.overflowed:
            self._log_llm_error(
                session_id=session_id,
                error_type="context_overflow",
                message=check.detail,
                model_id=model_id,
                tokens_est=check.estimated_input_tokens,
                ctx_window=check.context_window,
            )
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
        # Tracks whether loom emitted a structured ErrorEvent earlier in this
        # turn. When it did (e.g. upstream auth failure → classify_api_error
        # → ErrorEvent → DoneEvent(iterations=0)), the subsequent done frame
        # would otherwise hit our empty_reply check and synthesize a generic
        # "empty_response" error that overwrites the real one in the UI.
        # Setting this flag on the error branch lets the done branch skip
        # the synthetic emission.
        _saw_loom_error = False
        # Snapshot inbound history so we can rebuild the persisted message list
        # for app.py's `store.replace_history`. loom's streaming DoneEvent does
        # not carry the assembled message list.
        _history_snapshot = list(history or [])

        # Mirror of loom's all_messages for the parking flow. We only need this
        # when ask_user_tool parks: the snapshot up through the ASSISTANT
        # message that issued the parked tool_call is what we persist as
        # parked_messages_json so a later resume turn can re-enter loom from
        # exactly that history. Built incrementally as events stream:
        #   content_delta + tool_call_delta accumulate into pending state →
        #   materialised on the first tool_exec_start of an LLM iteration →
        #   tool_exec_result appends a TOOL message (skipped when parking).
        working_messages: list[lt.ChatMessage] = list(loom_messages)
        pending_content_chunks: list[str] = []
        pending_tcs: dict[int, dict[str, str]] = {}
        materialised_for_iter = False
        # Map (event-emitted) tool_call_id → name so tool_exec_result can
        # append a TOOL message with the right id even though loom's
        # ToolExecResultEvent carries it directly anyway. Kept defensively.
        _tc_id_by_index: dict[int, str] = {}
        # Last tool_call_id emitted in tool_exec_start, so tool_exec_result
        # can append the matching TOOL message even when loom's payload
        # somehow drops it (older serialisers). Updated per dispatch.
        last_tool_exec_id: str | None = None
        last_tool_exec_name: str | None = None

        # Auto-retry on retryable mid-stream errors (peer-closed connections,
        # 429 rate limits, transient 5xx, "empty response" from a flaky
        # provider). Loom emits ErrorEvent + DoneEvent and ends the iterator;
        # without this layer the UI would surface a "Retry" banner that the
        # user has to click. Instead we silently restart the loom run from
        # ``working_messages`` (which already mirrors loom's all_messages),
        # so every completed tool call in this turn is preserved and not
        # replayed. We only auto-retry when no visible content has been
        # streamed for the current LLM iteration — restarting after partial
        # text would duplicate tokens in the UI.
        _MAX_LOOM_RETRIES = 3
        _LOOM_RETRY_BACKOFFS = (2.0, 5.0, 12.0)
        _loom_retry_attempts = 0
        _delta_emitted_this_iter = False

        def _materialise_assistant_if_needed() -> None:
            nonlocal materialised_for_iter
            if materialised_for_iter:
                return
            tcs: list[lt.ToolCall] = []
            for idx in sorted(pending_tcs.keys()):
                p = pending_tcs[idx]
                tcs.append(
                    lt.ToolCall(
                        id=p.get("id") or f"tc_{idx}",
                        name=p.get("name") or "",
                        arguments=p.get("arguments") or "",
                    )
                )
            content = "".join(pending_content_chunks) or None
            working_messages.append(
                lt.ChatMessage(
                    role=lt.Role.ASSISTANT,
                    content=content,
                    tool_calls=tcs or None,
                )
            )
            materialised_for_iter = True

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

        # Trace what we're handing to loom — pinpoints "iters=0" cases
        # (loom never calls the LLM) versus genuine empty-content cases
        # (LLM was called but returned ""). model_id here is what the
        # registry lookup is keyed on.
        log.warning(
            "Agent.run_turn_stream → loom: model_id=%s msgs=%d (system=%d, user=%d, assistant=%d, tool=%d) ctx_window=%d",
            model_id, len(loom_messages),
            sum(1 for m in loom_messages if m.role == lt.Role.SYSTEM),
            sum(1 for m in loom_messages if m.role == lt.Role.USER),
            sum(1 for m in loom_messages if m.role == lt.Role.ASSISTANT),
            sum(1 for m in loom_messages if m.role == lt.Role.TOOL),
            ctx_window,
        )
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

            # Trace every loom event so we can pinpoint silent-completion
            # bugs (e.g. provider stream yields nothing, loom emits done
            # with iters=0). Emit at WARNING because Python's default
            # logger gates at WARNING and we don't configure basicConfig.
            if etype == "error":
                # For errors, log the full classified payload — message +
                # reason + status_code is exactly what we need to diagnose
                # silent failures.
                log.warning(
                    "loom event: type=error reason=%r status=%r retryable=%r message=%r",
                    ev.get("reason"),
                    ev.get("status_code"),
                    ev.get("retryable"),
                    (ev.get("message") or "")[:300],
                )
            elif etype == "done":
                log.warning(
                    "loom event: type=done iters=%s model=%s in_tokens=%s out_tokens=%s stop_reason=%s",
                    ev.get("iterations"),
                    ev.get("model"),
                    ev.get("input_tokens"),
                    ev.get("output_tokens"),
                    ev.get("stop_reason"),
                )
            else:
                log.warning(
                    "loom event: type=%s keys=%s",
                    etype,
                    sorted(ev.keys())[:8] if isinstance(ev, dict) else "?",
                )

            if etype == "content_delta":
                delta = ev.get("delta", "")
                full_text += delta
                pending_content_chunks.append(delta)
                materialised_for_iter = False
                _delta_emitted_this_iter = True
                # Mirror to the trace bus so /chat/{sid}/events subscribers
                # (e.g. CardActivityModal) can render typing live, not only
                # after the post-turn `reply` event.
                self._on_event("delta", {"text": delta})
                yield {"type": "delta", "text": delta}

            elif etype == "tool_call_delta":
                idx = ev.get("index")
                if isinstance(idx, int):
                    slot = pending_tcs.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""},
                    )
                    if ev.get("id"):
                        slot["id"] = ev["id"]
                        _tc_id_by_index[idx] = ev["id"]
                    if ev.get("name"):
                        slot["name"] = ev["name"]
                    args_delta = ev.get("arguments_delta")
                    if args_delta:
                        slot["arguments"] = (slot.get("arguments") or "") + args_delta
                    materialised_for_iter = False
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
                tool_call_id = ev.get("tool_call_id") or ""
                last_tool_exec_id = tool_call_id or None
                last_tool_exec_name = tool_name or None
                # Materialise the assistant message that issued this tool
                # call before dispatch so a parking persist later has the
                # correct snapshot.
                _materialise_assistant_if_needed()
                # Make tool_call_id visible to ask_user_tool through the
                # session store. Sequential within a turn — no race.
                if self._sessions is not None and session_id and tool_call_id:
                    try:
                        self._sessions.set_pending_tool_call(
                            session_id, tool_call_id,
                        )
                        self._sessions.set_messages_snapshot(
                            session_id,
                            [m.model_dump() for m in working_messages],
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("set_pending_tool_call failed")
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
                result_text = ev.get("text") or ""
                preview = result_text[:200]
                # Detect the parked sentinel BEFORE appending a TOOL message
                # to working_messages. We don't want the sentinel to land in
                # persisted history — it isn't a real tool result.
                parked_request_id = parse_parked_sentinel(result_text)
                if parked_request_id:
                    # Snapshot the messages up through the assistant tool_call
                    # (NOT the sentinel TOOL message — that's intentional) and
                    # write it onto the parked row so a resume turn can
                    # re-enter loom from the same history.
                    snapshot_dump = [
                        m.model_dump() for m in working_messages
                    ]
                    if self._sessions is not None:
                        try:
                            self._sessions.update_hitl_pending_snapshot(
                                parked_request_id,
                                json.dumps(snapshot_dump, ensure_ascii=False),
                                model_id=self._chosen_model,
                            )
                        except Exception:  # noqa: BLE001
                            log.exception(
                                "parked snapshot persist failed for %s",
                                parked_request_id,
                            )
                        try:
                            self._sessions.clear_pending_tool_call(session_id or "")
                            self._sessions.clear_messages_snapshot(session_id or "")
                        except Exception:  # noqa: BLE001
                            pass
                    self._on_event(
                        "parked",
                        {"request_id": parked_request_id},
                    )
                    yield {
                        "type": "parked",
                        "request_id": parked_request_id,
                        "session_id": session_id,
                    }
                    # Persist whatever assistant prefix has streamed so far
                    # so the UI shows the partial turn (e.g., "give me a
                    # moment while I check…" before the form).
                    persisted_messages = (
                        _history_snapshot
                        + [ChatMessage(role=Role.USER, content=user_msg_content)]
                        + (
                            [ChatMessage(role=Role.ASSISTANT, content=full_text)]
                            if full_text
                            else []
                        )
                    )
                    yield {
                        "type": "done",
                        "session_id": session_id,
                        "reply": full_text,
                        "trace": list(self._turn_trace),
                        "skills_touched": list(self._skills_touched),
                        "iterations": 0,
                        "messages": persisted_messages,
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "tool_calls": 0,
                            "model": self._chosen_model,
                        },
                        "parked_request_id": parked_request_id,
                    }
                    # Stop consuming loom_iter — finally-block cancels the
                    # outstanding loom_task so loom never appends the
                    # sentinel TOOL message and never makes another LLM call.
                    return
                # Normal flow: append a TOOL message mirroring loom's append
                # at loop.py:756 so working_messages stays in sync.
                tcid = ev.get("tool_call_id") or last_tool_exec_id or ""
                tc_name = (
                    ev.get("name") or last_tool_exec_name or tool_name
                )
                working_messages.append(
                    lt.ChatMessage(
                        role=lt.Role.TOOL,
                        content=result_text,
                        tool_call_id=tcid,
                        name=tc_name,
                    )
                )
                # Reset per-iteration accumulators so the NEXT LLM call
                # starts with a fresh content/tcs buffer.
                pending_content_chunks.clear()
                pending_tcs.clear()
                materialised_for_iter = False
                # A tool result completed an LLM iteration successfully;
                # the next iteration starts fresh, so visible-content
                # tracking and the retry budget both reset.
                _delta_emitted_this_iter = False
                _loom_retry_attempts = 0
                self._on_event("tool_result", {"name": tool_name, "preview": preview})
                yield {
                    "type": "tool_exec_result",
                    "name": tool_name,
                    "result_preview": preview,
                }
                # Loom now owns the intra-loop overflow check (it runs at
                # the top of every iteration with full visibility into the
                # system prompt that the wrapper never sees). The wrapper
                # just listens for the resulting `context_overflow` event
                # below and forwards it to the UI's existing banner path.

            elif etype == "limit_reached":
                yield {"type": "limit_reached", "iterations": ev.get("iterations", 0)}

            elif etype == "context_overflow":
                self._log_llm_error(
                    session_id=session_id,
                    error_type="context_overflow",
                    message=ev.get("message", "context overflow"),
                    model_id=model_id,
                    tokens_est=ev.get("estimated_input_tokens", 0),
                    ctx_window=ev.get("context_window", 0),
                )
                yield {
                    "type": "error",
                    "detail": ev.get("message", "context overflow"),
                    "reason": "context_overflow",
                    "retryable": False,
                    "status_code": None,
                    "estimated_input_tokens": ev.get("estimated_input_tokens", 0),
                    "context_window": ev.get("context_window", 0),
                    "actions": ["compact_history", "new_session"],
                }

            elif etype == "error":
                # Auto-recover from transient mid-stream failures (peer-closed
                # connections, 429 rate limits, transient 5xx, "empty
                # response" from a flaky provider). When loom flags the
                # error retryable AND nothing visible has been streamed for
                # this LLM iteration AND we still have retry budget, swallow
                # the error+done frames, sleep, and restart loom from
                # ``working_messages`` (which already has every successful
                # tool result this turn — no replay). The user sees a brief
                # pause instead of a "Retry" banner.
                retryable = bool(ev.get("retryable", False))
                if (
                    retryable
                    and not _delta_emitted_this_iter
                    and _loom_retry_attempts < _MAX_LOOM_RETRIES
                ):
                    # Drain the loom DoneEvent that follows every ErrorEvent
                    # before we swap iterators. ``loom_task`` is already
                    # awaiting the next __anext__ result (line above), which
                    # is the done frame. We discard it; the new loom run
                    # will emit its own done when it finishes.
                    try:
                        await loom_task  # consume the trailing done event
                    except StopAsyncIteration:
                        pass
                    except Exception:  # noqa: BLE001 — best-effort drain
                        pass
                    delay = _LOOM_RETRY_BACKOFFS[
                        min(_loom_retry_attempts, len(_LOOM_RETRY_BACKOFFS) - 1)
                    ]
                    log.warning(
                        "loom auto-retry: attempt=%d/%d delay=%.1fs reason=%r "
                        "(message=%r)",
                        _loom_retry_attempts + 1, _MAX_LOOM_RETRIES,
                        delay, ev.get("reason"),
                        (ev.get("message") or "")[:200],
                    )
                    # Surface the backoff so the UI can render a small
                    # "Reconnecting…" hint instead of leaving the user
                    # staring at a frozen bubble.
                    yield {
                        "type": "reconnecting",
                        "attempt": _loom_retry_attempts + 1,
                        "max_attempts": _MAX_LOOM_RETRIES,
                        "delay_seconds": delay,
                        "reason": ev.get("reason") or "",
                    }
                    await asyncio.sleep(delay)
                    _loom_retry_attempts += 1
                    # Reset per-iteration state from the failed attempt so
                    # the new loom run doesn't see stale tool_call_delta or
                    # content_delta fragments.
                    pending_content_chunks.clear()
                    pending_tcs.clear()
                    _tc_id_by_index.clear()
                    materialised_for_iter = False
                    _delta_emitted_this_iter = False
                    # Restart loom from the working_messages we've already
                    # accumulated — every successful tool call/result is
                    # there, so no work is replayed.
                    loom_iter = self._loom.run_turn_stream(
                        working_messages, model_id=model_id
                    ).__aiter__()
                    loom_task = asyncio.ensure_future(loom_iter.__anext__())
                    continue
                # Either non-retryable, content already streamed, or budget
                # exhausted — surface the error as before. _saw_loom_error
                # suppresses the done branch's empty-reply synthesis so the
                # UI's applyErrorEvent (last error wins) keeps loom's
                # classified message instead of a generic banner.
                _saw_loom_error = True
                self._log_llm_error(
                    session_id=session_id,
                    error_type=ev.get("reason") or "llm_error",
                    message=(ev.get("message") or "")[:2000],
                    retryable=retryable,
                    retry_attempt=_loom_retry_attempts if _loom_retry_attempts > 0 else None,
                    model_id=model_id,
                    tokens_est=check_overflow(loom_messages, context_window=ctx_window or 0).estimated_input_tokens if ctx_window > 0 else None,
                    ctx_window=ctx_window,
                )
                error_reason = ev.get("reason") or ""
                if error_reason == "rate_limit" and session_id and self._sessions is not None:
                    try:
                        from datetime import datetime, timezone, timedelta
                        cooldown = 60
                        retry_after = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
                        wm_json = json.dumps(
                            [{"role": m.role.value if hasattr(m.role, "value") else str(m.role),
                              "content": m.content if isinstance(m.content, str) else json.dumps(m.content, default=str),
                              "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in (m.tool_calls or [])] if m.tool_calls else None,
                              "tool_call_id": m.tool_call_id, "name": m.name}
                             for m in working_messages],
                            default=str,
                        )
                        self._sessions.pause_turn(
                            session_id,
                            user_message=user_msg_content,
                            working_messages_json=wm_json,
                            retry_after_iso=retry_after.isoformat(),
                            model_id=model_id,
                            error_detail=(ev.get("message") or "")[:500],
                        )
                        yield {
                            "type": "paused_for_cooldown",
                            "retry_after": retry_after.isoformat(),
                            "estimated_seconds": cooldown,
                            "reason": error_reason,
                        }
                    except Exception:
                        log.debug("failed to persist paused turn", exc_info=True)
                yield {
                    "type": "error",
                    "detail": ev.get("message", ""),
                    "reason": ev.get("reason"),
                    "retryable": retryable,
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
                    # WARNING (not INFO) so this surfaces in the daemon log
                    # under default Python logging — Nexus doesn't configure
                    # a level, so INFO is swallowed by the root WARNING gate.
                    log.warning(
                        "turn ended truncated (model=%s, stop_reason=length, reply_len=%d)",
                        model_used, len(reply_text),
                    )
                    yield {
                        "type": "error",
                        "detail": "Response was truncated — the model hit its output limit.",
                        "reason": "length",
                        "retryable": True,
                        "status_code": None,
                    }
                elif not reply_text and stop_reason not in ("tool_use",) and not _saw_loom_error:
                    # WARNING so this lands in the daemon log under default
                    # Python logging (no basicConfig is set anywhere, so the
                    # root logger gates at WARNING). Include model + iteration
                    # count + token usage so debugging "why did the model
                    # return nothing" doesn't require code-side prints.
                    #
                    # The ``_saw_loom_error`` guard skips synthesis when
                    # loom already emitted an ErrorEvent earlier — its
                    # classified message (auth, transport, rate-limit, …)
                    # is the truth and we shouldn't paper over it with a
                    # generic "empty response" banner.
                    usage_in = ev.get("input_tokens") or 0
                    usage_out = ev.get("output_tokens") or 0
                    log.warning(
                        "turn ended with empty reply "
                        "(model=%s, stop_reason=%s, iters=%s, in_tokens=%s, out_tokens=%s)",
                        model_used, stop_reason, ev.get("iterations"),
                        usage_in, usage_out,
                    )
                    self._log_llm_error(
                        session_id=session_id,
                        error_type="empty_response",
                        message=f"stop_reason={stop_reason} iters={ev.get('iterations')} in={usage_in} out={usage_out}",
                        model_id=model_id,
                        tokens_est=usage_in,
                        ctx_window=ctx_window,
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

    async def continue_after_hitl(
        self,
        *,
        session_id: str,
        request_id: str,
        answer: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Resume a parked turn after the user answers via the bell or push.

        Loads the snapshot persisted at park time, appends a TOOL message
        carrying the answer (matching the original tool_call_id), and drives
        loom's stream from that history. Loom is stateless w.r.t. its
        ``run_turn_stream`` entry-point, so handing it a history that ends
        in TOOL is enough to make it call the LLM again and continue.
        """
        if self._sessions is None:
            raise RuntimeError("continue_after_hitl: session_store not wired")
        row = self._sessions.get_hitl_pending(request_id)
        if row is None:
            raise LookupError(f"no parked request: {request_id!r}")

        try:
            raw_snapshot = json.loads(row.get("parked_messages_json") or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"corrupt parked snapshot for {request_id!r}"
            ) from exc

        # Refuse to resume from an empty snapshot. Without preceding context
        # (at minimum the assistant message with the tool_call) loom would
        # be handed a bare TOOL message — most providers reject that, and
        # the few that don't can't produce a coherent continuation. Empty
        # snapshots historically meant the parking-sentinel detection in
        # the agent loop was bypassed; surfacing the error early gives the
        # caller a chance to recover instead of silently losing the answer.
        if not raw_snapshot:
            raise RuntimeError(
                f"parked snapshot for {request_id!r} is empty — cannot resume"
            )

        loom_messages: list[lt.ChatMessage] = []
        for m in raw_snapshot:
            try:
                loom_messages.append(lt.ChatMessage(**m))
            except Exception:  # noqa: BLE001
                # A row that pre-dates a schema change shouldn't crash the
                # whole resume — drop unknown fields and keep going.
                loom_messages.append(
                    lt.ChatMessage(
                        role=lt.Role(m.get("role", "user")),
                        content=m.get("content"),
                    )
                )

        # Build the TOOL message answering the parked tool_call. The answer
        # is serialised to a string the LLM can consume. Forms come in as
        # dicts; everything else as a plain string.
        if isinstance(answer, str):
            tool_content = answer
        else:
            try:
                tool_content = json.dumps(answer, ensure_ascii=False)
            except (TypeError, ValueError):
                tool_content = str(answer)
        loom_messages.append(
            lt.ChatMessage(
                role=lt.Role.TOOL,
                content=tool_content,
                tool_call_id=row["tool_call_id"],
                name="ask_user",
            )
        )

        # Re-derive _pending_question from the most recent ASSISTANT text in
        # the snapshot so terse follow-up replies in the next user turn are
        # still annotated correctly.
        last_assistant = next(
            (m for m in reversed(loom_messages) if m.role == lt.Role.ASSISTANT),
            None,
        )
        if last_assistant and last_assistant.content:
            from .helpers import _extract_pending_question
            self._loom._pending_question = _extract_pending_question(
                last_assistant.content
            )
        else:
            self._loom._pending_question = None

        self._turn_trace = []
        self._skills_touched = []
        self._chosen_model = row.get("model_id") or self._chosen_model

        full_text = ""
        history_snapshot = [_from_loom_message(m) for m in loom_messages[:-1]]

        async for raw in self._loom.run_turn_stream(
            loom_messages, model_id=self._chosen_model,
        ):
            etype = raw.get("type") if isinstance(raw, dict) else getattr(raw, "type", None)
            ev = raw if isinstance(raw, dict) else raw.model_dump()

            if etype == "content_delta":
                delta = ev.get("delta", "")
                full_text += delta
                self._on_event("delta", {"text": delta})
                yield {"type": "delta", "text": delta}
            elif etype == "tool_call_delta":
                yield {
                    "type": "tool_call_delta",
                    "index": ev.get("index"),
                    "id": ev.get("id"),
                    "name": ev.get("name"),
                    "args_delta": ev.get("arguments_delta"),
                }
            elif etype == "tool_exec_start":
                tool_name = ev.get("name", "")
                self._on_event(
                    "tool_call",
                    {"name": tool_name, "args": ev.get("arguments", "")},
                )
                yield {
                    "type": "tool_exec_start",
                    "name": tool_name,
                    "args": ev.get("arguments", ""),
                }
            elif etype == "tool_exec_result":
                tool_name = ev.get("name", "")
                preview = (ev.get("text") or "")[:200]
                self._on_event(
                    "tool_result", {"name": tool_name, "preview": preview},
                )
                yield {
                    "type": "tool_exec_result",
                    "name": tool_name,
                    "result_preview": preview,
                }
            elif etype == "limit_reached":
                yield {
                    "type": "limit_reached",
                    "iterations": ev.get("iterations", 0),
                }
            elif etype == "error":
                yield {
                    "type": "error",
                    "detail": ev.get("message", ""),
                    "reason": ev.get("reason"),
                    "retryable": ev.get("retryable", False),
                    "status_code": ev.get("status_code"),
                }
            elif etype == "done":
                ctx = ev.get("context") or {}
                model_used = ev.get("model") or self._chosen_model
                loom_msgs = ctx.get("messages")
                if loom_msgs:
                    persisted_messages = [
                        _from_loom_message(lt.ChatMessage(**m))
                        for m in loom_msgs
                        if m.get("role") != "system"
                    ]
                else:
                    persisted_messages = history_snapshot + [
                        ChatMessage(role=Role.ASSISTANT, content=full_text),
                    ]
                yield {
                    "type": "done",
                    "session_id": session_id,
                    "reply": full_text,
                    "trace": list(self._turn_trace),
                    "skills_touched": ev.get("skills_touched")
                    or list(self._skills_touched),
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
