"""Request/response Pydantic models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    context: str | None = None


class ChatReply(BaseModel):
    session_id: str
    reply: str
    trace: list[dict[str, Any]]
    skills_touched: list[str]
    iterations: int


class SkillInfo(BaseModel):
    name: str
    description: str
    trust: str


class SkillDetail(BaseModel):
    name: str
    description: str
    trust: str
    body: str


class Health(BaseModel):
    ok: bool = True
