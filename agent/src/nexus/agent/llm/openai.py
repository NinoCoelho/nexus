"""OpenAI-compatible HTTP adapter (no vendor SDK)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from loom.types import ToolSpec, Usage

from .types import (
    ChatMessage,
    ChatResponse,
    LLMError,
    LLMProvider,
    LLMTransportError,
    MalformedOutputError,
    StopReason,
    StreamEvent,
    ToolCall,
)

_FINISH_MAP = {
    "stop": StopReason.STOP,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "length": StopReason.LENGTH,
}


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
        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")
        payload: dict[str, Any] = {
            "model": resolved_model,
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
        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")
        payload: dict[str, Any] = {
            "model": resolved_model,
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
