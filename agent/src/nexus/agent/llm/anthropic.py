"""Anthropic native adapter using the anthropic SDK."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from loom.types import Role, ToolSpec, Usage

log = logging.getLogger(__name__)

from .types import (
    ChatMessage,
    ChatResponse,
    ContentPart,
    LLMError,
    LLMProvider,
    StopReason,
    StreamEvent,
    ToolCall,
)


def _encode_part_anthropic(part: ContentPart) -> dict[str, Any]:
    """Translate one ``ContentPart`` into Anthropic native block shape.

    Image parts read bytes from the vault and emit a ``base64`` source
    block. Document (PDF) parts ride Anthropic's native ``document``
    block. The encoder assumes ``materialize_message`` has already
    lowered any parts the model can't handle natively to text.
    """
    import base64

    if part.kind == "text":
        return {"type": "text", "text": part.text or ""}
    if part.kind in ("image", "document"):
        from ...multimodal import read_vault_bytes, sniff_mime

        path = part.vault_path or ""
        mime = part.mime_type or sniff_mime(path)
        try:
            data = read_vault_bytes(path)
        except FileNotFoundError:
            return {"type": "text", "text": f"[{part.kind} missing: {path}]"}
        b64 = base64.b64encode(data).decode("ascii")
        return {
            "type": part.kind,
            "source": {"type": "base64", "media_type": mime, "data": b64},
        }
    # Audio is not natively supported by Anthropic; materialize_message
    # transcribes audio away before we get here. Safe breadcrumb fallback.
    return {"type": "text", "text": f"[unsupported part kind: {part.kind}]"}


def _encode_message_content_anthropic(content: Any) -> Any:
    """Encode :class:`ChatMessage.content` for Anthropic — string, list of
    :class:`ContentPart`, or ``None``."""
    if isinstance(content, list):
        return [_encode_part_anthropic(p) for p in content]
    return content or ""


def _encode_msg_anthropic(m: ChatMessage) -> dict[str, Any]:
    if m.role == Role.TOOL:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": _encode_message_content_anthropic(m.content),
                }
            ],
        }
    if m.tool_calls:
        content: list[dict[str, Any]] = []
        if isinstance(m.content, list):
            content.extend(_encode_part_anthropic(p) for p in m.content)
        elif m.content:
            content.append({"type": "text", "text": m.content})
        for tc in m.tool_calls:
            content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
            )
        return {"role": "assistant", "content": content}
    if isinstance(m.content, list):
        return {"role": m.role.value, "content": [_encode_part_anthropic(p) for p in m.content]}
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
    """Anthropic native adapter using the anthropic SDK.

    Supports two auth modes:

    * ``api_key`` — standard ``x-api-key`` header. Used for Anthropic
      Workbench / Console keys (sk-ant-…).
    * ``oauth_access_token`` — ``Authorization: Bearer`` + the OAuth
      beta header. Used by Claude Pro / Max sessions claimed from
      ``claude-code``'s local credential store. Requires the SDK's
      ``auth_token`` parameter (anthropic >= 0.40).
    """

    # Beta flag the Anthropic API requires when authenticating with an
    # OAuth bundle (as opposed to a workbench API key). Tracked at
    # https://docs.anthropic.com — bumped here when Anthropic changes it.
    OAUTH_BETA_HEADER = "oauth-2025-04-20"

    # Identity headers the official Claude Code CLI sends. We attach
    # them when authenticating with a Pro/Max OAuth bundle so Anthropic's
    # subscription rate-limit allocation applies to our calls. Without
    # these, OAuth bundles get throttled aggressively (HTTP 429 even
    # when the user's monthly quota is nowhere near exhausted) because
    # Anthropic treats unidentified third-party callers separately from
    # the official client.
    #
    # ToS posture: the Pro/Max subscription is intended for use through
    # Anthropic's official products. Sending these headers is a more
    # explicit form of impersonation than just lifting the OAuth bundle;
    # we do it only when the user has opted into ``local_claude_code``
    # (the wizard step that disclaims this), never on a plain API key.
    # Match the installed Claude Code version. Update when bumping; Anthropic
    # rate-limit allocation is keyed on the User-Agent + x-app combo identifying
    # the call as coming from the official client. Found via:
    #   `claude --version` → "2.1.112 (Claude Code)"
    # The CLI binary sends "claude-cli/<version> (external, cli)" — the
    # VS Code extension uses "x-app: vscode" with the same UA shape.
    _CLAUDE_CODE_USER_AGENT = "claude-cli/2.1.112 (external, cli)"
    _CLAUDE_CODE_X_APP = "cli"

    def __init__(
        self,
        *,
        api_key: str = "",
        oauth_access_token: str = "",
        model: str = "",
        temperature: float = 0.0,
        impersonate_claude_code: bool = False,
    ) -> None:
        import anthropic  # type: ignore[import]

        if oauth_access_token:
            # The SDK's `auth_token` swap to Bearer; the beta header is
            # required by Anthropic for OAuth-authed requests.
            headers: dict[str, str] = {"anthropic-beta": self.OAUTH_BETA_HEADER}
            if impersonate_claude_code:
                headers["User-Agent"] = self._CLAUDE_CODE_USER_AGENT
                headers["x-app"] = self._CLAUDE_CODE_X_APP
            self._client = anthropic.AsyncAnthropic(
                auth_token=oauth_access_token,
                default_headers=headers,
            )
        else:
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._temperature = float(temperature or 0.0)

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
        system: Any = ""
        filtered: list[dict[str, Any]] = []
        for m in prepared:
            if m.role == Role.SYSTEM:
                system = m.content if isinstance(m.content, str) else (m.content or "")
            else:
                filtered.append(_encode_msg_anthropic(m))

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": int(max_tokens) if max_tokens else 4096,
            "messages": filtered,
            "temperature": self._temperature,
        }
        # Caller-supplied extras (e.g. voice_ack disables extended thinking
        # via {"thinking": {"type": "disabled"}}). Anthropic accepts a
        # `thinking` field natively. Other extras are passed through; the
        # SDK will reject anything it doesn't recognize.
        if extra_payload:
            for k, v in extra_payload.items():
                if k not in kwargs:
                    kwargs[k] = v
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
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")
        from ...multimodal import materialize_messages
        from ...providers.catalog import capabilities_for_model_name

        caps = capabilities_for_model_name(resolved_model)
        prepared = await materialize_messages(messages, caps)
        system: Any = ""
        filtered: list[dict[str, Any]] = []
        for m in prepared:
            if m.role == Role.SYSTEM:
                system = m.content if isinstance(m.content, str) else (m.content or "")
            else:
                filtered.append(_encode_msg_anthropic(m))

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "max_tokens": int(max_tokens) if max_tokens else 4096,
            "messages": filtered,
            "temperature": self._temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_encode_tool_anthropic(t) for t in tools]

        full_text = ""
        tool_bufs: dict[str, dict[str, Any]] = {}  # id -> {name, args_buf}
        sdk_event_count = 0

        log.warning(
            "AnthropicProvider.chat_stream → model=%s msgs=%d tools=%d max_toks=%s",
            resolved_model, len(filtered), len(tools or []), kwargs.get("max_tokens"),
        )
        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                sdk_event_count += 1
                etype = event.type
                if sdk_event_count <= 3:
                    log.warning(
                        "AnthropicProvider SDK event #%d type=%s",
                        sdk_event_count, etype,
                    )

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
        log.warning(
            "AnthropicProvider.chat_stream finished — sdk_events=%d",
            sdk_event_count,
        )

    async def aclose(self) -> None:
        await self._client.close()
