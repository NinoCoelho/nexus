"""Agent tool-calling loop for Nexus."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .llm import (
    ChatMessage,
    LLMProvider,
    MalformedOutputError,
    Role,
    StopReason,
    ToolCall,
    ToolSpec,
)
from .prompt_builder import build_system_prompt
from ..skills.manager import SkillManager
from ..skills.registry import SkillRegistry
from ..tools.acp_call import ACP_CALL_TOOL, acp_call
from ..tools.http_call import HTTP_CALL_TOOL, HttpCallHandler
from ..tools.state_tool import STATE_TOOLS, StateToolHandler

MAX_TOOL_ITERATIONS = 16

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


class Agent:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        registry: SkillRegistry,
        trace: TraceCallback | None = None,
        provider_registry: Any | None = None,
        nexus_cfg: Any | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._trace = trace
        self._state = StateToolHandler(registry)
        self._manager = SkillManager(registry)
        self._http = HttpCallHandler()
        self._provider_registry = provider_registry
        self._nexus_cfg = nexus_cfg

    def _emit(self, event: str, data: dict[str, Any], trace: list[dict[str, Any]]) -> None:
        entry = {"event": event, **data}
        trace.append(entry)
        if self._trace:
            self._trace(event, data)

    def _tools(self) -> list[ToolSpec]:
        return [*STATE_TOOLS, SKILL_MANAGE_TOOL, HTTP_CALL_TOOL, ACP_CALL_TOOL]

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

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            self._emit("iter", {"n": iteration}, trace)
            response = await provider.chat(messages, tools=tools, model=upstream_model)

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
                )

            messages.append(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for tc in response.tool_calls:
                self._emit("tool_call", {"name": tc.name, "args": tc.arguments}, trace)
                result = await self._handle(tc, skills_touched)
                self._emit("tool_result", {"name": tc.name, "preview": result[:200]}, trace)
                messages.append(
                    ChatMessage(role=Role.TOOL, content=result, tool_call_id=tc.id)
                )

        reply_text = "[iteration limit reached]"
        messages.append(ChatMessage(role=Role.ASSISTANT, content=reply_text))
        return AgentTurn(
            reply=reply_text,
            skills_touched=skills_touched,
            iterations=MAX_TOOL_ITERATIONS,
            trace=trace,
            messages=messages,
        )

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

        return f"error: unknown tool {tc.name!r}"

    async def aclose(self) -> None:
        await self._http.aclose()
        if self._provider_registry:
            await self._provider_registry.aclose()
