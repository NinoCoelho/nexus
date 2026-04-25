"""Data models and type-conversion helpers for the Nexus session store.

Defines:
- ``Session`` / ``SessionSummary`` dataclasses used by FastAPI handlers.
- ``_to_loom_msg`` / ``_from_loom_msg`` converters for the Nexus↔loom type boundary.
- ``_ts_to_int`` timestamp normaliser (ISO string or integer → Unix epoch int).

Type boundary — Nexus uses ``nexus.agent.llm.ChatMessage`` / ``ToolCall``
which have ``arguments: dict``.  Loom uses ``loom.types.ChatMessage`` /
``ToolCall`` which have ``arguments: str`` (JSON).  The converters below
handle this at the boundary so the rest of the code stays unaware.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ...agent.llm import ChatMessage, Role, ToolCall


# ── Dataclasses consumed by server handlers ───────────────────────────────────


@dataclass
class Session:
    id: str
    title: str
    history: list[ChatMessage] = field(default_factory=list)
    context: str | None = None


@dataclass
class SessionSummary:
    id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int


# ── Type conversion helpers ───────────────────────────────────────────────────


def _to_loom_msg(msg: ChatMessage) -> "loom.types.ChatMessage":  # type: ignore[name-defined]
    """Convert a Nexus ChatMessage to loom's format.

    Nexus ``ToolCall.arguments`` is a ``dict``; loom expects a JSON string.
    """
    import loom.types as lt

    loom_tcs: list[lt.ToolCall] | None = None
    if msg.tool_calls:
        loom_tcs = [
            lt.ToolCall(
                id=tc.id,
                name=tc.name,
                arguments=json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments,
            )
            for tc in msg.tool_calls
        ]
    return lt.ChatMessage(
        role=lt.Role(msg.role.value),
        content=msg.content,
        tool_calls=loom_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _from_loom_msg(msg: "loom.types.ChatMessage") -> ChatMessage:  # type: ignore[name-defined]
    """Convert loom's ChatMessage to Nexus's format.

    Loom ``ToolCall.arguments`` is a JSON string; Nexus expects a ``dict``.
    """
    nexus_tcs: list[ToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            except (TypeError, json.JSONDecodeError):
                args = {}
            nexus_tcs.append(ToolCall(id=tc.id, name=tc.name, arguments=args))
    return ChatMessage(
        role=Role(msg.role.value),
        content=msg.content,
        tool_calls=nexus_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _ts_to_int(ts: Any) -> int:
    """Convert a timestamp value (ISO string or integer) to a Unix epoch int."""
    if ts is None:
        return 0
    if isinstance(ts, (int, float)):
        return int(ts)
    # ISO string from SQLite CURRENT_TIMESTAMP: "2024-01-15 10:30:00"
    try:
        dt = datetime.fromisoformat(str(ts).replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return 0
