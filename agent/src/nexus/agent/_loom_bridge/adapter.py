"""LoomProviderAdapter — wraps a Nexus LLMProvider for loom compatibility."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import loom.types as lt
from loom.llm.base import LLMProvider as LoomLLMProvider

from nexus.agent.llm import LLMProvider as NexusLLMProvider

from .message import _loom_to_nexus_message, _nexus_stop_to_loom

log = logging.getLogger(__name__)


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
        max_tokens_for: Callable[[str | None], int] | None = None,
    ) -> None:
        self._nexus = provider
        self._registry = provider_registry
        self._default_model = default_model
        # Resolves the per-call max_tokens (per-model > global default > 0).
        # 0 means "don't pass max_tokens" — providers handle that as either
        # omitting the field (OpenAI-compat) or a legacy fallback (Anthropic).
        self._max_tokens_for = max_tokens_for
        # Optional side-channel for thinking-model chain-of-thought. The Nexus
        # Agent sets this per-turn; the adapter funnels reasoning chunks here
        # instead of yielding them to loom (loom's ContentDeltaEvent path
        # appends to assistant content and would persist the CoT to history).
        self._thinking_sink: Callable[[str], None] | None = None

    def _resolve(self, model_id: str | None) -> tuple[NexusLLMProvider, str | None]:
        """Map a Nexus model id like ``zai/glm-4.6`` to (provider, upstream_name).

        Falls back to ``self._nexus`` (the agent's default provider) when
        the registry doesn't know the id — so a typo in the model picker
        doesn't immediately crash the turn. We log loudly when that
        happens because routing a slash-prefixed Nexus id to a raw
        upstream provider almost always returns nothing useful (the
        upstream rejects the unknown model name, and depending on the
        provider may answer 200-with-empty-content rather than 404).
        """
        resolved = model_id or self._default_model
        if self._registry and resolved:
            try:
                return self._registry.get_for_model(resolved)
            except KeyError as exc:
                known = []
                try:
                    known = list(self._registry.available_model_ids())[:10]
                except Exception:  # noqa: BLE001
                    pass
                log.warning(
                    "LoomAdapter: model %r not in registry (%s) — falling back "
                    "to default provider with the same id, which usually means "
                    "the upstream returns nothing. Known ids (first 10): %s",
                    resolved, exc, known,
                )
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
        max_toks = self._max_tokens_for(model) if self._max_tokens_for else 0
        log.warning(
            "LoomAdapter.chat → provider=%s upstream=%s msgs=%d tools=%d max_toks=%s",
            type(provider).__name__, upstream, len(nexus_messages),
            len(tools or []), max_toks or None,
        )
        nexus_resp = await provider.chat(
            nexus_messages, tools=tools, model=upstream,
            max_tokens=max_toks or None,
        )

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
        max_toks = self._max_tokens_for(model) if self._max_tokens_for else 0
        log.warning(
            "LoomAdapter.chat_stream → provider=%s upstream=%s msgs=%d tools=%d",
            type(provider).__name__, upstream, len(nexus_messages), len(tools or []),
        )
        provider_event_count = 0
        async for ev in provider.chat_stream(
            nexus_messages, tools=tools, model=upstream,
            max_tokens=max_toks or None,
        ):
            provider_event_count += 1
            etype = ev.get("type")
            # First handful of events get logged so we can prove the
            # upstream stream actually yields. Beyond that we'd flood
            # the log with delta events; the count is preserved for the
            # post-loop trace below.
            if provider_event_count <= 3:
                log.warning(
                    "LoomAdapter: provider event #%d type=%s",
                    provider_event_count, etype,
                )

            if etype == "thinking_delta":
                sink = self._thinking_sink
                if sink is not None:
                    try:
                        sink(ev.get("text", ""))
                    except Exception:
                        # A broken sink must not poison the LLM stream.
                        pass
                continue

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

        # Post-iteration summary — fires whether the provider yielded
        # one event or zero. A zero-event stream means the upstream
        # provider returned 200 with no SSE frames at all, which for
        # Anthropic OAuth tokens usually means the token shape was
        # accepted at TLS but rejected at handshake time silently.
        log.warning(
            "LoomAdapter.chat_stream: provider yielded %d events total",
            provider_event_count,
        )

    async def aclose(self) -> None:
        await self._nexus.aclose()
