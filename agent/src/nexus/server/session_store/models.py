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

from ...agent.llm import ChatMessage, ContentPart, Role, ToolCall


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


def _content_to_loom(content: Any) -> Any:
    """Translate a Nexus message ``content`` value to loom's shape.

    String / None pass through unchanged. A list of Nexus ``ContentPart``s
    is mapped to loom's ``TextPart`` / ``ImagePart`` / ``FilePart``
    discriminated union (loom has no audio/document part — both ride
    ``FilePart`` carrying their mime type).
    """
    if not isinstance(content, list):
        return content
    import loom.types as lt

    out: list[Any] = []
    for p in content:
        if not isinstance(p, ContentPart):
            out.append(p)
            continue
        if p.kind == "text":
            out.append(lt.TextPart(text=p.text or ""))
        elif p.kind == "image":
            out.append(
                lt.ImagePart(
                    source=p.vault_path or "", media_type=p.mime_type or ""
                )
            )
        else:
            out.append(
                lt.FilePart(
                    source=p.vault_path or "", media_type=p.mime_type or ""
                )
            )
    return out


def _content_from_loom(content: Any) -> Any:
    """Reverse of :func:`_content_to_loom`. Re-classifies FilePart by
    media_type so audio comes back as ``kind="audio"`` and everything
    else as ``kind="document"``."""
    if not isinstance(content, list):
        return content
    out: list[Any] = []
    for p in content:
        ptype = getattr(p, "type", None)
        media = getattr(p, "media_type", "") or ""
        source = getattr(p, "source", "") or ""
        if ptype == "text":
            out.append(ContentPart(kind="text", text=getattr(p, "text", "") or ""))
        elif ptype == "image":
            out.append(
                ContentPart(kind="image", vault_path=source, mime_type=media or None)
            )
        elif media.startswith("audio/"):
            out.append(
                ContentPart(kind="audio", vault_path=source, mime_type=media)
            )
        else:
            out.append(
                ContentPart(kind="document", vault_path=source, mime_type=media or None)
            )
    return out


def _to_loom_msg(msg: ChatMessage) -> "loom.types.ChatMessage":  # type: ignore[name-defined]
    """Convert a Nexus ChatMessage to loom's format.

    Nexus ``ToolCall.arguments`` is a ``dict``; loom expects a JSON string.
    Multipart ``content`` (image/audio/document attachments) is translated
    to loom's part discriminated union via :func:`_content_to_loom`.
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
        content=_content_to_loom(msg.content),
        tool_calls=loom_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _from_loom_msg(msg: "loom.types.ChatMessage") -> ChatMessage:  # type: ignore[name-defined]
    """Convert loom's ChatMessage to Nexus's format.

    Loom ``ToolCall.arguments`` is a JSON string; Nexus expects a ``dict``.
    Multipart ``content`` is rebuilt into Nexus ``ContentPart``s.
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
        content=_content_from_loom(msg.content),
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
