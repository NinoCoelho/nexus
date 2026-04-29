"""Core types and abstract base for LLM providers.

Types here are structurally aligned with :mod:`loom.types` so Nexus can
eventually plug into :class:`loom.Agent` without translation. ``Role``,
``ToolSpec``, and ``Usage`` are imported directly from Loom; the others
shadow Loom's shape locally because Nexus's provider encoding depends on
them being Pydantic (and on small defaults tweaks).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Types shared verbatim with Loom.
from loom.types import Role, ToolSpec, Usage  # re-exported for Nexus imports

# StreamEvent is a plain dict union with ``type`` as the discriminator —
# kept as dict (not Loom's Pydantic union) so Nexus's provider adapters
# can yield cheaply without model construction on every token. Converting
# this to Pydantic is a separate, much larger refactor.
StreamEvent = dict[str, Any]


class ToolCall(BaseModel):
    """Tool-call request from the model.

    ``arguments`` is a **parsed dict** (intentional divergence from
    :class:`loom.types.ToolCall`, which uses a JSON string). Nexus's
    tool dispatchers consume dicts directly; we parse once at decode
    time instead of at every dispatch site. Use :meth:`arguments_json`
    to get the loom-style string form when interoperating.
    """

    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    arguments: dict[str, Any]

    @property
    def arguments_json(self) -> str:
        return json.dumps(self.arguments)


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class StopReason(StrEnum):
    STOP = "stop"
    TOOL_USE = "tool_use"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


class ChatResponse(BaseModel):
    """One provider round-trip.

    Flat shape (``content`` + ``tool_calls`` at top level) rather than
    Loom's wrapped ``message: ChatMessage`` — Nexus's consumers rely on
    direct access at many sites and this is the main remaining surface
    divergence. A ``.message`` property bridges the gap for loom.Agent
    adoption down the line.
    """

    model_config = ConfigDict(frozen=True)
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason = StopReason.STOP
    usage: Usage = Field(default_factory=Usage)

    @property
    def message(self) -> ChatMessage:
        return ChatMessage(
            role=Role.ASSISTANT, content=self.content, tool_calls=list(self.tool_calls)
        )


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
        max_tokens: int | None = None,
    ) -> ChatResponse: ...

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # Default fallback: call non-streaming chat and synthesize events.
        resp = await self.chat(messages, tools=tools, model=model, max_tokens=max_tokens)
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
