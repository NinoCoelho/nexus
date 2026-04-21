"""Agent tool-calling loop for Nexus — loom.Agent façade.

The iteration logic (tool-call loop, retry, streaming) now lives in
``loom.loop.Agent``.  This module provides a compatibility layer that:

* Keeps the external signature callers (server/app.py, chat.py TUI,
  tests) depend on: ``run_turn(user_message, *, history, context,
  model_id)`` and ``run_turn_stream(...)``.
* Translates between Nexus's types (flat ChatResponse, dict tool args)
  and loom's types (wrapped ChatResponse, string tool args) via
  :mod:`nexus.agent._loom_bridge`.
* Preserves progressive skill disclosure via loom's ``before_llm_call``
  hook, which re-injects a fresh system prompt on every iteration.
* Preserves router tracing via loom's ``choose_model`` hook.
* Exposes ``_trace``, ``_ask_user_handler``, ``_terminal_handler``, and
  ``_resolve_provider`` as attributes so server/app.py can late-bind
  them without changes.

The old loop's module-level helpers ``_extract_pending_question`` and
``_annotate_short_reply`` are kept because tests import them directly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import loom.types as lt
from loom.loop import Agent as LoomAgent, AgentConfig

from .ask_user_tool import AskUserHandler
from .llm import (
    ChatMessage,
    LLMProvider,
    Role,
    StreamEvent,
    ToolCall,
    ToolSpec,
)
from .prompt_builder import build_system_prompt
from .terminal_tool import TerminalHandler
from ..skills.registry import SkillRegistry

log = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_ITERATIONS = 32

SKILL_MANAGE_TOOL = ToolSpec(
    name="skill_manage",
    description=(
        "Create, edit, patch, delete, write_file, or remove_file for a skill in the registry. "
        "A skill is a prescriptive procedure you wrote for future-you, NOT a copy of library docs. "
        "Every SKILL.md MUST have this shape:\n\n"
        "  ---\n"
        "  name: <kebab-case>\n"
        "  description: <imperative one-liner — 'Use this whenever X; prefer over Y.' "
        "Not 'A library that does X'.>\n"
        "  ---\n\n"
        "  ## When to use\n"
        "  - Trigger conditions (concrete, e.g. 'fetching any web page, especially bot-protected or JS-rendered').\n"
        "  - What to reach for this INSTEAD of (e.g. 'prefer over curl/terminal for web fetches').\n\n"
        "  ## Steps\n"
        "  1. Numbered, runnable. Paste the exact commands/snippets that worked.\n\n"
        "  ## Gotchas\n"
        "  - Known failure modes and how to recover (auth walls, rate limits, missing deps).\n\n"
        "Write in the imperative voice of a teammate handing off a recipe. Skip background theory and "
        "library-feature tours — those belong in upstream docs. If the skill won't save a future-you turn, don't create it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "edit", "patch", "delete", "write_file", "remove_file"],
            },
            "name": {"type": "string", "description": "Skill name (directory name, kebab-case)."},
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content (create/edit). Must follow the template in the tool "
                    "description: frontmatter with `name` + imperative `description`, then "
                    "`## When to use`, `## Steps`, `## Gotchas`. Description is an order "
                    "('Use this whenever...'), not a summary ('A library that...')."
                ),
            },
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
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    model: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers (kept for test imports)
# ---------------------------------------------------------------------------

_AFFIRMATIVES = frozenset({
    "yes", "y", "ok", "okay", "sure", "correct", "right", "yeah", "yep",
    "go ahead", "proceed", "continue", "please", "do it",
})
_NEGATIVES = frozenset({
    "no", "n", "nope", "cancel", "stop", "don't", "dont", "negative",
})


def _extract_pending_question(reply: str) -> str | None:
    """Return the last question the agent asked, if the reply ends with one."""
    last_q = reply.rfind("?")
    if last_q == -1:
        return None
    start = max(0, last_q - 200)
    segment = reply[start:last_q + 1]
    first_nl = segment.find("\n")
    if first_nl >= 0:
        segment = segment[first_nl + 1:]
    if len(segment) > 500:
        segment = segment[-500:]
    stripped = segment.strip()
    return stripped or None


def _annotate_short_reply(user_text: str, pending_question: str | None) -> str | None:
    """Expand a terse yes/no reply with the question context."""
    if not pending_question:
        return None
    stripped = user_text.strip().lower()
    if stripped in _AFFIRMATIVES:
        return f'{user_text} (affirmative answer to: "{pending_question}")'
    if stripped in _NEGATIVES:
        return f'{user_text} (negative answer to: "{pending_question}")'
    return None


# ---------------------------------------------------------------------------
# Nexus ↔ loom message conversion helpers
# ---------------------------------------------------------------------------

def _to_loom_message(msg: ChatMessage) -> lt.ChatMessage:
    loom_tcs: list[lt.ToolCall] | None = None
    if msg.tool_calls:
        loom_tcs = [
            lt.ToolCall(id=tc.id, name=tc.name, arguments=json.dumps(tc.arguments))
            for tc in msg.tool_calls
        ]
    return lt.ChatMessage(
        role=lt.Role(msg.role.value),
        content=msg.content,
        tool_calls=loom_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _from_loom_message(msg: lt.ChatMessage) -> ChatMessage:
    nexus_tcs: list[ToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                args = {}
            nexus_tcs.append(ToolCall(id=tc.id, name=tc.name, arguments=args))
    return ChatMessage(
        role=Role(msg.role.value),
        content=msg.content,
        tool_calls=nexus_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


# ---------------------------------------------------------------------------
# Agent façade
# ---------------------------------------------------------------------------

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
        from ._loom_bridge import AgentHandlers

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

        self._loom = self._build_loom_agent()

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

    def _build_loom_agent(self) -> LoomAgent:
        from ._loom_bridge import LoomProviderAdapter, build_tool_registry

        adapter = LoomProviderAdapter(
            self._nexus_provider,
            provider_registry=self._provider_registry,
        )
        tool_reg = build_tool_registry(
            skill_registry=self._registry,
            handlers=self._handlers,
        )

        max_iter = (
            getattr(self._nexus_cfg.agent, "max_iterations", None)
            if self._nexus_cfg else None
        ) or DEFAULT_MAX_TOOL_ITERATIONS

        def _choose_model(messages: list[lt.ChatMessage]) -> str | None:
            if not self._nexus_cfg:
                return None
            from .router import choose_model
            # Extract the last user message for routing
            user_text = ""
            for m in reversed(messages):
                if m.role == lt.Role.USER and m.content:
                    user_text = m.content
                    break
            result = choose_model(user_text, self._nexus_cfg)
            self._chosen_model = result
            return result

        _iter_counter: list[int] = [0]

        def _before_llm_call(messages: list[lt.ChatMessage]) -> list[lt.ChatMessage]:
            _iter_counter[0] += 1
            _on_event("iter", {"n": _iter_counter[0]})
            sys_prompt = build_system_prompt(self._registry)
            return [
                lt.ChatMessage(role=lt.Role.SYSTEM, content=sys_prompt),
                *[m for m in messages if m.role != lt.Role.SYSTEM],
            ]

        def _on_event(kind: str, payload: dict[str, Any]) -> None:
            entry = {"event": kind, **payload}
            self._turn_trace.append(entry)
            if self._trace:
                self._trace(kind, payload)

        def _limit_msg(n: int) -> str:
            return (
                f"I hit the per-turn tool-call limit ({n}) before finishing. "
                "Ask me to continue, or narrow the task — I'll pick up where I left off."
            )

        def _on_after_turn(turn: Any) -> None:
            _on_event("reply", {"text": (turn.reply or "")[:200]})
            # Reset iter counter for next turn
            _iter_counter[0] = 0

        loom_cfg = AgentConfig(
            max_iterations=max_iter,
            model=getattr(getattr(self._nexus_cfg, "agent", None), "default_model", None)
            if self._nexus_cfg else None,
            choose_model=_choose_model if self._nexus_cfg else None,
            before_llm_call=_before_llm_call,
            on_event=_on_event,
            on_after_turn=_on_after_turn,
            serialize_event=lambda ev: ev.model_dump(),
            limit_message_builder=_limit_msg,
            affirmatives=_AFFIRMATIVES,
            negatives=_NEGATIVES,
        )
        return LoomAgent(
            provider=adapter,
            tool_registry=tool_reg,
            config=loom_cfg,
        )

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
                ctx = ev.get("context") or {}
                model_used = ctx.get("model") or self._chosen_model
                # Grab the most recent full_text as reply
                reply_text = full_text
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
                    "skills_touched": list(self._skills_touched),
                    "iterations": ctx.get("iterations", 0),
                    "messages": persisted_messages,
                    "usage": {
                        "input_tokens": ctx.get("input_tokens", 0),
                        "output_tokens": ctx.get("output_tokens", 0),
                        "tool_calls": ctx.get("tool_calls", 0),
                        "model": model_used,
                    },
                }

    async def aclose(self) -> None:
        await self._nexus_provider.aclose()
        if self._provider_registry:
            await self._provider_registry.aclose()
