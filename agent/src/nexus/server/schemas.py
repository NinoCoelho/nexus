"""Request/response Pydantic models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    context: str | None = None
    model: str | None = None


class ChatReply(BaseModel):
    session_id: str
    reply: str
    trace: list[dict[str, Any]]
    skills_touched: list[str]
    iterations: int
    plan: list[dict[str, Any]] | None = None


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


class RespondPayload(BaseModel):
    """Body for the ``/chat/{session_id}/respond`` endpoint — the UI's
    answer to a pending ``ask_user`` request."""

    request_id: str = Field(min_length=1)
    answer: str


class TruncateRequest(BaseModel):
    before_seq: int


class ModelRolePayload(BaseModel):
    role: str  # "embedding" | "extraction" | "classification"
    model_id: str


class SettingsPayload(BaseModel):
    """Full settings snapshot, both directions. Returned by
    ``GET /settings`` and accepted by ``POST /settings``. A partial
    update POST omits fields it doesn't want to change — the server
    merges against the current state."""

    yolo_mode: bool | None = None
