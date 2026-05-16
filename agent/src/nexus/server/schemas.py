"""Request/response Pydantic models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """One file attached to a chat message.

    The UI uploads files to ``~/.nexus/vault/uploads/`` first (via
    ``POST /vault/upload``) and then references them here by their
    vault-relative path. The chat route resolves each path into a
    ``ContentPart`` (image / audio / document) before handing the
    message to the agent loop.
    """

    vault_path: str
    mime_type: str | None = None  # sniffed from extension when absent


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    attachments: list[Attachment] = Field(default_factory=list)
    context: str | None = None
    model: str | None = None
    # "voice" when the user dictated; "text" when typed. Threaded through to
    # the voice-ack pipeline so spoken acknowledgments only fire for voice
    # turns. Defaults to "text" so existing callers (CLI, scripts) stay quiet.
    input_mode: str = "text"


class ChatReply(BaseModel):
    session_id: str
    reply: str
    trace: list[dict[str, Any]]
    skills_touched: list[str]
    iterations: int
    plan: list[dict[str, Any]] | None = None


class DerivedFromSourceDTO(BaseModel):
    slug: str = ""
    url: str = ""
    title: str = ""


class DerivedFromDTO(BaseModel):
    """Provenance block for skills built by the capability wizard.

    ``None`` on every skill that wasn't wizard-built — the UI keys off the
    presence of this field to render the "Built from your request" affordance.
    """

    wizard_ask: str = ""
    wizard_built_at: str = ""
    sources: list[DerivedFromSourceDTO] = Field(default_factory=list)


class SkillInfo(BaseModel):
    name: str
    description: str
    trust: str
    derived_from: DerivedFromDTO | None = None


class SkillDetail(BaseModel):
    name: str
    description: str
    trust: str
    body: str
    derived_from: DerivedFromDTO | None = None


class Health(BaseModel):
    ok: bool = True


class RespondPayload(BaseModel):
    """Body for the ``/chat/{session_id}/respond`` endpoint — the UI's
    answer to a pending ``ask_user`` request."""

    request_id: str = Field(min_length=1)
    answer: str


class TruncateRequest(BaseModel):
    before_seq: int


class CompactRequest(BaseModel):
    """Replace oversized tool messages and/or summarize older turns.

    ``strategy`` controls what runs:
      - ``auto`` (default): tool compaction + LLM summarization when zone >= yellow.
      - ``tools_only``: only compress oversized tool results.
      - ``summarize_only``: only LLM summarization of older turns.
      - ``aggressive``: lower thresholds (4 KB/512 B) + summarization.

    ``force_summarize`` runs LLM summarization regardless of zone.
    """

    strategy: str = "auto"
    force_summarize: bool = False
    model: str | None = None


class ModelRolePayload(BaseModel):
    role: str  # "embedding" | "extraction" | "vision" | "classification"
    # Pass an empty string (or null) to clear the role and fall back to the
    # built-in defaults (embedding → fastembed, classification → local).
    model_id: str | None = ""


class SettingsPayload(BaseModel):
    """Full settings snapshot, both directions. Returned by
    ``GET /settings`` and accepted by ``POST /settings``. A partial
    update POST omits fields it doesn't want to change — the server
    merges against the current state."""

    yolo_mode: bool | None = None
