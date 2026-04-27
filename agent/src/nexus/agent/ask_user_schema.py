"""ASK_USER_TOOL ToolSpec definition.

Extracted from ask_user_tool.py to keep that module under 300 LOC.
Import ``ASK_USER_TOOL`` from ``ask_user_tool`` — it is re-exported there.
"""

from __future__ import annotations

from .llm import ToolSpec

ASK_USER_TOOL = ToolSpec(
    name="ask_user",
    description=(
        "Pause the agent and ask the user a question. Use when the next "
        "step requires their judgment — confirming a destructive action, "
        "picking between options only they know, or asking for a value "
        "(URL, filename, number). Returns the user's answer as a string. "
        "If the user doesn't respond within the timeout (default 300s), "
        "returns the literal string '__timeout__' — treat that as 'do "
        "not proceed'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The question to show the user. Be specific: include "
                    "the exact action, the target, and any side effects."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["confirm", "choice", "text", "form"],
                "description": (
                    "'confirm' for yes/no; 'choice' for a pick-one from "
                    "`choices`; 'text' for free-form input; 'form' for a "
                    "multi-field structured form (answer is a JSON object). "
                    "Default: 'confirm'."
                ),
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Required for kind='choice'. Each string is a "
                    "selectable option; the returned answer is one of "
                    "these verbatim."
                ),
            },
            "fields": {
                "type": "array",
                "description": (
                    "Required for kind='form'. Array of field descriptors. "
                    "Each has: name (str, required), label (str), "
                    "kind ('text'|'textarea'|'number'|'boolean'|'select'|'multiselect'|'date'), "
                    "required (bool), default, choices (for select/multiselect), "
                    "placeholder (str), help (str). "
                    "Answer comes back as a JSON object keyed by field name."
                ),
                "items": {"type": "object"},
            },
            "title": {
                "type": "string",
                "description": "Optional title shown in the form dialog header.",
            },
            "description": {
                "type": "string",
                "description": "Optional description shown below the title in the form dialog.",
            },
            "default": {
                "type": "string",
                "description": (
                    "Optional suggested answer (shown in the UI as the "
                    "preferred option / default text). Does not "
                    "auto-apply — the user still has to pick."
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    "How long to wait for an answer. Default 300. "
                    "Shorter for low-stakes confirms; longer for "
                    "questions that need investigation."
                ),
            },
            "parkable": {
                "type": "boolean",
                "description": (
                    "Opt-in for kind='text' or 'choice': if the user does "
                    "not respond within ~30s, park the request and end the "
                    "turn cleanly. The session resumes when the user "
                    "answers via the bell or push notification. "
                    "kind='form' parks by default; kind='confirm' never "
                    "parks (approvals must stay synchronous). Default false."
                ),
            },
        },
        "required": ["prompt"],
    },
)
