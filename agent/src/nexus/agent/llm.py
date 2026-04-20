"""LLM provider port — OpenAI-compatible + Anthropic native adapters."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

# StreamEvent is a plain TypedDict-style dict union; we use plain dicts for
# zero-overhead yielding.  The "type" key is the discriminator.
StreamEvent = dict[str, Any]


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    arguments: dict[str, Any]


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    description: str
    parameters: dict[str, Any]


class StopReason(StrEnum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    ERROR = "error"
    UNKNOWN = "unknown"


class Usage(BaseModel):
    """Token usage for one provider round-trip.

    All fields default to 0 so callers can always read them without
    ``None`` guards. Providers that don't report usage (older local
    models, some proxies) just yield zeros — that's fine, it just
    means the session's cost stays at $0 for that turn.
    """

    model_config = ConfigDict(frozen=True)
    input_tokens: int = 0
    output_tokens: int = 0
    # Reserved for Anthropic cache-aware accounting — we capture but
    # don't bill against it yet.
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason = StopReason.STOP
    usage: Usage = Field(default_factory=Usage)


class LLMError(Exception):
    pass


class LLMTransportError(LLMError):
    """Raised on any upstream transport failure.

    Carries the HTTP status code and parsed response body (if JSON) so
    :mod:`nexus.error_classifier` can walk the exception and build a rich
    :class:`ClassifiedError`. Fields default to ``None`` / ``{}`` for
    lower-layer failures (e.g. pure network errors) where there is no
    response to parse.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


class MalformedOutputError(LLMError):
    pass


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResponse: ...

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # Default fallback: call non-streaming chat and synthesize events.
        resp = await self.chat(messages, tools=tools, model=model)
        if resp.content:
            yield {"type": "delta", "text": resp.content}
        finish_reason = resp.stop_reason.value
        yield {
            "type": "finish",
            "finish_reason": finish_reason,
            "content": resp.content or "",
            "tool_calls": [tc.model_dump() for tc in resp.tool_calls],
        }

    async def aclose(self) -> None:
        return


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible HTTP adapter (no vendor SDK)."""

    def __init__(self, *, base_url: str, api_key: str, model: str = "") -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(timeout=120.0)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": [_encode_msg(m) for m in messages],
            "temperature": 0.0,
        }
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
            payload["tool_choice"] = "auto"

        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            # Transport-layer failure — no status code / body to attach.
            raise LLMTransportError(str(exc)) from exc

        if resp.status_code >= 400:
            # Attach the parsed body when possible so the error_classifier
            # can reach nested provider messages (OpenRouter wraps upstream
            # errors under error.metadata.raw, for instance).
            body: dict[str, Any] = {}
            try:
                body = resp.json()
                if not isinstance(body, dict):
                    body = {}
            except Exception:
                body = {}
            raise LLMTransportError(
                f"HTTP {resp.status_code}: {resp.text[:400]}",
                status_code=resp.status_code,
                body=body,
            )

        try:
            return _decode_openai(resp.json())
        except (KeyError, ValueError, TypeError) as exc:
            raise MalformedOutputError(str(exc)) from exc

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": [_encode_msg(m) for m in messages],
            "temperature": 0.0,
            "stream": True,
        }
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
            payload["tool_choice"] = "auto"

        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers,
            ) as resp:
                if resp.status_code >= 400:
                    raw = await resp.aread()
                    # Decode upstream error body so the error surfaces as
                    # real JSON text instead of a Python b'...' repr, and
                    # parse it into the structured body carried by the
                    # exception for the error_classifier to walk.
                    text = raw.decode("utf-8", errors="replace")
                    parsed: dict[str, Any] = {}
                    try:
                        parsed = json.loads(text)
                        if not isinstance(parsed, dict):
                            parsed = {}
                    except json.JSONDecodeError:
                        parsed = {}
                    raise LLMTransportError(
                        f"HTTP {resp.status_code}: {text[:400]}",
                        status_code=resp.status_code,
                        body=parsed,
                    )

                # Aggregated state for the finish event
                full_text = ""
                # id -> {name, args_buf}
                tool_bufs: dict[str, dict[str, Any]] = {}

                # Many OpenAI-compat providers emit the `usage` object only
                # on the final SSE frame after all choice deltas — we stash
                # it here and attach it to our synthesized `finish` event.
                stream_usage = Usage()

                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    raw_usage = chunk.get("usage")
                    if raw_usage:
                        stream_usage = _usage_from_openai(raw_usage)

                    choices = chunk.get("choices")
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    finish_reason = choices[0].get("finish_reason")

                    # Text delta
                    text_piece = delta.get("content")
                    if text_piece:
                        full_text += text_piece
                        yield {"type": "delta", "text": text_piece}

                    # Tool call deltas
                    for tc_delta in delta.get("tool_calls") or []:
                        idx = tc_delta.get("index", 0)
                        tc_id = tc_delta.get("id", f"tc_{idx}")
                        fn = tc_delta.get("function", {})
                        tc_name = fn.get("name", "")
                        args_delta = fn.get("arguments", "")

                        if tc_id not in tool_bufs and tc_name:
                            tool_bufs[tc_id] = {"name": tc_name, "args_buf": ""}
                            yield {"type": "tool_call_start", "id": tc_id, "name": tc_name}

                        if args_delta and tc_id in tool_bufs:
                            tool_bufs[tc_id]["args_buf"] += args_delta
                            yield {"type": "tool_call_delta", "id": tc_id, "args_delta": args_delta}

                    if finish_reason is not None:
                        # Emit tool_call_end for each accumulated tool call
                        tool_calls: list[dict[str, Any]] = []
                        for tc_id, buf in tool_bufs.items():
                            yield {"type": "tool_call_end", "id": tc_id}
                            try:
                                args = json.loads(buf["args_buf"] or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            tool_calls.append({"id": tc_id, "name": buf["name"], "arguments": args})

                        mapped = _FINISH_MAP.get(finish_reason or "stop", StopReason.UNKNOWN).value
                        yield {
                            "type": "finish",
                            "finish_reason": mapped,
                            "content": full_text,
                            "tool_calls": tool_calls,
                            "usage": stream_usage.model_dump(),
                        }
        except httpx.HTTPError as exc:
            raise LLMTransportError(str(exc)) from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class AnthropicProvider(LLMProvider):
    """Anthropic native adapter using the anthropic SDK."""

    def __init__(self, *, api_key: str, model: str = "") -> None:
        import anthropic  # type: ignore[import]

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResponse:
        system = ""
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                system = m.content or ""
            else:
                filtered.append(_encode_msg_anthropic(m))

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": 4096,
            "messages": filtered,
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
        system = ""
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                system = m.content or ""
            else:
                filtered.append(_encode_msg_anthropic(m))

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": 4096,
            "messages": filtered,
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
                    finish_reason = "tool_calls" if tool_calls else "stop"
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


# --- encoding helpers ---------------------------------------------------------

def _encode_msg(m: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role.value}
    if m.content is not None:
        out["content"] = m.content
    if m.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in m.tool_calls
        ]
    if m.tool_call_id is not None:
        out["tool_call_id"] = m.tool_call_id
    if m.name is not None:
        out["name"] = m.name
    return out


def _encode_tool(t: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
    }


_FINISH_MAP = {
    "stop": StopReason.STOP,
    "tool_calls": StopReason.TOOL_CALLS,
    "function_call": StopReason.TOOL_CALLS,
    "length": StopReason.LENGTH,
}


def _decode_openai(data: dict[str, Any]) -> ChatResponse:
    choices = data.get("choices")
    if not choices:
        raise ValueError("no choices in response")
    choice = choices[0]
    msg = choice.get("message", {})
    tool_calls: list[ToolCall] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        args_raw = fn.get("arguments", "{}")
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        if not isinstance(args, dict):
            raise ValueError(f"tool args not an object: {args_raw!r}")
        tool_calls.append(ToolCall(id=tc.get("id", fn.get("name", "")), name=fn["name"], arguments=args))
    stop_reason = _FINISH_MAP.get(choice.get("finish_reason") or "stop", StopReason.UNKNOWN)
    return ChatResponse(
        content=msg.get("content"),
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=_usage_from_openai(data.get("usage") or {}),
    )


def _usage_from_openai(raw: dict[str, Any]) -> Usage:
    """Map an OpenAI-compat ``usage`` object to our :class:`Usage`.

    Accepts the fields OpenAI documents (``prompt_tokens``,
    ``completion_tokens``) plus a few vendor extensions we've seen in
    the wild (``prompt_tokens_details.cached_tokens`` from OpenAI's
    own caching rollout; ``cache_read_input_tokens`` /
    ``cache_creation_input_tokens`` from Anthropic proxies).
    """
    if not isinstance(raw, dict):
        return Usage()
    input_tokens = int(raw.get("prompt_tokens") or 0)
    output_tokens = int(raw.get("completion_tokens") or 0)
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    cache_write = int(raw.get("cache_creation_input_tokens") or 0)
    details = raw.get("prompt_tokens_details") or {}
    if isinstance(details, dict):
        cache_read = cache_read or int(details.get("cached_tokens") or 0)
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
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
    stop_reason = StopReason.TOOL_CALLS if tool_calls else StopReason.STOP
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
