"""Anthropic native adapter using the anthropic SDK."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from loom.types import Role, ToolSpec, Usage

from .types import (
    ChatMessage,
    ChatResponse,
    LLMError,
    LLMProvider,
    StopReason,
    StreamEvent,
    ToolCall,
)


def _encode_msg_anthropic(m: ChatMessage) -> dict[str, Any]:
    if m.role == Role.TOOL:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content or "",
                }
            ],
        }
    if m.tool_calls:
        content: list[dict[str, Any]] = []
        if m.content:
            content.append({"type": "text", "text": m.content})
        for tc in m.tool_calls:
            content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
            )
        return {"role": "assistant", "content": content}
    return {"role": m.role.value, "content": m.content or ""}


def _encode_tool_anthropic(t: ToolSpec) -> dict[str, Any]:
    return {"name": t.name, "description": t.description, "input_schema": t.parameters}


def _decode_anthropic(resp: Any) -> ChatResponse:
    tool_calls: list[ToolCall] = []
    text_parts: list[str] = []
    for block in resp.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
    stop_reason = StopReason.TOOL_USE if tool_calls else StopReason.STOP
    if resp.stop_reason == "max_tokens":
        stop_reason = StopReason.LENGTH
    return ChatResponse(
        content="\n".join(text_parts) or None,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=_usage_from_anthropic(getattr(resp, "usage", None)),
    )


def _usage_from_anthropic(raw: Any) -> Usage:
    """Map Anthropic's ``MessageUsage`` to our :class:`Usage`.

    Anthropic reports ``input_tokens``, ``output_tokens``, plus
    ``cache_read_input_tokens`` and ``cache_creation_input_tokens`` when
    prompt caching is engaged.
    """
    if raw is None:
        return Usage()
    return Usage(
        input_tokens=int(getattr(raw, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(raw, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(raw, "cache_creation_input_tokens", 0) or 0),
    )


class AnthropicProvider(LLMProvider):
    """Anthropic native adapter using the anthropic SDK."""

    def __init__(self, *, api_key: str, model: str = "", temperature: float = 0.0) -> None:
        import anthropic  # type: ignore[import]

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._temperature = float(temperature or 0.0)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResponse:
        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")
        system = ""
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                system = m.content or ""
            else:
                filtered.append(_encode_msg_anthropic(m))

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": 4096,
            "messages": filtered,
            "temperature": self._temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_encode_tool_anthropic(t) for t in tools]

        resp = await self._client.messages.create(**kwargs)
        return _decode_anthropic(resp)

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")
        system = ""
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                system = m.content or ""
            else:
                filtered.append(_encode_msg_anthropic(m))

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": 4096,
            "messages": filtered,
            "temperature": self._temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_encode_tool_anthropic(t) for t in tools]

        full_text = ""
        tool_bufs: dict[str, dict[str, Any]] = {}  # id -> {name, args_buf}

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                etype = event.type

                if etype == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        tool_bufs[block.id] = {"name": block.name, "args_buf": ""}
                        yield {"type": "tool_call_start", "id": block.id, "name": block.name}

                elif etype == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        full_text += delta.text
                        yield {"type": "delta", "text": delta.text}
                    elif delta.type == "input_json_delta":
                        # Find the block being built — Anthropic gives us index
                        block_id = None
                        # event.index tells us position; map to id via order
                        for tid, buf in tool_bufs.items():
                            if buf.get("_index") == event.index:
                                block_id = tid
                                break
                        if block_id is None:
                            # store index on first delta for this block
                            for tid, buf in tool_bufs.items():
                                if "_index" not in buf:
                                    buf["_index"] = event.index
                                    block_id = tid
                                    break
                        if block_id and block_id in tool_bufs:
                            tool_bufs[block_id]["args_buf"] += delta.partial_json
                            yield {"type": "tool_call_delta", "id": block_id, "args_delta": delta.partial_json}

                elif etype == "content_block_stop":
                    # Identify which tool call ended by index
                    block_id = None
                    for tid, buf in tool_bufs.items():
                        if buf.get("_index") == event.index:
                            block_id = tid
                            break
                    if block_id:
                        yield {"type": "tool_call_end", "id": block_id}

                elif etype == "message_stop":
                    tool_calls: list[dict[str, Any]] = []
                    for tc_id, buf in tool_bufs.items():
                        try:
                            args = json.loads(buf["args_buf"] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append({"id": tc_id, "name": buf["name"], "arguments": args})

                    msg = await stream.get_final_message()
                    finish_reason = "tool_use" if tool_calls else "stop"
                    if msg.stop_reason == "max_tokens":
                        finish_reason = "length"
                    yield {
                        "type": "finish",
                        "finish_reason": finish_reason,
                        "content": full_text,
                        "tool_calls": tool_calls,
                        "usage": _usage_from_anthropic(getattr(msg, "usage", None)).model_dump(),
                    }

    async def aclose(self) -> None:
        await self._client.close()
