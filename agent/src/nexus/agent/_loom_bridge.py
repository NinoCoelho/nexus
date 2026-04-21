"""Bridge between Nexus's LLM/tool interfaces and loom's contracts.

Nexus providers return a flat ChatResponse with dict-typed ToolCall.arguments.
Loom expects a wrapped ChatResponse(message=ChatMessage(...), ...) with
ToolCall.arguments as a JSON string, and all messages use the loom ChatMessage
schema.  This module provides:

* ``LoomProviderAdapter`` — wraps a Nexus LLMProvider to satisfy
  ``loom.llm.base.LLMProvider``.
* ``build_tool_registry`` — registers all Nexus tool handlers into a
  ``loom.tools.registry.ToolRegistry`` using loom's ToolHandler ABC.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import loom.types as lt
from loom.llm.base import LLMProvider as LoomLLMProvider
from loom.tools.base import ToolHandler, ToolResult
from loom.tools.registry import ToolRegistry

from .llm import (
    ChatMessage as NexusChatMessage,
    LLMProvider as NexusLLMProvider,
    Role,
    StopReason,
    ToolCall as NexusToolCall,
    ToolSpec,
)


# ---------------------------------------------------------------------------
# Provider adapter: Nexus → Loom
# ---------------------------------------------------------------------------

def _nexus_to_loom_message(msg: NexusChatMessage) -> lt.ChatMessage:
    """Convert a Nexus ChatMessage (dict args) to a loom ChatMessage (str args)."""
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


def _loom_to_nexus_message(msg: lt.ChatMessage) -> NexusChatMessage:
    """Convert a loom ChatMessage (str args) to a Nexus ChatMessage (dict args)."""
    nexus_tcs: list[NexusToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                args = {}
            nexus_tcs.append(NexusToolCall(id=tc.id, name=tc.name, arguments=args))
    return NexusChatMessage(
        role=Role(msg.role.value),
        content=msg.content,
        tool_calls=nexus_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _nexus_stop_to_loom(stop: StopReason) -> lt.StopReason:
    try:
        return lt.StopReason(stop.value)
    except ValueError:
        return lt.StopReason.UNKNOWN


class LoomProviderAdapter(LoomLLMProvider):
    """Wraps a Nexus LLMProvider to satisfy loom.llm.base.LLMProvider.

    Translates:
    - inbound loom ChatMessages → Nexus ChatMessages (str args → dict args)
    - outbound Nexus ChatResponse (flat, dict args) → loom ChatResponse (wrapped, str args)
    - streaming: loom expects Pydantic StreamEvent objects; Nexus streams dicts.
      We translate the Nexus dict stream into loom Pydantic events.
    """

    def __init__(
        self,
        provider: NexusLLMProvider,
        *,
        provider_registry: Any | None = None,
    ) -> None:
        self._nexus = provider
        self._registry = provider_registry

    def _resolve(self, model_id: str | None) -> tuple[NexusLLMProvider, str | None]:
        """Map a Nexus model id like ``zai/glm-4.6`` to (provider, upstream_name)."""
        if self._registry and model_id:
            try:
                return self._registry.get_for_model(model_id)
            except KeyError:
                pass
        return self._nexus, model_id

    async def chat(
        self,
        messages: list[lt.ChatMessage],
        *,
        tools: list[lt.ToolSpec] | None = None,
        model: str | None = None,
    ) -> lt.ChatResponse:
        nexus_messages = [_loom_to_nexus_message(m) for m in messages]
        provider, upstream = self._resolve(model)
        nexus_resp = await provider.chat(nexus_messages, tools=tools, model=upstream)

        # Build a loom ChatMessage from the flat Nexus response
        loom_tcs: list[lt.ToolCall] | None = None
        if nexus_resp.tool_calls:
            loom_tcs = [
                lt.ToolCall(id=tc.id, name=tc.name, arguments=json.dumps(tc.arguments))
                for tc in nexus_resp.tool_calls
            ]
        loom_msg = lt.ChatMessage(
            role=lt.Role.ASSISTANT,
            content=nexus_resp.content,
            tool_calls=loom_tcs,
        )
        return lt.ChatResponse(
            message=loom_msg,
            usage=nexus_resp.usage,
            stop_reason=_nexus_stop_to_loom(nexus_resp.stop_reason),
            model=model or "",
        )

    async def chat_stream(
        self,
        messages: list[lt.ChatMessage],
        *,
        tools: list[lt.ToolSpec] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[lt.StreamEvent]:
        nexus_messages = [_loom_to_nexus_message(m) for m in messages]
        # Nexus streaming yields dicts; we translate to loom Pydantic events.
        # Collect tool call deltas so we can emit a stop event at the end.
        finish_reason: str = "stop"
        tool_parts: dict[str, dict[str, Any]] = {}

        provider, upstream = self._resolve(model)
        async for ev in provider.chat_stream(nexus_messages, tools=tools, model=upstream):
            etype = ev.get("type")

            if etype == "delta":
                yield lt.ContentDeltaEvent(delta=ev.get("text", ""))

            elif etype == "tool_call_start":
                tc_id = ev.get("id", "")
                tc_name = ev.get("name", "")
                tool_parts[tc_id] = {"name": tc_name, "args": ""}
                # Emit a tool_call_delta with index so loom.Agent assembles it
                idx = len(tool_parts) - 1
                yield lt.ToolCallDeltaEvent(
                    index=idx, id=tc_id, name=tc_name, arguments_delta=None
                )

            elif etype == "tool_call_delta":
                tc_id = ev.get("id", "")
                args_delta = ev.get("args_delta", "")
                if tc_id in tool_parts:
                    tool_parts[tc_id]["args"] += args_delta
                    # Find index by insertion order
                    idx = list(tool_parts.keys()).index(tc_id)
                    yield lt.ToolCallDeltaEvent(
                        index=idx, id=tc_id, name=None, arguments_delta=args_delta
                    )

            elif etype == "tool_call_end":
                pass  # loom assembles from deltas; no explicit end event in loom

            elif etype == "finish":
                finish_reason = ev.get("finish_reason", "stop")
                # Emit any tool calls from the finish event as delta events so
                # loom.Agent can assemble them (some providers only emit tool
                # calls in the finish frame, not as deltas).
                # Only synthesize tool-call deltas for providers that DIDN'T
                # already stream them. If we've seen any `tool_call_delta`
                # event for a tc_id, its args are already assembled in loom's
                # buffer — re-emitting the full payload here would duplicate.
                finish_tool_calls = ev.get("tool_calls") or []
                for idx, tc_dict in enumerate(finish_tool_calls):
                    tc_id = tc_dict.get("id", f"tc_{idx}")
                    if tc_id in tool_parts and tool_parts[tc_id]["args"]:
                        continue  # already streamed — skip
                    tc_name = tc_dict.get("name", "")
                    tc_args = tc_dict.get("arguments", {})
                    if isinstance(tc_args, dict):
                        tc_args_str = json.dumps(tc_args)
                    else:
                        tc_args_str = str(tc_args)
                    if tc_id not in tool_parts:
                        yield lt.ToolCallDeltaEvent(
                            index=idx, id=tc_id, name=tc_name, arguments_delta=None
                        )
                        tool_parts[tc_id] = {"name": tc_name, "args": tc_args_str}
                    yield lt.ToolCallDeltaEvent(
                        index=idx, id=tc_id, name=None, arguments_delta=tc_args_str
                    )

                usage_dict = ev.get("usage") or {}
                usage = lt.Usage(
                    input_tokens=int(usage_dict.get("input_tokens") or 0),
                    output_tokens=int(usage_dict.get("output_tokens") or 0),
                    cache_read_tokens=int(usage_dict.get("cache_read_tokens") or 0),
                    cache_write_tokens=int(usage_dict.get("cache_write_tokens") or 0),
                )
                yield lt.UsageEvent(usage=usage)
                try:
                    loom_stop = lt.StopReason(finish_reason)
                except ValueError:
                    loom_stop = lt.StopReason.UNKNOWN
                yield lt.StopEvent(stop_reason=loom_stop)

    async def aclose(self) -> None:
        await self._nexus.aclose()


# ---------------------------------------------------------------------------
# Tool adapter: Nexus handlers → loom ToolHandler
# ---------------------------------------------------------------------------

class _SimpleToolHandler(ToolHandler):
    """Adapts a sync or async callable(args: dict) -> str into loom ToolHandler."""

    def __init__(self, spec: ToolSpec, fn: Any) -> None:
        self._spec = spec
        self._fn = fn

    @property
    def tool(self) -> ToolSpec:
        return self._spec

    async def invoke(self, args: dict) -> ToolResult:
        import inspect
        result = self._fn(args)
        if inspect.isawaitable(result):
            result = await result
        text = result if isinstance(result, str) else json.dumps(result)
        return ToolResult(text=text)


class AgentHandlers:
    """Mutable holder for late-bound HITL handlers.

    Built once and shared between the tool registry closures and the
    Agent façade.  When ``app.py`` sets ``agent._ask_user_handler``,
    the corresponding attribute here is updated so all registry
    closures see the new value without a registry rebuild.
    """

    def __init__(
        self,
        ask_user: Any | None = None,
        terminal: Any | None = None,
    ) -> None:
        self.ask_user = ask_user
        self.terminal = terminal


def build_tool_registry(
    *,
    skill_registry: Any,
    handlers: AgentHandlers,
) -> ToolRegistry:
    """Build a loom ToolRegistry populated with all Nexus tools.

    HITL handler closures read from ``handlers`` at dispatch time, so
    late-binding by the server (setting ``handlers.ask_user`` after
    registry construction) takes effect on the next tool call.
    """
    from ..agent.loop import SKILL_MANAGE_TOOL
    from ..skills.manager import SkillManager
    from ..tools.acp_call import ACP_CALL_TOOL, acp_call
    from ..tools.http_call import HTTP_CALL_TOOL, HttpCallHandler
    from ..tools.kanban_tool import KANBAN_MANAGE_TOOL, handle_kanban_tool
    from ..tools.memory_tool import MEMORY_READ_TOOL, MEMORY_WRITE_TOOL, MemoryHandler
    from ..tools.state_tool import STATE_TOOLS, StateToolHandler
    from ..tools.vault_tool import VAULT_TOOLS, handle_vault_tool
    from ..agent.ask_user_tool import ASK_USER_TOOL
    from ..agent.terminal_tool import TERMINAL_TOOL

    registry = ToolRegistry()
    state = StateToolHandler(skill_registry)
    manager = SkillManager(skill_registry)
    http = HttpCallHandler()

    # skills_list / skill_view
    for spec in STATE_TOOLS:
        _spec = spec

        async def _state_invoke(args: dict, *, _spec=_spec) -> str:
            return state.invoke(_spec.name, args).to_text()

        registry.register(_SimpleToolHandler(_spec, _state_invoke))

    # skill_manage
    async def _skill_manage(args: dict) -> str:
        action = args.get("action", "")
        result = manager.invoke(action, args)
        return (
            f'{{"ok": {str(result.ok).lower()}, '
            f'"message": {result.message!r}, '
            f'"rolled_back": {str(result.rolled_back).lower()}}}'
        )

    registry.register(_SimpleToolHandler(SKILL_MANAGE_TOOL, _skill_manage))

    # http_call
    async def _http_call(args: dict) -> str:
        res = await http.invoke(args)
        return res.to_text()

    registry.register(_SimpleToolHandler(HTTP_CALL_TOOL, _http_call))

    # acp_call
    async def _acp_call(args: dict) -> str:
        return await acp_call(args.get("agent_id", ""), args.get("message", ""))

    registry.register(_SimpleToolHandler(ACP_CALL_TOOL, _acp_call))

    # vault tools
    for spec in VAULT_TOOLS:
        _spec = spec

        async def _vault(args: dict, *, _spec=_spec) -> str:
            return handle_vault_tool(_spec.name, args)

        registry.register(_SimpleToolHandler(_spec, _vault))

    # kanban_manage
    async def _kanban(args: dict) -> str:
        return handle_kanban_tool(args)

    registry.register(_SimpleToolHandler(KANBAN_MANAGE_TOOL, _kanban))

    # memory_read / memory_write
    async def _mem_read(args: dict) -> str:
        return await MemoryHandler().read(args.get("key", ""))

    async def _mem_write(args: dict) -> str:
        return await MemoryHandler().write(args.get("key", ""), args.get("content", ""))

    registry.register(_SimpleToolHandler(MEMORY_READ_TOOL, _mem_read))
    registry.register(_SimpleToolHandler(MEMORY_WRITE_TOOL, _mem_write))

    # HITL tools — always registered; handlers resolved at dispatch time.
    # This lets app.py late-bind handlers without rebuilding the registry.
    async def _ask_user(args: dict) -> str:
        h = handlers.ask_user
        if h is None:
            return '{"ok": false, "error": "ask_user unavailable: handler not wired"}'
        result = await h.invoke(args)
        return result.to_text()

    async def _terminal(args: dict) -> str:
        h = handlers.terminal
        if h is None:
            return '{"ok": false, "error": "terminal unavailable: handler not wired"}'
        result = await h.invoke(args)
        return result.to_text()

    registry.register(_SimpleToolHandler(ASK_USER_TOOL, _ask_user))
    registry.register(_SimpleToolHandler(TERMINAL_TOOL, _terminal))

    return registry
