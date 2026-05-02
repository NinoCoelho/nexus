"""OpenAI-compatible HTTP adapter (no vendor SDK)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from loom.types import ToolSpec, Usage

from .auth import AuthStrategy
from .types import (
    ChatMessage,
    ChatResponse,
    ContentPart,
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

# Max pattern length to consider when scanning for runaway repetition.
# Catches single-char loops ("@@@@…"), short n-gram loops ("ababab…"), and
# small word loops ("lorem ipsum lorem ipsum…").
_REPEAT_MAX_PATTERN = 8


def _is_repeating_tail(text: str, threshold: int) -> bool:
    """Return True if the tail of ``text`` is a single ≤8-char pattern repeated
    for at least ``threshold`` characters. ``threshold <= 0`` disables.
    """
    if threshold <= 0 or len(text) < threshold:
        return False
    tail = text[-threshold:]
    for k in range(1, _REPEAT_MAX_PATTERN + 1):
        if threshold % k:
            continue
        pat = tail[:k]
        # Cheap early-out for the common case (single char like '@')
        if k == 1:
            if tail.count(pat) == threshold:
                return True
            continue
        if pat * (threshold // k) == tail:
            return True
    return False


def _encode_part_openai(part: ContentPart) -> dict[str, Any] | None:
    """Translate one ``ContentPart`` into OpenAI-compat multipart shape.

    Image parts read bytes from the vault and produce a ``data:`` URL
    (works on api.openai.com and on Google's ``/v1beta/openai`` shim).
    Audio parts emit ``input_audio`` blocks (gpt-4o-audio-preview style).
    The encoder assumes ``materialize_message`` has already lowered any
    parts the model can't handle natively to text — so unsupported kinds
    (e.g. document) simply fall back to a breadcrumb text block.
    """
    import base64

    if part.kind == "text":
        return {"type": "text", "text": part.text or ""}
    if part.kind == "image":
        from ...multimodal import read_vault_bytes, sniff_mime

        path = part.vault_path or ""
        mime = part.mime_type or sniff_mime(path)
        try:
            data = read_vault_bytes(path)
        except FileNotFoundError:
            return {"type": "text", "text": f"[image missing: {path}]"}
        b64 = base64.b64encode(data).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    if part.kind == "audio":
        from ...multimodal import read_vault_bytes, sniff_mime

        path = part.vault_path or ""
        mime = part.mime_type or sniff_mime(path)
        try:
            data = read_vault_bytes(path)
        except FileNotFoundError:
            return {"type": "text", "text": f"[audio missing: {path}]"}
        # OpenAI's ``input_audio.format`` accepts the bare extension
        # (``wav``, ``mp3``); normalise from the mime type.
        fmt = mime.split("/", 1)[-1]
        b64 = base64.b64encode(data).decode("ascii")
        return {"type": "input_audio", "input_audio": {"data": b64, "format": fmt}}
    # Document parts shouldn't reach the OpenAI encoder — materialize_message
    # extracts them to text — but emit a safe breadcrumb just in case.
    return {"type": "text", "text": f"[unsupported part kind: {part.kind}]"}


def _encode_msg(m: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role.value}
    if isinstance(m.content, list):
        encoded_parts: list[dict[str, Any]] = []
        for part in m.content:
            translated = _encode_part_openai(part)
            if translated is not None:
                encoded_parts.append(translated)
        out["content"] = encoded_parts
    elif m.content is not None:
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

    # Hostnames whose OpenAI-compat endpoint REJECTS unknown fields with
    # HTTP 400 INVALID_ARGUMENT instead of silently ignoring them. We
    # know about Google's Gemini compat layer (strict proto-validated);
    # any other provider that surfaces "Unknown name" 400s should be
    # added here. Detection by hostname is hacky but the alternative
    # (a per-provider quirks field on the catalog) is more plumbing.
    _STRICT_COMPAT_HOSTS = (
        "generativelanguage.googleapis.com",  # Gemini /v1beta/openai
    )

    def __init__(
        self,
        *,
        base_url: str,
        auth: AuthStrategy,
        model: str = "",
        read_timeout: float | None = None,
        temperature: float = 0.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        anti_repeat_threshold: int = 0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = float(temperature or 0.0)
        self._frequency_penalty = float(frequency_penalty or 0.0)
        self._presence_penalty = float(presence_penalty or 0.0)
        self._anti_repeat_threshold = int(anti_repeat_threshold or 0)
        self._auth = auth
        self._base_headers: dict[str, str] = {"Content-Type": "application/json"}
        # When the upstream rejects unknown fields (Gemini), strip
        # OpenAI-extensions from the payload before sending. Other
        # providers (OpenAI, Groq, Together, …) silently ignore unknown
        # fields, so leaving them on the wire is fine.
        self._strict_compat = any(h in self._base_url for h in self._STRICT_COMPAT_HOSTS)
        # Local reasoning models (GLM-4.7-flash, DeepSeek-R1, Qwen-QwQ, …) can
        # spend many minutes on the chain-of-thought before emitting any
        # `delta.content`. A 120s read timeout truncates them mid-thought; we
        # keep the connect timeout tight but let the read side run unbounded
        # by default. Callers that issue non-streaming batch requests
        # (e.g. GraphRAG extraction) should pass an explicit ``read_timeout``
        # to bound a hung Ollama from blocking the event loop indefinitely.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=read_timeout, write=60.0, pool=10.0)
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> ChatResponse:
        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")
        from ...multimodal import materialize_messages
        from ...providers.catalog import capabilities_for_model_name

        caps = capabilities_for_model_name(resolved_model)
        prepared = await materialize_messages(messages, caps)
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": [_encode_msg(m) for m in prepared],
            "temperature": self._temperature,
        }
        # OpenAI-extensions: silently ignored by most compat providers
        # but rejected outright by some (Gemini's strict proto-validated
        # compat endpoint). _strict_compat omits them entirely.
        if not self._strict_compat:
            if self._frequency_penalty:
                payload["frequency_penalty"] = self._frequency_penalty
            if self._presence_penalty:
                payload["presence_penalty"] = self._presence_penalty
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
            payload["tool_choice"] = "auto"
        # Pass-through for caller-supplied extras (e.g. voice_ack disables
        # extended thinking via {"thinking": {"type": "disabled"}} so the
        # ack call doesn't burn its token budget on internal reasoning).
        # Most OpenAI-compat providers silently ignore unknown fields;
        # the strict ones (Gemini compat) reject them, so we skip there.
        if extra_payload and not self._strict_compat:
            for k, v in extra_payload.items():
                if k not in payload:  # never overwrite explicit fields
                    payload[k] = v

        try:
            resp = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={**self._base_headers, **(await self._auth.headers())},
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
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")
        from ...multimodal import materialize_messages
        from ...providers.catalog import capabilities_for_model_name

        caps = capabilities_for_model_name(resolved_model)
        prepared = await materialize_messages(messages, caps)
        payload: dict[str, Any] = {
            "model": resolved_model,
            "messages": [_encode_msg(m) for m in prepared],
            "temperature": self._temperature,
            "stream": True,
        }
        # See chat() — same strict-compat gate.
        if not self._strict_compat:
            if self._frequency_penalty:
                payload["frequency_penalty"] = self._frequency_penalty
            if self._presence_penalty:
                payload["presence_penalty"] = self._presence_penalty
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if tools:
            payload["tools"] = [_encode_tool(t) for t in tools]
            payload["tool_choice"] = "auto"

        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={**self._base_headers, **(await self._auth.headers())},
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

                # Anti-repetition guard. Tripped if the trailing N chars are a
                # single ≤8-char pattern repeated; once tripped we synthesize a
                # finish event and break so the runaway tail can't grow further.
                aborted_repeat = False
                repeat_threshold = self._anti_repeat_threshold

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

                    # Reasoning delta (chain-of-thought from thinking models —
                    # GLM-4.7-flash, DeepSeek-R1, Qwen-QwQ — exposed by Ollama
                    # as `delta.reasoning`, by some OpenAI proxies as
                    # `delta.reasoning_content`). We surface it as a separate
                    # event type so consumers can display it without it being
                    # appended to assistant content / persisted to history.
                    reasoning_piece = delta.get("reasoning") or delta.get("reasoning_content")
                    if reasoning_piece:
                        yield {"type": "thinking_delta", "text": reasoning_piece}

                    # Text delta
                    text_piece = delta.get("content")
                    if text_piece:
                        full_text += text_piece
                        yield {"type": "delta", "text": text_piece}
                        if repeat_threshold and _is_repeating_tail(full_text, repeat_threshold):
                            aborted_repeat = True
                            break

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

                if aborted_repeat:
                    # Provider never sent a finish_reason (we cut the stream
                    # short). Synthesize one so downstream consumers terminate
                    # cleanly. ``abort_reason`` is advisory metadata.
                    yield {
                        "type": "finish",
                        "finish_reason": StopReason.STOP.value,
                        "content": full_text,
                        "tool_calls": [],
                        "usage": stream_usage.model_dump(),
                        "abort_reason": "repetition",
                    }
        except httpx.HTTPError as exc:
            raise LLMTransportError(str(exc)) from exc

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._auth.aclose()
