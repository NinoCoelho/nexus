"""LoomProviderAdapter — wraps a Nexus LLMProvider for loom compatibility."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import loom.types as lt
from loom.llm.base import LLMProvider as LoomLLMProvider

from nexus.agent.llm import LLMProvider as NexusLLMProvider

from .message import _loom_to_nexus_message, _nexus_stop_to_loom


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
        default_model: str | None = None,
    ) -> None:
        self._nexus = provider
        self._registry = provider_registry
        self._default_model = default_model

    def _resolve(self, model_id: str | None) -> tuple[NexusLLMProvider, str | None]:
        """Map a Nexus model id like ``zai/glm-4.6`` to (provider, upstream_name)."""
        resolved = model_id or self._default_model
        if self._registry and resolved:
            try:
                return self._registry.get_for_model(resolved)
            except KeyError:
                pass
        if not resolved:
            resolved = getattr(self._nexus, "_model", None) or None
        return self._nexus, resolved

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
