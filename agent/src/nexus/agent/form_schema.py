"""Shared form / field schema types used by ask_user (kind='form') and vault data-tables."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FieldKind = Literal["text", "textarea", "number", "boolean", "select", "multiselect", "date"]


class FieldSchema(BaseModel):
    name: str
    label: str | None = None
    kind: FieldKind = "text"
    required: bool = False
    default: Any = None
    choices: list[str] | None = Field(default=None)
    placeholder: str | None = None
    help: str | None = None
    # Optional URL displayed alongside ``help`` (e.g. "Get your token here →").
    help_url: str | None = None
    # When true, render as a masked password input. The submitted value is
    # redacted in the persisted chat transcript (see ask_user_tool resolution
    # path) and the YOLO short-circuit refuses to auto-answer such forms.
    secret: bool = False


class FormSchema(BaseModel):
    title: str | None = None
    description: str | None = None
    fields: list[FieldSchema]


def redact_secret_fields(
    fields: list[dict[str, Any]] | list[FieldSchema] | None, answer: Any
) -> Any:
    """Replace values of ``secret: true`` form fields with ``[redacted]``.

    Used at the two persistence choke points a parked form answer passes
    through — ``mark_hitl_pending_answered`` (sessions DB) and
    ``Agent.continue_after_hitl`` (resumed TOOL message) — so a secret
    answered after the 30s park threshold never lands in plaintext
    storage or the LLM context. Mirrors ``AskUserResult.to_text``.
    Non-dict answers and forms without secret fields pass through as-is.
    """
    if not fields or not isinstance(answer, dict):
        return answer
    names: set[str] = set()
    for f in fields:
        if isinstance(f, FieldSchema):
            if f.secret:
                names.add(f.name)
        elif isinstance(f, dict) and f.get("secret"):
            name = f.get("name")
            if isinstance(name, str):
                names.add(name)
    if not names:
        return answer
    return {k: ("[redacted]" if k in names else v) for k, v in answer.items()}
