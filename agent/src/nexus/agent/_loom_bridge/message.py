"""Message conversion utilities between Nexus and loom ChatMessage types."""

from __future__ import annotations

import json
from typing import Any

import loom.types as lt

from nexus.agent.llm import (
    ChatMessage as NexusChatMessage,
    ContentPart as NexusContentPart,
    Role,
    StopReason,
    ToolCall as NexusToolCall,
)


def _nexus_part_to_loom(part: NexusContentPart) -> Any:
    """Map one Nexus ``ContentPart`` to a loom ``ContentPart`` instance.

    Loom's union covers text/image/video/file. Audio + document fall
    back to ``FilePart`` carrying the mime type so the receiving Nexus
    encoder can re-classify on the way out.

    The ``source`` field carries the vault-relative path verbatim — no
    base64 happens at the bridge. The provider encoder reads bytes and
    encodes only when it's about to send.
    """
    if part.kind == "text":
        return lt.TextPart(text=part.text or "")
    source = part.vault_path or ""
    media = part.mime_type or ""
    if part.kind == "image":
        return lt.ImagePart(source=source, media_type=media)
    # audio + document both ride FilePart; classifier on the return trip
    # uses the media_type prefix to put them back in the right kind.
    return lt.FilePart(source=source, media_type=media)


def _loom_part_to_nexus(part: Any) -> NexusContentPart:
    """Reverse of :func:`_nexus_part_to_loom`. Audio is reclassified by
    media_type; everything that's not text/image/audio is treated as
    a document (PDF, txt, etc.)."""
    ptype = getattr(part, "type", None)
    if ptype == "text":
        return NexusContentPart(kind="text", text=getattr(part, "text", "") or "")
    media = getattr(part, "media_type", "") or ""
    source = getattr(part, "source", "") or ""
    if ptype == "image":
        return NexusContentPart(
            kind="image", vault_path=source, mime_type=media or None
        )
    if media.startswith("audio/"):
        return NexusContentPart(
            kind="audio", vault_path=source, mime_type=media
        )
    return NexusContentPart(
        kind="document", vault_path=source, mime_type=media or None
    )


def _content_to_loom(content: Any) -> Any:
    if isinstance(content, list):
        return [_nexus_part_to_loom(p) for p in content]
    return content


def _content_from_loom(content: Any) -> Any:
    if isinstance(content, list):
        return [_loom_part_to_nexus(p) for p in content]
    return content


def _nexus_to_loom_message(msg: NexusChatMessage) -> lt.ChatMessage:
    """Convert a Nexus ChatMessage (dict args) to a loom ChatMessage (str args)."""
    loom_tcs: list[lt.ToolCall] | None = None
    if msg.tool_calls:
        loom_tcs = [
            lt.ToolCall(id=tc.id, name=tc.name, arguments=json.dumps(tc.arguments))
            for tc in msg.tool_calls
        ]
    return lt.ChatMessage(
        role=lt.Role(msg.role.value),
        content=_content_to_loom(msg.content),
        tool_calls=loom_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _loom_to_nexus_message(msg: lt.ChatMessage) -> NexusChatMessage:
    """Convert a loom ChatMessage (str args) to a Nexus ChatMessage (dict args)."""
    nexus_tcs: list[NexusToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                args = {}
            nexus_tcs.append(NexusToolCall(id=tc.id, name=tc.name, arguments=args))
    return NexusChatMessage(
        role=Role(msg.role.value),
        content=_content_from_loom(msg.content),
        tool_calls=nexus_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _nexus_stop_to_loom(stop: StopReason) -> lt.StopReason:
    try:
        return lt.StopReason(stop.value)
    except ValueError:
        return lt.StopReason.UNKNOWN
