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


class FormSchema(BaseModel):
    title: str | None = None
    description: str | None = None
    fields: list[FieldSchema]
