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
from .budget import check_tool_budget
from .reasoning import ReasoningTracker
from .retry import RetryManager
from .stream_translator import StreamTranslator

if TYPE_CHECKING:
    from loom.home import AgentHome
    from loom.permissions import AgentPermissions

log = logging.getLogger(__name__)

TraceCallback = Callable[[str, dict[str, Any]], None]

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


def _sanitize_tool_pairs(history: list[ChatMessage]) -> list[ChatMessage]:
    """Ensure every assistant(tool_calls) is immediately followed by tool results.

    For each assistant message with tool_calls, checks that the very next
    messages in the list are tool results covering every tool_call_id.
    Inserts synthetic results for any missing ones.  Also drops tool messages
    that appear without a preceding assistant that owns their tool_call_id.
    """
    out: list[ChatMessage] = []
    needs_repair = False
    for i, m in enumerate(history):
        if m.role == Role.ASSISTANT and m.tool_calls:
            pending = {tc.id for tc in m.tool_calls if tc.id}
            if not pending:
                out.append(m)
                continue
            answered_here: set[str] = set()
            j = i + 1
            while j < len(history) and history[j].role == Role.TOOL:
                answered_here.add(history[j].tool_call_id or "")
                j += 1
            unanswered = pending - answered_here
            if unanswered:
                needs_repair = True
            out.append(m)
            for tc in m.tool_calls:
                if tc.id in unanswered:
                    out.append(ChatMessage(
                        role=Role.TOOL,
                        content="[Tool result unavailable — interrupted before execution]",
                        tool_call_id=tc.id,
                        name=tc.name or "tool",
                    ))
        elif m.role == Role.TOOL and m.tool_call_id:
            has_parent = False
            for prev in reversed(out):
                if prev.role == Role.ASSISTANT and prev.tool_calls:
                    if any(tc.id == m.tool_call_id for tc in prev.tool_calls):
                        has_parent = True
                    break
                if prev.role not in (Role.TOOL,):
                    break
            if not has_parent:
                needs_repair = True
                continue
            out.append(m)
        else:
            out.append(m)

    return out if needs_repair else history


def _sanitize_loom_tool_pairs(msgs: list[lt.ChatMessage]) -> list[lt.ChatMessage]:
    """Loom-message variant of _sanitize_tool_pairs.

    Ensures every assistant(tool_calls) in *msgs* is followed by matching
    tool responses before the list is sent to a new loom iteration or retry.
    Inserts synthetic tool results for unanswered tool_call_ids and drops
    orphaned tool messages.  Returns the original list when no repair is
    needed, otherwise returns a repaired copy.
    """
    out: list[lt.ChatMessage] = []
    needs_repair = False
    for i, m in enumerate(msgs):
        if m.role == lt.Role.ASSISTANT and m.tool_calls:
            pending = {tc.id for tc in m.tool_calls if tc.id}
            if not pending:
                out.append(m)
                continue
            answered: set[str] = set()
            j = i + 1
            while j < len(msgs) and msgs[j].role == lt.Role.TOOL:
                answered.add(msgs[j].tool_call_id or "")
                j += 1
            unanswered = pending - answered
            if unanswered:
                needs_repair = True
            out.append(m)
            for tc in m.tool_calls:
                if tc.id in unanswered:
                    out.append(lt.ChatMessage(
                        role=lt.Role.TOOL,
                        content="[Tool result unavailable — interrupted before execution]",
                        tool_call_id=tc.id,
                        name=tc.name or "tool",
                    ))
        elif m.role == lt.Role.TOOL and m.tool_call_id:
            has_parent = False
            for prev in reversed(out):
                if prev.role == lt.Role.ASSISTANT and prev.tool_calls:
                    if any(tc.id == m.tool_call_id for tc in prev.tool_calls):
                        has_parent = True
                    break
                if prev.role != lt.Role.TOOL:
                    break
            if not has_parent:
                needs_repair = True
                continue
            out.append(m)
        else:
            out.append(m)
    return out if needs_repair else msgs


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
        self._handlers = AgentHandlers(ask_user=ask_user_handler)
        self._turn_trace: list[dict[str, Any]] = []
        self._skills_touched: list[str] = []
        self._chosen_model: str | None = None

        self._loom = build_loom_agent(
            nexus_provider=self._nexus_provider,
            registry=self._registry,
            handlers=self._handlers,
            provider_registry=self._provider_registry,
            get_nexus_cfg=lambda: self._nexus_cfg,
            get_chosen_model=lambda: self._chosen_model,
            get_turn_trace=lambda: self._turn_trace,
            on_trace_event=self._on_event,
            home=self._home,
            permissions=self._permissions,
        )
        self._loom._build_tools = self._filtered_tools
        self._sessions: Any | None = None

    def _on_event(self, kind: str, payload: dict[str, Any]) -> None:
        entry = {"event": kind, **payload}
        self._turn_trace.append(entry)
        if self._trace:
            self._trace(kind, payload)

    def _filtered_tools(self) -> list:
        from ..context import ALLOWED_TOOLS
        all_tools = self._loom._tools.specs()
        allowed = ALLOWED_TOOLS.get(None)
        if allowed is None:
            return all_tools
        return [t for t in all_tools if t.name in allowed]

    @property
    def _ask_user_handler(self) -> AskUserHandler | None:
        return self._handlers.ask_user

    @_ask_user_handler.setter
    def _ask_user_handler(self, value: AskUserHandler | None) -> None:
        self._handlers.ask_user = value

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
        resolved = model_id or getattr(getattr(cfg, "agent", None), "default_model", None)
        if cfg and resolved:
            for m in getattr(cfg, "models", []) or []:
                if getattr(m, "id", None) == resolved:
                    cw = int(getattr(m, "context_window", 0) or 0)
                    if cw > 0:
                        return cw
            fallback = known_context_window(resolved)
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

    @staticmethod
    def _rebuild_loom_messages(
        nexus_msgs: list[ChatMessage],
        orig_loom_msgs: list[lt.ChatMessage],
    ) -> list[lt.ChatMessage]:
        from .._loom_bridge.message import _nexus_to_loom_message
        out: list[lt.ChatMessage] = []
        for nm, orig_lm in zip(nexus_msgs, orig_loom_msgs):
            lm = _nexus_to_loom_message(nm)
            rc = getattr(orig_lm, '_reasoning_content', None)
            if rc:
                lm._reasoning_content = rc  # type: ignore[attr-defined]
            out.append(lm)
        if len(nexus_msgs) > len(orig_loom_msgs):
            from .._loom_bridge.message import _nexus_to_loom_message as _conv
            for nm in nexus_msgs[len(orig_loom_msgs):]:
                out.append(_conv(nm))
        return out

    async def run_turn(
        self,
        user_message: str,
        *,
        history: list[ChatMessage] | None = None,
        context: str | None = None,
        model_id: str | None = None,
        attachments: list[ContentPart] | None = None,
    ) -> AgentTurn:
        self._turn_trace = []
        self._skills_touched = []
        self._chosen_model = model_id

        loom_messages: list[lt.ChatMessage] = []
        stripped: list[ChatMessage] = []
        if history:
            stripped = _strip_dead_placeholders(history)
            loom_messages = [_to_loom_message(m) for m in stripped]
            for nm, lm in zip(stripped, loom_messages):
                if nm.role == Role.ASSISTANT and nm.reasoning_content:
                    lm._reasoning_content = nm.reasoning_content  # type: ignore[attr-defined]

        pending = self._loom._pending_question
        annotated = _annotate_short_reply(user_message, pending)
        user_msg = _build_user_message(annotated or user_message, attachments)
        loom_messages.append(_to_loom_message(user_msg))

        loom_turn = await self._loom.run_turn(loom_messages, model_id=model_id)

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
        resume_working_messages: list[lt.ChatMessage] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._turn_trace = []
        self._skills_touched = []
        self._chosen_model = model_id

        loom_messages: list[lt.ChatMessage] = []
        stripped_history: list[ChatMessage] = []
        if resume_working_messages is not None:
            loom_messages = list(resume_working_messages)
            stripped_history = [_from_loom_message(m) for m in resume_working_messages]
        elif history:
            stripped_history = _sanitize_tool_pairs(_strip_dead_placeholders(history))

        ctx_window = self._context_window_for(model_id or self._chosen_model)
        effective_window = ctx_window if ctx_window > 0 else _DEFAULT_FALLBACK_WINDOW

        loom_messages = [_to_loom_message(m) for m in stripped_history]
        for nm, lm in zip(stripped_history, loom_messages):
            if nm.role == Role.ASSISTANT and nm.reasoning_content:
                lm._reasoning_content = nm.reasoning_content  # type: ignore[attr-defined]

        from ..context import CURRENT_HISTORY, CURRENT_CONTEXT_WINDOW, TOOL_BUDGET_EXCEEDED
        CURRENT_HISTORY.set(stripped_history)
        CURRENT_CONTEXT_WINDOW.set(effective_window)
        TOOL_BUDGET_EXCEEDED.set(False)
        _tool_budget = 0
        _scrape_call_limit = 0
        _session_tool_budget = 0
        if self._nexus_cfg:
            agent_cfg = getattr(self._nexus_cfg, "agent", None)
            if agent_cfg:
                _tool_budget = int(getattr(agent_cfg, "tool_budget_tokens", 0) or 0)
                _session_tool_budget = int(getattr(agent_cfg, "session_tool_budget_tokens", 0) or 0)
            scrape_cfg = getattr(self._nexus_cfg, "scrape", None)
            if scrape_cfg:
                _scrape_call_limit = int(getattr(scrape_cfg, "max_scrape_calls", 0) or 0)

        pending = self._loom._pending_question
        annotated = _annotate_short_reply(user_message, pending)
        user_msg_text = annotated or user_message
        user_msg = _build_user_message(user_msg_text, attachments)
        user_msg_content = user_msg.content
        if resume_working_messages is None:
            loom_messages.append(_to_loom_message(user_msg))

        _auto_compacted_history: list[ChatMessage] | None = None
        if _session_tool_budget > 0 and stripped_history:
            from .budget import estimate_session_tool_tokens
            _session_tool_tok = estimate_session_tool_tokens(stripped_history)
            if _session_tool_tok > _session_tool_budget:
                log.info(
                    "Cross-turn tool budget exceeded: %dK > %dK tokens — triggering compact_and_summarize",
                    _session_tool_tok // 1024, _session_tool_budget // 1024,
                )
                from .compact import compact_and_summarize
                compacted, _cs_report = await compact_and_summarize(
                    stripped_history,
                    context_window=effective_window,
                    session_id=session_id,
                    model_id=model_id or self._chosen_model,
                    provider=self._nexus_provider,
                    strategy="auto",
                )
                if _cs_report.compact_report.compacted > 0 or _cs_report.summarized:
                    log.info(
                        "Cross-turn compaction: compacted=%d summarized=%s tokens %dK→%dK",
                        _cs_report.compact_report.compacted,
                        _cs_report.summarized,
                        _cs_report.tokens_before // 1024,
                        _cs_report.tokens_after // 1024,
                    )
                    stripped_history = compacted
                    _auto_compacted_history = compacted
                    loom_messages = [_to_loom_message(m) for m in compacted]
                    for nm, lm in zip(compacted, loom_messages):
                        if nm.role == Role.ASSISTANT and nm.reasoning_content:
                            lm._reasoning_content = nm.reasoning_content  # type: ignore[attr-defined]
                    loom_messages.append(_to_loom_message(user_msg))

        ctx_window = self._context_window_for(model_id or self._chosen_model)
        check = check_overflow(loom_messages, context_window=ctx_window)
        if check.estimated_input_tokens > 100_000:
            log.warning(
                "High token usage: ~%dK estimated input tokens for session %s, model %s",
                check.estimated_input_tokens // 1024, session_id, model_id,
            )
        # Overflow rescue is now delegated to loom's per-iteration
        # ``resolve_overflow`` (wired in _builder via NexusCompactor), so the
        # turn is handed to loom regardless of the pre-flight estimate. If the
        # compactor can't bring it under budget, loom emits OverflowEvent,
        # which the in-loop ``context_overflow`` branch below surfaces as the
        # SSE error (with the same actions payload this block used to emit).

        _saw_loom_error = False
        _history_snapshot = list(_auto_compacted_history if _auto_compacted_history is not None else (history or []))

        adapter = getattr(self._loom, "_provider", None)
        reasoning = ReasoningTracker()
        reasoning.hydrate_adapter_map(adapter, stripped_history)

        retry_mgr = RetryManager()
        _cumulative_tool_tokens: int = 0
        _budget_hint_injected: bool = False
        _tool_call_counts: dict[str, int] = {}
        _call_limits: dict[str, int] = {}
        if _scrape_call_limit > 0:
            _call_limits["web_scrape"] = _scrape_call_limit

        tr = StreamTranslator(
            on_event=self._on_event,
            reasoning=reasoning,
            adapter=adapter,
            history_snapshot=_history_snapshot,
            user_msg_content=user_msg_content,
            sessions=self._sessions,
            session_id=session_id,
            trace_getter=lambda: list(self._turn_trace),
            skills_touched_getter=lambda: list(self._skills_touched),
            chosen_model=model_id or self._chosen_model,
            handlers=self._handlers,
        )
        tr.working_messages = list(loom_messages)

        thinking_q: asyncio.Queue[str] = asyncio.Queue()
        had_sink_attr = adapter is not None and hasattr(adapter, "_thinking_sink")
        if had_sink_attr:
            adapter._thinking_sink = thinking_q.put_nowait  # type: ignore[attr-defined]

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

            etype = raw.get("type") if isinstance(raw, dict) else getattr(raw, "type", None)
            if isinstance(raw, dict):
                ev = raw
            else:
                ev = raw.model_dump()

            if etype == "error":
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
                log.debug(
                    "loom event: type=%s keys=%s",
                    etype,
                    sorted(ev.keys())[:8] if isinstance(ev, dict) else "?",
                )

            if etype == "content_delta":
                for sse_ev in tr.translate(ev, etype):
                    yield sse_ev
                retry_mgr.delta_emitted = True

            elif etype == "tool_call_delta":
                for sse_ev in tr.translate(ev, etype):
                    yield sse_ev

            elif etype == "tool_exec_start":
                for sse_ev in tr.translate(ev, etype):
                    yield sse_ev

            elif etype == "tool_exec_result":
                result_text = ev.get("text") or ""
                tool_name = ev.get("name", "")

                parked_request_id = parse_parked_sentinel(result_text)
                if parked_request_id:
                    for sse_ev in tr.translate(ev, etype):
                        yield sse_ev
                    return

                tcid = ev.get("tool_call_id") or tr.last_tool_exec_id or ""
                tc_name = ev.get("name") or tr.last_tool_exec_name or tool_name
                tr.working_messages.append(
                    lt.ChatMessage(
                        role=lt.Role.TOOL,
                        content=result_text,
                        tool_call_id=tcid,
                        name=tc_name,
                    )
                )

                bc = check_tool_budget(
                    _cumulative_tool_tokens, result_text,
                    budget=_tool_budget,
                    call_counts=_tool_call_counts,
                    tool_name=tool_name,
                    call_limits=_call_limits,
                )
                _cumulative_tool_tokens = bc.cumulative_tool_tokens
                if bc.exceeded and not _budget_hint_injected:
                    _budget_hint_injected = True
                    from ..context import TOOL_BUDGET_EXCEEDED
                    TOOL_BUDGET_EXCEEDED.set(True)
                    if bc.call_limit_exceeded:
                        lim_tool, lim_count = bc.call_limit_exceeded
                        log.warning(
                            "Tool call limit exceeded: %d %s calls in turn",
                            lim_count, lim_tool,
                        )
                    else:
                        log.warning(
                            "Tool budget exceeded: %d/%d tokens after %s",
                            bc.cumulative_tool_tokens, _tool_budget, tool_name,
                        )

                if ctx_window > 0 and len(tr.working_messages) > 6:
                    from .compact import compact_failed_scrapes, auto_compact
                    from .overflow import estimate_tokens, _TOOLS_AND_SYSTEM_OVERHEAD
                    from .zones import classify_zone
                    class _WM:
                        pass
                    _wm_compat = []
                    for _wm in tr.working_messages:
                        _o = _WM()
                        _o.content = _wm.content
                        _o.role = _wm.role
                        _o.tool_calls = getattr(_wm, "tool_calls", None)
                        _wm_compat.append(_o)
                    _est_tok = estimate_tokens(_wm_compat) if _wm_compat else 0
                    _zone = classify_zone(_est_tok, ctx_window, tools_overhead=_TOOLS_AND_SYSTEM_OVERHEAD)
                    _need_loom_restart = False
                    if _zone in ("yellow", "orange", "red"):
                        from .._loom_bridge.message import _loom_to_nexus_message
                        _nexus_wm = [_loom_to_nexus_message(m) for m in tr.working_messages]
                        _nexus_wm, _n_cleaned = compact_failed_scrapes(_nexus_wm)
                        if _n_cleaned > 0:
                            log.info(
                                "Mid-turn: cleaned %d failed scrape results (zone=%s, %dK tokens)",
                                _n_cleaned, _zone, _est_tok // 1024,
                            )
                            _need_loom_restart = True
                            tr.working_messages = self._rebuild_loom_messages(_nexus_wm, tr.working_messages)
                    if _zone in ("orange", "red"):
                        if not _need_loom_restart:
                            from .._loom_bridge.message import _loom_to_nexus_message
                            _nexus_wm = [_loom_to_nexus_message(m) for m in tr.working_messages]
                        _nexus_wm, _mid_report = auto_compact(_nexus_wm)
                        if _mid_report.compacted > 0:
                            log.info(
                                "Mid-turn: auto_compact compacted=%d saved=%d bytes (zone=%s)",
                                _mid_report.compacted, _mid_report.saved_bytes, _zone,
                            )
                            _need_loom_restart = True
                            tr.working_messages = self._rebuild_loom_messages(_nexus_wm, tr.working_messages)
                    if _need_loom_restart:
                        tr.working_messages = _sanitize_loom_tool_pairs(tr.working_messages)
                        tr.reset_iteration()
                        loom_iter = self._loom.run_turn_stream(
                            tr.working_messages, model_id=model_id
                        ).__aiter__()
                        loom_task = asyncio.ensure_future(loom_iter.__anext__())
                        continue

                tr.reset_iteration()
                retry_mgr.delta_emitted = False
                retry_mgr.reset_all()
                preview = result_text[:200]
                self._on_event("tool_result", {"name": tool_name, "preview": preview})
                yield {
                    "type": "tool_exec_result",
                    "name": tool_name,
                    "result_preview": preview,
                }

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
                retryable = bool(ev.get("retryable", False))

                if retry_mgr.should_clean_retry(ev):
                    try:
                        await loom_task
                    except StopAsyncIteration:
                        pass
                    except Exception:  # noqa: BLE001
                        pass
                    delay = retry_mgr.get_backoff()
                    log.warning(
                        "loom auto-retry: attempt=%d/%d delay=%.1fs reason=%r "
                        "(message=%r)",
                        retry_mgr.attempts + 1, RetryManager.MAX_RETRIES,
                        delay, ev.get("reason"),
                        (ev.get("message") or "")[:200],
                    )
                    yield {
                        "type": "reconnecting",
                        "attempt": retry_mgr.attempts + 1,
                        "max_attempts": RetryManager.MAX_RETRIES,
                        "delay_seconds": delay,
                        "reason": ev.get("reason") or "",
                    }
                    await asyncio.sleep(delay)
                    retry_mgr.increment()
                    tr.working_messages = _sanitize_loom_tool_pairs(tr.working_messages)
                    tr.reset_iteration()
                    retry_mgr.delta_emitted = False
                    loom_iter = self._loom.run_turn_stream(
                        tr.working_messages, model_id=model_id
                    ).__aiter__()
                    loom_task = asyncio.ensure_future(loom_iter.__anext__())
                    continue

                if retry_mgr.should_mid_stream_retry(ev):
                    try:
                        await loom_task
                    except StopAsyncIteration:
                        pass
                    except Exception:  # noqa: BLE001
                        pass
                    tr.materialise_assistant_if_needed()
                    last_asst = tr.working_messages[-1] if tr.working_messages else None
                    if (
                        last_asst
                        and last_asst.role == lt.Role.ASSISTANT
                        and last_asst.tool_calls
                    ):
                        for _tc in last_asst.tool_calls:
                            tr.working_messages.append(lt.ChatMessage(
                                role=lt.Role.TOOL,
                                content=f"[Connection interrupted before {_tc.name} could execute]",
                                tool_call_id=_tc.id,
                                name=_tc.name,
                            ))
                    tr.working_messages.append(lt.ChatMessage(
                        role=lt.Role.USER,
                        content="[Connection was interrupted. Continue your response from where you left off.]",
                    ))
                    delay = retry_mgr.get_backoff()
                    log.warning(
                        "loom mid-stream retry: attempt=%d/%d delay=%.1fs reason=%r "
                        "partial_len=%d",
                        retry_mgr.attempts + 1, RetryManager.MAX_RETRIES,
                        delay, ev.get("reason"),
                        len(tr.full_text),
                    )
                    yield {
                        "type": "reconnecting",
                        "attempt": retry_mgr.attempts + 1,
                        "max_attempts": RetryManager.MAX_RETRIES,
                        "delay_seconds": delay,
                        "reason": "mid_stream_disconnect",
                    }
                    await asyncio.sleep(delay)
                    retry_mgr.increment()
                    tr.working_messages = _sanitize_loom_tool_pairs(tr.working_messages)
                    tr.reset_iteration()
                    retry_mgr.delta_emitted = False
                    loom_iter = self._loom.run_turn_stream(
                        tr.working_messages, model_id=model_id
                    ).__aiter__()
                    loom_task = asyncio.ensure_future(loom_iter.__anext__())
                    continue

                if retry_mgr.should_post_retry_compaction(ev):
                    from .compact import auto_compact
                    from .._loom_bridge.message import _loom_to_nexus_message

                    _nexus_wm = [_loom_to_nexus_message(m) for m in tr.working_messages]
                    compacted_wm, compact_report = auto_compact(_nexus_wm)
                    if compact_report.compacted > 0:
                        try:
                            await loom_task
                        except StopAsyncIteration:
                            pass
                        except Exception:  # noqa: BLE001
                            pass
                        retry_mgr.mark_post_compaction()
                        log.warning(
                            "Post-retry compaction: compacted=%d saved=%d bytes, "
                            "retrying with %d→%d messages",
                            compact_report.compacted, compact_report.saved_bytes,
                            len(tr.working_messages), len(compacted_wm),
                        )
                        yield {
                            "type": "reconnecting",
                            "attempt": RetryManager.MAX_RETRIES + 1,
                            "max_attempts": RetryManager.MAX_RETRIES + 1,
                            "delay_seconds": 2.0,
                            "reason": "post_retry_compaction",
                        }
                        await asyncio.sleep(2.0)
                        retry_mgr.reset_all()
                        tr.working_messages = _sanitize_loom_tool_pairs(
                            self._rebuild_loom_messages(compacted_wm, tr.working_messages)
                        )
                        tr.reset_iteration()
                        retry_mgr.delta_emitted = False
                        loom_iter = self._loom.run_turn_stream(
                            tr.working_messages, model_id=model_id
                        ).__aiter__()
                        loom_task = asyncio.ensure_future(loom_iter.__anext__())
                        continue

                _saw_loom_error = True
                self._log_llm_error(
                    session_id=session_id,
                    error_type=ev.get("reason") or "llm_error",
                    message=(ev.get("message") or "")[:2000],
                    retryable=retryable,
                    retry_attempt=retry_mgr.attempts if retry_mgr.attempts > 0 else None,
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
                             for m in tr.working_messages],
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
                yield tr.format_error(ev)

            elif etype == "done":
                model_used = ev.get("model") or self._chosen_model
                reply_text = tr.full_text
                stop_reason = ev.get("stop_reason")
                if stop_reason == "length":
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
                if not tr.materialised_for_iter:
                    reasoning.capture(adapter)

                persisted_messages = tr.build_persisted_messages(ev)
                reasoning.stamp_onto(persisted_messages, _history_snapshot)

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
            if had_sink_attr:
                adapter._thinking_sink = None  # type: ignore[attr-defined]
            reasoning.clear_adapter_map(adapter)
            for t in (loom_task, q_task):
                if t is not None and not t.done():
                    t.cancel()
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
        """Resume a parked turn after the user answers via the bell or push."""
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

        if not raw_snapshot:
            raise RuntimeError(
                f"parked snapshot for {request_id!r} is empty — cannot resume"
            )

        loom_messages: list[lt.ChatMessage] = []
        for m in raw_snapshot:
            try:
                loom_messages.append(lt.ChatMessage(**m))
            except Exception:  # noqa: BLE001
                loom_messages.append(
                    lt.ChatMessage(
                        role=lt.Role(m.get("role", "user")),
                        content=m.get("content"),
                    )
                )

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

        history_snapshot = [_from_loom_message(m) for m in loom_messages[:-1]]
        adapter = getattr(self._loom, "_provider", None)

        reasoning = ReasoningTracker()
        reasoning.hydrate_adapter_map(adapter, history_snapshot)

        tr = StreamTranslator(
            on_event=self._on_event,
            reasoning=reasoning,
            adapter=adapter,
            history_snapshot=history_snapshot,
            user_msg_content="",
            sessions=self._sessions,
            session_id=session_id,
            trace_getter=lambda: list(self._turn_trace),
            skills_touched_getter=lambda: list(self._skills_touched),
            chosen_model=self._chosen_model,
        )
        tr.working_messages = list(loom_messages)

        async for raw in self._loom.run_turn_stream(
            loom_messages, model_id=self._chosen_model,
        ):
            etype = raw.get("type") if isinstance(raw, dict) else getattr(raw, "type", None)
            ev = raw if isinstance(raw, dict) else raw.model_dump()

            if etype == "done":
                if not tr.materialised_for_iter:
                    reasoning.capture(adapter)
                persisted_messages = tr.build_persisted_messages(ev)
                reasoning.stamp_onto_prefix_only(persisted_messages, history_snapshot)
                model_used = ev.get("model") or self._chosen_model
                yield {
                    "type": "done",
                    "session_id": session_id,
                    "reply": tr.full_text,
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
            else:
                for sse_ev in tr.translate(ev, etype):
                    yield sse_ev

    async def aclose(self) -> None:
        """Shut down the LLM provider and provider registry, releasing HTTP connections."""
        await self._nexus_provider.aclose()
        if self._provider_registry:
            await self._provider_registry.aclose()
