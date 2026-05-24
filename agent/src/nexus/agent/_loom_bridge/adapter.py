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


def _msg_fingerprint(msg: lt.ChatMessage) -> tuple | None:
    """Build a stable fingerprint for an assistant message for reasoning_content lookup."""
    if msg.role != lt.Role.ASSISTANT:
        return None
    content_str = msg.content if isinstance(msg.content, str) else str(msg.content or "")
    tc_ids = tuple(tc.id for tc in (msg.tool_calls or []))
    return ("assistant", content_str[:500], tc_ids)


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
        self._max_tokens_for = max_tokens_for
        self._thinking_sink: Callable[[str], None] | None = None
        self._last_reasoning_content: str | None = None
        # Side-channel for reasoning_content survival. The agent loop populates
        # this with fingerprints of assistant messages that carry reasoning,
        # and the adapter re-attaches it when converting loom→nexus so
        # thinking-model APIs (DeepSeek-R1, etc.) see reasoning_content on
        # every assistant turn in the message history.
        self._reasoning_content_map: dict[tuple, str] = {}

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

    def _restore_reasoning(self, loom_msgs: list[lt.ChatMessage]) -> list:
        from nexus.agent.llm import ChatMessage as NexusChatMessage
        nexus_messages = [_loom_to_nexus_message(m) for m in loom_msgs]
        if not self._reasoning_content_map:
            return nexus_messages
        out: list[NexusChatMessage] = []
        for loom_m, nexus_m in zip(loom_msgs, nexus_messages):
            fp = _msg_fingerprint(loom_m)
            if fp and fp in self._reasoning_content_map:
                out.append(nexus_m.model_copy(
                    update={"reasoning_content": self._reasoning_content_map[fp]}
                ))
            else:
                out.append(nexus_m)
        return out

    async def chat(
        self,
        messages: list[lt.ChatMessage],
        *,
        tools: list[lt.ToolSpec] | None = None,
        model: str | None = None,
    ) -> lt.ChatResponse:
        nexus_messages = self._restore_reasoning(messages)
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
        nexus_messages = self._restore_reasoning(messages)
        # Collect tool call deltas so we can emit a stop event at the end.
        finish_reason: str = "stop"
        tool_parts: dict[str, dict[str, Any]] = {}
        self._last_reasoning_content = None
        _accumulated_reasoning: list[str] = []
        _accumulated_content: list[str] = []

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
                rc_piece = ev.get("text", "")
                if rc_piece:
                    _accumulated_reasoning.append(rc_piece)
                sink = self._thinking_sink
                if sink is not None:
                    try:
                        sink(rc_piece)
                    except Exception:
                        pass
                continue

            if etype == "delta":
                _delta_text = ev.get("text", "")
                if _delta_text:
                    _accumulated_content.append(_delta_text)
                yield lt.ContentDeltaEvent(delta=_delta_text)

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
                    # Buffer raw deltas; validated complete args emitted at finish

            elif etype == "tool_call_end":
                pass  # loom assembles from deltas; no explicit end event in loom

            elif etype == "finish":
                finish_reason = ev.get("finish_reason", "stop")
                # Capture reasoning_content from the provider's finish event.
                # Prefer the finish event's value (accumulated by the provider);
                # fall back to our own accumulation from thinking_delta events.
                rc = ev.get("reasoning_content")
                if rc:
                    self._last_reasoning_content = rc
                elif _accumulated_reasoning:
                    self._last_reasoning_content = "".join(_accumulated_reasoning)
                _accumulated_reasoning.clear()
                # Emit validated tool call arguments from the finish event.
                # By NOT forwarding raw streaming deltas above, loom's
                # accumulation buffer is empty — so we safely emit the
                # complete validated args here without duplication.
                finish_tool_calls = ev.get("tool_calls") or []
                finish_tc_ids: set[str] = set()
                for tc_dict in finish_tool_calls:
                    tc_id = tc_dict.get("id", f"tc_{len(tool_parts)}")
                    finish_tc_ids.add(tc_id)
                    tc_name = tc_dict.get("name", "")
                    tc_args = tc_dict.get("arguments", {})
                    if isinstance(tc_args, dict):
                        tc_args_str = json.dumps(tc_args)
                    else:
                        tc_args_str = str(tc_args)
                    if tc_id not in tool_parts:
                        idx = len(tool_parts)
                        yield lt.ToolCallDeltaEvent(
                            index=idx, id=tc_id, name=tc_name, arguments_delta=None
                        )
                        tool_parts[tc_id] = {"name": tc_name, "args": tc_args_str}
                    else:
                        idx = list(tool_parts.keys()).index(tc_id)
                    yield lt.ToolCallDeltaEvent(
                        index=idx, id=tc_id, name=None, arguments_delta=tc_args_str
                    )
                # Handle tool calls that were streamed as deltas but absent
                # from the finish event — validate raw accumulated args.
                for tc_id, parts in tool_parts.items():
                    if tc_id not in finish_tc_ids:
                        idx = list(tool_parts.keys()).index(tc_id)
                        raw = parts["args"]
                        try:
                            json.loads(raw)
                            validated = raw
                        except json.JSONDecodeError:
                            validated = "{}"
                        yield lt.ToolCallDeltaEvent(
                            index=idx, id=tc_id, name=None, arguments_delta=validated
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

                # Self-register the fingerprint of the assistant message this
                # call produced (content + tool_call IDs). When loom's iteration
                # loop appends the assembled ChatMessage to all_messages and
                # calls chat_stream again, the fingerprint will match and
                # _restore_reasoning will re-attach reasoning_content.
                if self._last_reasoning_content:
                    _content_text = "".join(_accumulated_content) or None
                    _tc_ids = tuple(
                        tc_dict.get("id", f"tc_{i}")
                        for i, tc_dict in enumerate(finish_tool_calls)
                    )
                    _fp = (
                        "assistant",
                        (_content_text or "")[:500],
                        _tc_ids,
                    )
                    self._reasoning_content_map[_fp] = self._last_reasoning_content
                _accumulated_content.clear()

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
