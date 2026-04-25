"""Message conversion utilities between Nexus and loom ChatMessage types."""

from __future__ import annotations

import json

import loom.types as lt

from nexus.agent.llm import (
    ChatMessage as NexusChatMessage,
    Role,
    StopReason,
    ToolCall as NexusToolCall,
)


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
        content=msg.content,
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
        content=msg.content,
        tool_calls=nexus_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


def _nexus_stop_to_loom(stop: StopReason) -> lt.StopReason:
    try:
        return lt.StopReason(stop.value)
    except ValueError:
        return lt.StopReason.UNKNOWN
