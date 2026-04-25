"""Pure helpers and shared constants for the agent loop.

Kept in their own module so tests that import ``_extract_pending_question``
and ``_annotate_short_reply`` directly continue to work via the package
``__init__`` re-export.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import loom.types as lt

from ..llm import ChatMessage, Role, ToolCall, ToolSpec

DEFAULT_MAX_TOOL_ITERATIONS = 32

SKILL_MANAGE_TOOL = ToolSpec(
    name="skill_manage",
    description=(
        "Create, edit, patch, delete, write_file, or remove_file for a skill in the registry. "
        "A skill is a prescriptive procedure you wrote for future-you, NOT a copy of library docs. "
        "Every SKILL.md MUST have this shape:\n\n"
        "  ---\n"
        "  name: <kebab-case>\n"
        "  description: <imperative one-liner — 'Use this whenever X; prefer over Y.' "
        "Not 'A library that does X'.>\n"
        "  ---\n\n"
        "  ## When to use\n"
        "  - Trigger conditions (concrete, e.g. 'fetching any web page, especially bot-protected or JS-rendered').\n"
        "  - What to reach for this INSTEAD of (e.g. 'prefer over curl/terminal for web fetches').\n\n"
        "  ## Steps\n"
        "  1. Numbered, runnable. Paste the exact commands/snippets that worked.\n\n"
        "  ## Gotchas\n"
        "  - Known failure modes and how to recover (auth walls, rate limits, missing deps).\n\n"
        "Write in the imperative voice of a teammate handing off a recipe. Skip background theory and "
        "library-feature tours — those belong in upstream docs. If the skill won't save a future-you turn, don't create it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "edit", "patch", "delete", "write_file", "remove_file"],
            },
            "name": {"type": "string", "description": "Skill name (directory name, kebab-case)."},
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content (create/edit). Must follow the template in the tool "
                    "description: frontmatter with `name` + imperative `description`, then "
                    "`## When to use`, `## Steps`, `## Gotchas`. Description is an order "
                    "('Use this whenever...'), not a summary ('A library that...')."
                ),
            },
            "old": {"type": "string", "description": "Text to find (patch)."},
            "new": {"type": "string", "description": "Replacement text (patch)."},
            "path": {"type": "string", "description": "Relative file path (write_file/remove_file)."},
        },
        "required": ["action", "name"],
    },
)

_AFFIRMATIVES = frozenset({
    "yes", "y", "ok", "okay", "sure", "correct", "right", "yeah", "yep",
    "go ahead", "proceed", "continue", "please", "do it",
})
_NEGATIVES = frozenset({
    "no", "n", "nope", "cancel", "stop", "don't", "dont", "negative",
})


@dataclass
class AgentTurn:
    reply: str
    skills_touched: list[str] = field(default_factory=list)
    iterations: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    model: str | None = None


def _extract_pending_question(reply: str) -> str | None:
    """Return the last question the agent asked, if the reply ends with one."""
    last_q = reply.rfind("?")
    if last_q == -1:
        return None
    start = max(0, last_q - 200)
    segment = reply[start:last_q + 1]
    first_nl = segment.find("\n")
    if first_nl >= 0:
        segment = segment[first_nl + 1:]
    if len(segment) > 500:
        segment = segment[-500:]
    stripped = segment.strip()
    return stripped or None


def _annotate_short_reply(user_text: str, pending_question: str | None) -> str | None:
    """Expand a terse yes/no reply with the question context."""
    if not pending_question:
        return None
    stripped = user_text.strip().lower()
    if stripped in _AFFIRMATIVES:
        return f'{user_text} (affirmative answer to: "{pending_question}")'
    if stripped in _NEGATIVES:
        return f'{user_text} (negative answer to: "{pending_question}")'
    return None


def _to_loom_message(msg: ChatMessage) -> lt.ChatMessage:
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


def _from_loom_message(msg: lt.ChatMessage) -> ChatMessage:
    nexus_tcs: list[ToolCall] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                args = {}
            nexus_tcs.append(ToolCall(id=tc.id, name=tc.name, arguments=args))
    return ChatMessage(
        role=Role(msg.role.value),
        content=msg.content,
        tool_calls=nexus_tcs,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )
