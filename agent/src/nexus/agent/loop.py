"""Agent tool-calling loop for Nexus."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .ask_user_tool import ASK_USER_TOOL, AskUserHandler
from .llm import (
    ChatMessage,
    ChatResponse,
    LLMProvider,
    LLMTransportError,
    MalformedOutputError,
    Role,
    StopReason,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
)
from .prompt_builder import build_system_prompt
from .terminal_tool import TERMINAL_TOOL, TerminalHandler
from ..error_classifier import ClassifiedError, FailoverReason, classify_api_error
from ..retry import jittered_backoff
from ..skills.manager import SkillManager
from ..skills.registry import SkillRegistry
from ..tools.acp_call import ACP_CALL_TOOL, acp_call
from ..tools.http_call import HTTP_CALL_TOOL, HttpCallHandler
from ..tools.kanban_tool import KANBAN_MANAGE_TOOL, handle_kanban_tool
from ..tools.state_tool import STATE_TOOLS, StateToolHandler
from ..tools.memory_tool import MEMORY_READ_TOOL, MEMORY_WRITE_TOOL, MemoryHandler
from ..tools.vault_tool import VAULT_TOOLS, handle_vault_tool

log = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_ITERATIONS = 32  # was 16; doubled so research tasks finish

# Max provider call attempts per LLM round-trip. First attempt + up to
# (MAX_PROVIDER_ATTEMPTS - 1) retries. Kept small — the agent loop itself
# will retry on a subsequent iteration if a genuine upstream outage
# persists, so we don't need many retries here.
MAX_PROVIDER_ATTEMPTS = 3

SKILL_MANAGE_TOOL = ToolSpec(
    name="skill_manage",
    description=(
        "Create, edit, patch, delete, write_file, or remove_file for a skill in the registry."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "edit", "patch", "delete", "write_file", "remove_file"],
            },
            "name": {"type": "string", "description": "Skill name (directory name)."},
            "content": {"type": "string", "description": "Full SKILL.md content (create/edit)."},
            "old": {"type": "string", "description": "Text to find (patch)."},
            "new": {"type": "string", "description": "Replacement text (patch)."},
            "path": {"type": "string", "description": "Relative file path (write_file/remove_file)."},
        },
        "required": ["action", "name"],
    },
)

TraceCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class AgentTurn:
    reply: str
    skills_touched: list[str] = field(default_factory=list)
    iterations: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    # Aggregated usage across every provider round-trip in this turn.
    # Zeroes when the provider doesn't surface usage — callers should
    # treat that as "unknown" rather than "free".
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    model: str | None = None


class Agent:
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
        self._provider = provider
        self._registry = registry
        self._trace = trace
        self._state = StateToolHandler(registry)
        self._manager = SkillManager(registry)
        self._http = HttpCallHandler()
        self._provider_registry = provider_registry
        self._nexus_cfg = nexus_cfg
        # ask_user only makes sense in a live /chat session — without a
        # SessionStore wired in, the tool has nowhere to publish the
        # event or park the Future. The server supplies this; CLI-only
        # paths can leave it None and the tool simply isn't advertised.
        self._ask_user_handler = ask_user_handler
        # terminal composes on top of ask_user — no point offering it
        # when HITL isn't wired, because every call would fail.
        self._terminal_handler = (
            TerminalHandler(ask_user_handler=ask_user_handler)
            if ask_user_handler is not None
            else None
        )

    def _emit(self, event: str, data: dict[str, Any], trace: list[dict[str, Any]]) -> None:
        entry = {"event": event, **data}
        trace.append(entry)
        if self._trace:
            self._trace(event, data)

    @staticmethod
    def _classify(
        exc: Exception, *, provider_name: str, model: str, num_messages: int
    ) -> ClassifiedError:
        """Thin wrapper that applies our in-loop parameters to the classifier."""
        return classify_api_error(
            exc,
            provider=provider_name,
            model=model,
            num_messages=num_messages,
        )

    async def _chat_with_retry(
        self,
        provider: LLMProvider,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec],
        model: str | None,
        trace: list[dict[str, Any]],
    ) -> ChatResponse:
        """Call ``provider.chat`` with classifier-driven retry.

        Non-streaming path — safe to retry freely because no partial output
        has been emitted. Honours ``ClassifiedError.retryable`` and backs off
        with jittered exponential delay. Non-retryable errors propagate as
        the original :class:`LLMTransportError` so callers can display the
        upstream message.
        """
        provider_name = type(provider).__name__
        model_str = model or ""
        last_exc: Exception | None = None

        for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
            try:
                return await provider.chat(messages, tools=tools, model=model)
            except LLMTransportError as exc:
                last_exc = exc
                classified = self._classify(
                    exc,
                    provider_name=provider_name,
                    model=model_str,
                    num_messages=len(messages),
                )
                self._emit(
                    "provider_error",
                    {
                        "attempt": attempt,
                        "reason": classified.reason.value,
                        "retryable": classified.retryable,
                        "status_code": classified.status_code,
                        "message": classified.user_facing_summary,
                    },
                    trace,
                )
                if not classified.retryable or attempt >= MAX_PROVIDER_ATTEMPTS:
                    raise
                delay = jittered_backoff(attempt)
                log.warning(
                    "chat attempt %d/%d failed (%s); backoff %.1fs",
                    attempt, MAX_PROVIDER_ATTEMPTS, classified.reason.value, delay,
                )
                await asyncio.sleep(delay)
            except MalformedOutputError:
                # Malformed output isn't a transport problem — do NOT retry,
                # the same bad response would come back.
                raise
        # Defensive: should be unreachable because we raise inside the loop.
        assert last_exc is not None
        raise last_exc

    def _tools(self) -> list[ToolSpec]:
        tools: list[ToolSpec] = [
            *STATE_TOOLS,
            SKILL_MANAGE_TOOL,
            HTTP_CALL_TOOL,
            ACP_CALL_TOOL,
            *VAULT_TOOLS,
            KANBAN_MANAGE_TOOL,
            MEMORY_READ_TOOL,
            MEMORY_WRITE_TOOL,
        ]
        # HITL tools only surface when the handler is wired — a CLI run
        # without a SessionStore has no way to prompt, so advertising
        # ``ask_user`` there would just produce "unavailable" errors.
        if self._ask_user_handler is not None:
            tools.append(ASK_USER_TOOL)
        if self._terminal_handler is not None:
            tools.append(TERMINAL_TOOL)
        return tools

    def _resolve_provider(self, model_id: str | None) -> tuple[LLMProvider, str | None]:
        """Return (provider, upstream_model_name). Falls back to self._provider."""
        if self._provider_registry and model_id:
            try:
                provider, upstream = self._provider_registry.get_for_model(model_id)
                return provider, upstream
            except KeyError:
                pass
        return self._provider, None

    async def run_turn(
        self,
        user_message: str,
        *,
        history: list[ChatMessage] | None = None,
        context: str | None = None,
        model_id: str | None = None,
    ) -> AgentTurn:
        from .router import choose_model, ROUTE_TRACE

        trace: list[dict[str, Any]] = []
        skills_touched: list[str] = []

        # Determine model via router if configured
        chosen_model = model_id
        route_reason = "explicit"
        if self._nexus_cfg and not model_id:
            chosen_model = choose_model(user_message, self._nexus_cfg)
            route_reason = ROUTE_TRACE[-1] if ROUTE_TRACE else "auto"

        if not history:
            messages: list[ChatMessage] = [
                ChatMessage(
                    role=Role.SYSTEM,
                    content=build_system_prompt(self._registry, context=context),
                )
            ]
        else:
            messages = list(history)

        messages.append(ChatMessage(role=Role.USER, content=user_message))
        tools = self._tools()

        provider, upstream_model = self._resolve_provider(chosen_model)

        # Emit model meta on first iter
        self._emit(
            "tool_call",
            {"name": "_meta", "args": {"model": chosen_model or "default", "reason": route_reason}},
            trace,
        )

        max_iter = (
            getattr(self._nexus_cfg.agent, "max_iterations", None)
            if self._nexus_cfg else None
        ) or DEFAULT_MAX_TOOL_ITERATIONS

        # Per-turn usage accumulation. ``model`` is the canonical slug
        # the user / router chose (what they'd see in their config),
        # not the possibly-different upstream name.
        acc_in = 0
        acc_out = 0
        acc_tool_calls = 0

        for iteration in range(1, max_iter + 1):
            self._emit("iter", {"n": iteration}, trace)
            response = await self._chat_with_retry(
                provider,
                messages,
                tools=tools,
                model=upstream_model,
                trace=trace,
            )
            acc_in += response.usage.input_tokens
            acc_out += response.usage.output_tokens

            if response.stop_reason != StopReason.TOOL_CALLS or not response.tool_calls:
                reply_text = response.content or ""
                messages.append(ChatMessage(role=Role.ASSISTANT, content=reply_text))
                self._emit("reply", {"text": reply_text[:200]}, trace)
                return AgentTurn(
                    reply=reply_text,
                    skills_touched=skills_touched,
                    iterations=iteration,
                    trace=trace,
                    messages=messages,
                    input_tokens=acc_in,
                    output_tokens=acc_out,
                    tool_calls=acc_tool_calls,
                    model=chosen_model,
                )

            messages.append(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for tc in response.tool_calls:
                acc_tool_calls += 1
                self._emit("tool_call", {"name": tc.name, "args": tc.arguments}, trace)
                result = await self._handle(tc, skills_touched)
                self._emit("tool_result", {"name": tc.name, "preview": result[:200]}, trace)
                messages.append(
                    ChatMessage(role=Role.TOOL, content=result, tool_call_id=tc.id)
                )

        reply_text = (
            f"I hit the per-turn tool-call limit ({max_iter}) before finishing. "
            "Ask me to continue, or narrow the task — I'll pick up where I left off."
        )
        messages.append(ChatMessage(role=Role.ASSISTANT, content=reply_text))
        return AgentTurn(
            reply=reply_text,
            skills_touched=skills_touched,
            iterations=max_iter,
            trace=trace,
            messages=messages,
            input_tokens=acc_in,
            output_tokens=acc_out,
            tool_calls=acc_tool_calls,
            model=chosen_model,
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
        from .router import choose_model, ROUTE_TRACE

        trace: list[dict[str, Any]] = []
        skills_touched: list[str] = []

        chosen_model = model_id
        route_reason = "explicit"
        if self._nexus_cfg and not model_id:
            chosen_model = choose_model(user_message, self._nexus_cfg)
            route_reason = ROUTE_TRACE[-1] if ROUTE_TRACE else "auto"

        if not history:
            messages: list[ChatMessage] = [
                ChatMessage(
                    role=Role.SYSTEM,
                    content=build_system_prompt(self._registry, context=context),
                )
            ]
        else:
            messages = list(history)

        messages.append(ChatMessage(role=Role.USER, content=user_message))
        tools = self._tools()

        provider, upstream_model = self._resolve_provider(chosen_model)

        self._emit(
            "tool_call",
            {"name": "_meta", "args": {"model": chosen_model or "default", "reason": route_reason}},
            trace,
        )

        max_iter = (
            getattr(self._nexus_cfg.agent, "max_iterations", None)
            if self._nexus_cfg else None
        ) or DEFAULT_MAX_TOOL_ITERATIONS

        full_text = ""

        provider_name = type(provider).__name__

        # Per-turn usage accumulation (see run_turn for the non-stream
        # equivalent). Streaming adds: some providers emit `usage` only
        # on the last frame, which we pick up in the finish event below.
        acc_in = 0
        acc_out = 0
        acc_tool_calls = 0

        for iteration in range(1, max_iter + 1):
            self._emit("iter", {"n": iteration}, trace)

            # Collect tool calls from this provider pass
            current_tool_calls: list[dict[str, Any]] = []
            current_content = ""
            finish_reason = "stop"
            current_usage: dict[str, int] = {}

            # Streaming retry: we can only retry BEFORE the first event has
            # been forwarded to the caller — once bytes are on the wire the
            # client has partial content and re-running the stream would
            # duplicate deltas. `streamed_any` flips True at the first event
            # we receive and disables retry thereafter.
            streamed_any = False
            for attempt in range(1, MAX_PROVIDER_ATTEMPTS + 1):
                try:
                    async for event in provider.chat_stream(
                        messages, tools=tools, model=upstream_model
                    ):
                        streamed_any = True
                        etype = event.get("type")

                        if etype == "delta":
                            full_text += event["text"]
                            current_content += event["text"]
                            yield event

                        elif etype in ("tool_call_start", "tool_call_delta", "tool_call_end"):
                            yield event

                        elif etype == "finish":
                            finish_reason = event.get("finish_reason", "stop")
                            current_content = event.get("content", current_content)
                            current_tool_calls = event.get("tool_calls", [])
                            current_usage = event.get("usage") or {}
                    break  # stream ended cleanly — exit retry loop
                except LLMTransportError as exc:
                    classified = self._classify(
                        exc,
                        provider_name=provider_name,
                        model=upstream_model or "",
                        num_messages=len(messages),
                    )
                    self._emit(
                        "provider_error",
                        {
                            "attempt": attempt,
                            "reason": classified.reason.value,
                            "retryable": classified.retryable,
                            "status_code": classified.status_code,
                            "message": classified.user_facing_summary,
                        },
                        trace,
                    )
                    if streamed_any:
                        # Mid-stream failure — bytes sent already, cannot retry.
                        # Terminate the turn cleanly with a structured error.
                        yield {
                            "type": "error",
                            "detail": classified.user_facing_summary,
                            "reason": classified.reason.value,
                            "retryable": False,
                            "status_code": classified.status_code,
                        }
                        yield {
                            "type": "done",
                            "session_id": session_id,
                            "reply": current_content or full_text,
                            "trace": trace,
                            "skills_touched": skills_touched,
                            "iterations": iteration,
                            "messages": messages,
                        }
                        return
                    if not classified.retryable or attempt >= MAX_PROVIDER_ATTEMPTS:
                        raise
                    delay = jittered_backoff(attempt)
                    log.warning(
                        "chat_stream attempt %d/%d failed (%s); backoff %.1fs",
                        attempt, MAX_PROVIDER_ATTEMPTS, classified.reason.value, delay,
                    )
                    await asyncio.sleep(delay)

            # Fold the provider-reported usage into the per-turn accumulator
            # as soon as we have it. We do this regardless of whether this
            # iteration was terminal or produced more tool calls — every
            # round-trip counts toward cost.
            acc_in += int(current_usage.get("input_tokens") or 0)
            acc_out += int(current_usage.get("output_tokens") or 0)

            if finish_reason != "tool_calls" or not current_tool_calls:
                # Terminal: no more tool calls
                messages.append(ChatMessage(role=Role.ASSISTANT, content=current_content or full_text))
                self._emit("reply", {"text": (current_content or full_text)[:200]}, trace)
                yield {
                    "type": "done",
                    "session_id": session_id,
                    "reply": current_content or full_text,
                    "trace": trace,
                    "skills_touched": skills_touched,
                    "iterations": iteration,
                    "messages": messages,
                    "usage": {
                        "input_tokens": acc_in,
                        "output_tokens": acc_out,
                        "tool_calls": acc_tool_calls,
                        "model": chosen_model,
                    },
                }
                return

            # Tool calls to execute
            messages.append(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=current_content or None,
                    tool_calls=[
                        ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                        for tc in current_tool_calls
                    ],
                )
            )

            for tc_dict in current_tool_calls:
                acc_tool_calls += 1
                tc = ToolCall(id=tc_dict["id"], name=tc_dict["name"], arguments=tc_dict["arguments"])
                self._emit("tool_call", {"name": tc.name, "args": tc.arguments}, trace)
                yield {"type": "tool_exec_start", "name": tc.name, "args": tc.arguments}
                result = await self._handle(tc, skills_touched)
                self._emit("tool_result", {"name": tc.name, "preview": result[:200]}, trace)
                yield {"type": "tool_exec_result", "name": tc.name, "result_preview": result[:200]}
                messages.append(ChatMessage(role=Role.TOOL, content=result, tool_call_id=tc.id))

        # Hit iteration cap
        limit_text = (
            f"I hit the per-turn tool-call limit ({max_iter}) before finishing. "
            "Ask me to continue, or narrow the task — I'll pick up where I left off."
        )
        messages.append(ChatMessage(role=Role.ASSISTANT, content=limit_text))
        yield {"type": "limit_reached", "iterations": max_iter}
        yield {
            "type": "done",
            "session_id": session_id,
            "reply": limit_text,
            "trace": trace,
            "skills_touched": skills_touched,
            "iterations": max_iter,
            "messages": messages,
            "usage": {
                "input_tokens": acc_in,
                "output_tokens": acc_out,
                "tool_calls": acc_tool_calls,
                "model": chosen_model,
            },
        }

    async def _handle(self, tc: ToolCall, skills_touched: list[str]) -> str:
        if tc.name in {"skills_list", "skill_view"}:
            return self._state.invoke(tc.name, tc.arguments).to_text()

        if tc.name == "skill_manage":
            action = tc.arguments.get("action", "")
            name = tc.arguments.get("name", "")
            result = self._manager.invoke(action, tc.arguments)
            if name:
                skills_touched.append(name)
            return f'{{"ok": {str(result.ok).lower()}, "message": {result.message!r}, "rolled_back": {str(result.rolled_back).lower()}}}'

        if tc.name == "http_call":
            res = await self._http.invoke(tc.arguments)
            return res.to_text()

        if tc.name == "acp_call":
            agent_id = tc.arguments.get("agent_id", "")
            message = tc.arguments.get("message", "")
            return await acp_call(agent_id, message)

        if tc.name in {"vault_list", "vault_read", "vault_write"}:
            return handle_vault_tool(tc.name, tc.arguments)

        if tc.name == "kanban_manage":
            return handle_kanban_tool(tc.arguments)

        if tc.name == "ask_user" and self._ask_user_handler is not None:
            ask_result = await self._ask_user_handler.invoke(tc.arguments)
            return ask_result.to_text()

        if tc.name == "terminal" and self._terminal_handler is not None:
            term_result = await self._terminal_handler.invoke(tc.arguments)
            return term_result.to_text()

        if tc.name == "memory_read":
            return await MemoryHandler().read(tc.arguments.get("key", ""))

        if tc.name == "memory_write":
            return await MemoryHandler().write(
                tc.arguments.get("key", ""),
                tc.arguments.get("content", ""),
            )

        return f"error: unknown tool {tc.name!r}"

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._provider_registry:
            await self._provider_registry.aclose()
