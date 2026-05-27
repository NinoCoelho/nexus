"""Pure helpers and shared constants for the agent loop.

Kept in their own module so tests that import ``_extract_pending_question``
and ``_annotate_short_reply`` directly continue to work via the package
``__init__`` re-export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..llm import ChatMessage, ContentPart, Role, ToolSpec

if TYPE_CHECKING:
    import loom.types as lt

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
        "  requires_keys:\n"
        "    - CREDENTIAL_NAME\n"
        "  ---\n\n"
        "  ## When to use\n"
        "  - Trigger conditions (concrete, e.g. 'fetching any web page, especially bot-protected or JS-rendered').\n"
        "  - What to reach for this INSTEAD of (e.g. 'prefer over curl/terminal for web fetches').\n\n"
        "  ## Steps\n"
        "  1. Numbered, runnable. Paste the exact commands/snippets that worked.\n\n"
        "  ## Gotchas\n"
        "  - Known failure modes and how to recover (auth walls, rate limits, missing deps).\n\n"
        "Optional frontmatter field:\n"
        "- `requires_keys`: list of UPPER_CASE credential names the skill needs. On first use, "
        "the user is prompted to provide each value (masked form, stored locally in "
        "~/.nexus/secrets.toml, never visible to the LLM). Use `$NAME` placeholders in the "
        "skill body for both `http_call` args (server substitutes the value at the tool "
        "boundary) and `terminal` commands (the shell expands `$NAME` naturally via env var "
        "injection). Only declare keys the skill actually uses.\n\n"
        "Write in the imperative voice of a teammate handing off a recipe. Skip background theory and "
        "library-feature tours — those belong in upstream docs. If the skill won't save a future-you turn, don't create it.\n\n"
        "Safe usage pattern:\n"
        "- Before `edit`, `patch`, or `delete`, always call `skill_view` to inspect the current SKILL.md.\n"
        "- Preserve existing 'Gotchas' sections unless they are obsolete — those capture hard-won lessons.\n"
        "- After `create`, call `skill_view` to verify the skill was saved correctly before relying on it.\n\n"
        "Micro-recipe for creating a new skill:\n"
        "1. Draft content in your reasoning (do NOT output inert code blocks to the user).\n"
        "2. Call `skill_manage` with `action: 'create'` and fully-formed SKILL.md content.\n"
        "3. Verify with `skill_view` that the skill saved correctly.\n"
        "4. Only then start using the skill in your own future plans."
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


def _content_to_loom(content: Any) -> Any:
    from .._loom_bridge.message import _content_to_loom as _impl
    return _impl(content)


def _content_from_loom(content: Any) -> Any:
    from .._loom_bridge.message import _content_from_loom as _impl
    return _impl(content)


def _build_user_message(
    text: str, attachments: list[ContentPart] | None = None
) -> ChatMessage:
    """Assemble a user :class:`ChatMessage` with optional attachments.

    When ``attachments`` is empty, returns a plain text message (the legacy
    shape — keeps every existing call site that expects ``content: str``
    working). With attachments, the text becomes a leading text part and
    each attachment slots in after.
    """
    if not attachments:
        return ChatMessage(role=Role.USER, content=text)
    parts: list[ContentPart] = []
    if text:
        parts.append(ContentPart(kind="text", text=text))
    parts.extend(attachments)
    return ChatMessage(role=Role.USER, content=parts)


def _to_loom_message(msg: ChatMessage) -> lt.ChatMessage:
    from .._loom_bridge.message import _nexus_to_loom_message
    return _nexus_to_loom_message(msg)


def _from_loom_message(msg: lt.ChatMessage) -> ChatMessage:
    from .._loom_bridge.message import _loom_to_nexus_message
    return _loom_to_nexus_message(msg)
