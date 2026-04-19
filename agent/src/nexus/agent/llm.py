"""LLM provider port — OpenAI-compatible + Anthropic native adapters."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field


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


class ChatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason = StopReason.STOP


class LLMError(Exception):
    pass


class LLMTransportError(LLMError):
    pass


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
            raise LLMTransportError(str(exc)) from exc

        if resp.status_code >= 400:
            raise LLMTransportError(f"HTTP {resp.status_code}: {resp.text[:400]}")

        try:
            return _decode_openai(resp.json())
        except (KeyError, ValueError, TypeError) as exc:
            raise MalformedOutputError(str(exc)) from exc

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
    return ChatResponse(content=msg.get("content"), tool_calls=tool_calls, stop_reason=stop_reason)


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
    )
