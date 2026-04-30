"""AWS Bedrock adapter using boto3's Converse API.

Converse is Bedrock's normalized chat surface — same shape across
Anthropic Claude, Meta Llama, Mistral, Amazon Nova, etc. We translate
inbound Nexus ChatMessages → Converse messages, call
``bedrock-runtime``, and translate the response back.

boto3 is an optional dep (``nexus[bedrock]``); this module only
imports it inside ``__init__`` so the rest of Nexus loads even when
boto3 isn't installed.
"""

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
    LLMTransportError,
    StopReason,
    StreamEvent,
    ToolCall,
)


def _encode_msg_bedrock(m: ChatMessage) -> dict[str, Any]:
    """Bedrock Converse format: ``{role, content: [{text|toolUse|toolResult}]}``.

    Mirrors the Anthropic mapping closely since most users on Bedrock
    are calling Claude through it.
    """
    if m.role == Role.TOOL:
        return {
            "role": "user",
            "content": [
                {
                    "toolResult": {
                        "toolUseId": m.tool_call_id or "",
                        "content": [{"text": m.content or ""}],
                    }
                }
            ],
        }
    if m.tool_calls:
        content: list[dict[str, Any]] = []
        if m.content:
            content.append({"text": m.content})
        for tc in m.tool_calls:
            content.append(
                {
                    "toolUse": {
                        "toolUseId": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                }
            )
        return {"role": "assistant", "content": content}
    role = m.role.value
    if role == "system":
        # Caller is expected to extract system messages out of the list
        # before calling — Converse takes them as a separate top-level
        # ``system`` parameter.
        raise ValueError("system messages must be filtered out before _encode_msg_bedrock")
    return {"role": role, "content": [{"text": m.content or ""}]}


def _encode_tool_bedrock(t: ToolSpec) -> dict[str, Any]:
    return {
        "toolSpec": {
            "name": t.name,
            "description": t.description,
            "inputSchema": {"json": t.parameters},
        }
    }


def _decode_bedrock(resp: dict[str, Any]) -> ChatResponse:
    output = resp.get("output", {})
    msg = output.get("message", {})
    tool_calls: list[ToolCall] = []
    text_parts: list[str] = []
    for block in msg.get("content", []):
        if "text" in block:
            text_parts.append(block["text"])
        elif "toolUse" in block:
            tu = block["toolUse"]
            tool_calls.append(
                ToolCall(
                    id=tu.get("toolUseId", ""),
                    name=tu.get("name", ""),
                    arguments=tu.get("input") or {},
                )
            )
    stop_reason_raw = resp.get("stopReason", "end_turn")
    stop_reason = (
        StopReason.TOOL_USE if tool_calls
        else StopReason.LENGTH if stop_reason_raw == "max_tokens"
        else StopReason.STOP
    )
    usage_raw = resp.get("usage") or {}
    usage = Usage(
        input_tokens=int(usage_raw.get("inputTokens", 0) or 0),
        output_tokens=int(usage_raw.get("outputTokens", 0) or 0),
        cache_read_tokens=int(usage_raw.get("cacheReadInputTokens", 0) or 0),
        cache_write_tokens=int(usage_raw.get("cacheWriteInputTokens", 0) or 0),
    )
    return ChatResponse(
        content="\n".join(text_parts) or None,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=usage,
    )


class BedrockProvider(LLMProvider):
    """Bedrock Converse adapter. Optional dep: ``nexus[bedrock]`` (boto3).

    Uses the AWS credential chain: explicit ``profile`` first (if set),
    otherwise default chain (env vars / ~/.aws/credentials / SSO / IAM
    role / instance profile). All signing happens inside botocore.
    """

    def __init__(
        self,
        *,
        region: str,
        profile: str = "",
        model: str = "",
        temperature: float = 0.0,
    ) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise LLMError(
                "Bedrock provider requires the 'bedrock' optional install — "
                "run `uv pip install 'nexus[bedrock]'` (or "
                "`pip install boto3` in a non-uv setup)."
            ) from exc

        session_kwargs: dict[str, Any] = {}
        if profile:
            session_kwargs["profile_name"] = profile
        if region:
            session_kwargs["region_name"] = region
        session = boto3.Session(**session_kwargs)
        self._client = session.client("bedrock-runtime")
        self._model = model
        self._temperature = float(temperature or 0.0)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        import asyncio
        import botocore.exceptions  # type: ignore[import-not-found]

        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")

        system: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                if m.content:
                    system.append({"text": m.content})
            else:
                filtered.append(_encode_msg_bedrock(m))

        kwargs: dict[str, Any] = {
            "modelId": resolved_model,
            "messages": filtered,
            "inferenceConfig": {
                "temperature": self._temperature,
                "maxTokens": int(max_tokens) if max_tokens else 4096,
            },
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["toolConfig"] = {"tools": [_encode_tool_bedrock(t) for t in tools]}

        try:
            # boto3's bedrock-runtime client is synchronous; offload to a
            # thread so we don't block the event loop. The cost is one
            # extra hop per request; acceptable for the chat path.
            resp = await asyncio.to_thread(self._client.converse, **kwargs)
        except botocore.exceptions.ClientError as exc:
            err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
            code = err.get("Code", "")
            msg = err.get("Message", str(exc))
            raise LLMTransportError(
                f"Bedrock {code}: {msg}",
                status_code=int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
                if hasattr(exc, "response") else None,
            ) from exc
        except botocore.exceptions.BotoCoreError as exc:
            raise LLMTransportError(f"Bedrock transport: {exc!s}") from exc

        return _decode_bedrock(resp)

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming via Bedrock's ConverseStream — boto3 yields events
        synchronously, we re-yield as Nexus stream dicts.

        Implementation note: we proxy by collecting in a thread queue
        because boto3's response streams aren't async-iterable. For v1
        we synthesize a single ``finish`` event at the end carrying the
        accumulated content + tool_calls; mid-stream deltas are surfaced
        per chunk to keep the UI lively.
        """
        import asyncio
        import botocore.exceptions  # type: ignore[import-not-found]

        resolved_model = model or self._model
        if not resolved_model:
            raise LLMError("No model specified: pass model= or set a default at construction")

        system: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        for m in messages:
            if m.role == Role.SYSTEM:
                if m.content:
                    system.append({"text": m.content})
            else:
                filtered.append(_encode_msg_bedrock(m))

        kwargs: dict[str, Any] = {
            "modelId": resolved_model,
            "messages": filtered,
            "inferenceConfig": {
                "temperature": self._temperature,
                "maxTokens": int(max_tokens) if max_tokens else 4096,
            },
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["toolConfig"] = {"tools": [_encode_tool_bedrock(t) for t in tools]}

        try:
            resp = await asyncio.to_thread(self._client.converse_stream, **kwargs)
        except botocore.exceptions.ClientError as exc:
            err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
            raise LLMTransportError(
                f"Bedrock {err.get('Code', '')}: {err.get('Message', str(exc))}",
                status_code=int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
                if hasattr(exc, "response") else None,
            ) from exc

        full_text = ""
        tool_bufs: dict[str, dict[str, Any]] = {}
        # Bedrock streams contentBlockIndex per block; we map index → tool buf.
        index_to_id: dict[int, str] = {}
        usage = Usage()
        finish_reason = "stop"

        # Pull events off the synchronous boto3 generator in a thread.
        def _drain() -> list[dict[str, Any]]:
            return list(resp.get("stream", []))

        events = await asyncio.to_thread(_drain)
        for ev in events:
            if "messageStart" in ev:
                continue
            if "contentBlockStart" in ev:
                start = ev["contentBlockStart"].get("start", {})
                idx = ev["contentBlockStart"].get("contentBlockIndex", 0)
                if "toolUse" in start:
                    tu = start["toolUse"]
                    tc_id = tu.get("toolUseId", f"tc_{idx}")
                    name = tu.get("name", "")
                    index_to_id[idx] = tc_id
                    tool_bufs[tc_id] = {"name": name, "args_buf": ""}
                    yield {"type": "tool_call_start", "id": tc_id, "name": name}
                continue
            if "contentBlockDelta" in ev:
                delta = ev["contentBlockDelta"].get("delta", {})
                idx = ev["contentBlockDelta"].get("contentBlockIndex", 0)
                if "text" in delta:
                    full_text += delta["text"]
                    yield {"type": "delta", "text": delta["text"]}
                elif "toolUse" in delta:
                    tc_id = index_to_id.get(idx)
                    args_delta = delta["toolUse"].get("input", "")
                    if tc_id and args_delta:
                        tool_bufs[tc_id]["args_buf"] += args_delta
                        yield {"type": "tool_call_delta", "id": tc_id, "args_delta": args_delta}
                continue
            if "contentBlockStop" in ev:
                idx = ev["contentBlockStop"].get("contentBlockIndex", 0)
                tc_id = index_to_id.get(idx)
                if tc_id:
                    yield {"type": "tool_call_end", "id": tc_id}
                continue
            if "messageStop" in ev:
                finish_reason = ev["messageStop"].get("stopReason", "end_turn")
                continue
            if "metadata" in ev:
                u = ev["metadata"].get("usage", {})
                usage = Usage(
                    input_tokens=int(u.get("inputTokens", 0) or 0),
                    output_tokens=int(u.get("outputTokens", 0) or 0),
                )
                continue

        tool_calls: list[dict[str, Any]] = []
        for tc_id, buf in tool_bufs.items():
            try:
                args = json.loads(buf["args_buf"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({"id": tc_id, "name": buf["name"], "arguments": args})

        mapped = (
            StopReason.TOOL_USE.value if tool_calls
            else StopReason.LENGTH.value if finish_reason == "max_tokens"
            else StopReason.STOP.value
        )
        yield {
            "type": "finish",
            "finish_reason": mapped,
            "content": full_text,
            "tool_calls": tool_calls,
            "usage": usage.model_dump(),
        }

    async def aclose(self) -> None:
        # boto3 clients don't expose async close. Default no-op.
        return None
