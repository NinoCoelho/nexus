"""Skill wizard routes — capability discovery + build for non-technical users.

Surface:

* ``POST /skills/wizard/discover`` — ranked, abstract candidates that match a
  plain-language ask. Bodies stay server-side; only metadata crosses the wire.
* ``POST /skills/wizard/build`` — kicks off the agentic synthesis turn. Loads
  the chosen + related candidates from the discovery cache, composes a seed
  message, creates a hidden session, and fires one background turn. Returns
  the session id so the wizard can subscribe to ``/chat/{sid}/events`` for
  progress.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...agent.llm import LLMProvider
from ...agent.loop import Agent
from ...config import SKILLS_DIR
from ...skills.discovery import (
    Candidate,
    ScoredCandidate,
    SkillDiscovery,
    builtin_sources_path,
    discovery_cache_dir,
    load_candidate_by_id,
    load_sources,
    user_sources_path,
)
from ..deps import get_agent, get_locale, get_sessions
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()

# Cap related candidate fan-out — matches the cap in skills/skill-builder/SKILL.md.
_MAX_RELATED = 4
# Cap individual candidate body size when stitching the seed message. The
# discovery side already caps at 100 KB, but with N candidates the seed can
# still get huge. 12 KB per body keeps the parent's first turn under ~60 KB
# even at maxed-out fan-out.
_SEED_BODY_CAP = 12_000


# ── DTOs ───────────────────────────────────────────────────────────────────


class DiscoverRequest(BaseModel):
    user_ask: str = Field(..., min_length=2, max_length=500)
    language: str | None = None
    limit: int = Field(default=8, ge=1, le=20)


class CandidateKeyDTO(BaseModel):
    name: str
    vendor: str = ""
    get_key_url: str = ""
    free_tier_available: bool = False


class CandidateSourceDTO(BaseModel):
    slug: str
    url: str
    verified: bool


class CandidateDTO(BaseModel):
    id: str
    title: str
    summary: str
    capabilities: list[str]
    complexity: int
    cost_tier: str
    requires_keys: list[CandidateKeyDTO]
    risks: list[str]
    confidence: float
    score: float
    source: CandidateSourceDTO


class DiscoverResponse(BaseModel):
    candidates: list[CandidateDTO]


class BuildRequest(BaseModel):
    candidate_id: str = Field(..., min_length=3, max_length=200)
    user_ask: str = Field(..., min_length=2, max_length=500)
    related_ids: list[str] = Field(default_factory=list)
    language: str | None = None


class BuildResponse(BaseModel):
    session_id: str


def _resolve_classifier(agent_: Agent) -> tuple[LLMProvider, str]:
    """Pick a (provider, model) pair for the discovery classifier.

    The agent's ``_nexus_provider`` is a single configured instance that may
    not have a default model baked in (provider-registry construction goes
    that way). Calling ``provider.chat()`` without a model then raises
    ``LLMError("No model specified")``.

    Resolution order:
    1. Agent config's ``agent.default_model`` if it's available in the
       provider registry.
    2. The first available model in the registry.
    3. Raise 503 — the user has no functional model configured.

    Returns the (provider, model_name) tuple from
    :meth:`ProviderRegistry.get_for_model`, which already routes to the
    right provider for the chosen model id.
    """
    pr = getattr(agent_, "_provider_registry", None)
    if pr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM provider registry unavailable; configure a model in Settings first",
        )
    cfg = getattr(agent_, "_nexus_cfg", None)
    vision_id = (getattr(cfg, "agent", None) and cfg.agent.vision_model) or ""
    exclude = {vision_id} if vision_id else None
    available = pr.available_model_ids(exclude=exclude)
    if not available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="no functional models configured; add one in Settings → Providers",
        )
    default = getattr(getattr(cfg, "agent", None), "default_model", None) if cfg else None
    model_id = default if (default and default in available) else available[0]
    try:
        return pr.get_for_model(model_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"could not resolve provider for model {model_id!r}: {exc}",
        )


def _to_dto(scored: ScoredCandidate) -> CandidateDTO:
    c: Candidate = scored.candidate
    cls = c.classification
    return CandidateDTO(
        id=c.id,
        title=cls.title,
        summary=cls.summary,
        capabilities=list(cls.capabilities),
        complexity=cls.complexity,
        cost_tier=cls.cost_tier,
        requires_keys=[CandidateKeyDTO(**k.__dict__) for k in cls.requires_keys],
        risks=list(cls.risks),
        confidence=cls.confidence,
        score=scored.score,
        source=CandidateSourceDTO(
            slug=c.source_slug, url=c.source_url, verified=c.source_verified
        ),
    )


# ── /discover ──────────────────────────────────────────────────────────────


@router.post("/skills/wizard/discover", response_model=DiscoverResponse)
async def discover(
    req: DiscoverRequest,
    agent: Agent = Depends(get_agent),
    locale: str = Depends(get_locale),
) -> DiscoverResponse:
    """Find candidate skills matching the user's plain-language request.

    Cached results are reused across calls; cold-cache calls invoke the
    configured LLM provider once per candidate to extract structured
    metadata. The response never includes raw SKILL.md bodies.
    """
    language = req.language or locale or "en"
    sources = load_sources(
        builtin_path=builtin_sources_path(),
        user_path=user_sources_path(SKILLS_DIR),
    )
    if not sources:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="no skill sources configured",
        )

    provider, model_name = _resolve_classifier(agent)

    async with httpx.AsyncClient() as client:
        discovery = SkillDiscovery(
            cache_dir=discovery_cache_dir(SKILLS_DIR),
            sources=sources,
            provider=provider,
            provider_model=model_name,
            http_client=client,
        )
        results = await discovery.discover(
            req.user_ask, language=language, limit=req.limit
        )
    return DiscoverResponse(candidates=[_to_dto(s) for s in results])


# ── /build ─────────────────────────────────────────────────────────────────


@router.post("/skills/wizard/build", response_model=BuildResponse)
async def build_skill(
    req: BuildRequest,
    agent: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
    locale: str = Depends(get_locale),
) -> BuildResponse:
    """Kick off the agentic build of a derived skill.

    Loads the primary + related candidates from the discovery cache, composes
    a seed user message, creates a hidden chat session, and fires one
    background turn. The wizard subscribes to ``/chat/{sid}/events`` for
    progress.
    """
    cache = discovery_cache_dir(SKILLS_DIR)
    primary = load_candidate_by_id(cache, req.candidate_id)
    if primary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"candidate {req.candidate_id!r} not found in discovery cache; rerun /discover",
        )

    related: list[Candidate] = []
    seen = {req.candidate_id}
    for rid in req.related_ids:
        if rid in seen:
            continue
        seen.add(rid)
        cand = load_candidate_by_id(cache, rid)
        if cand is not None:
            related.append(cand)
        if len(related) >= _MAX_RELATED:
            break

    language = req.language or locale or "en"
    seed = _compose_build_seed(
        user_ask=req.user_ask,
        language=language,
        primary=primary,
        related=related,
    )

    session = store.create(context=f"Wizard build: {primary.classification.title}")
    try:
        store.mark_hidden(session.id)
    except Exception:
        log.exception("wizard build: mark_hidden failed")
    try:
        store.rename(
            session.id, f"Build: {primary.classification.title}"[:60].strip()
        )
    except Exception:
        log.exception("wizard build: rename failed")

    asyncio.create_task(
        _run_build_turn(
            session_id=session.id,
            seed_message=seed,
            agent_=agent,
            store=store,
        )
    )
    return BuildResponse(session_id=session.id)


# ── seed composition ───────────────────────────────────────────────────────


def _compose_build_seed(
    *,
    user_ask: str,
    language: str,
    primary: Candidate,
    related: list[Candidate],
) -> str:
    # Build the source list the agent must stamp into derived_from. We compose
    # it server-side so the seed itself shows the exact value to use — leaving
    # provenance to the model has been unreliable (it tends to omit the field).
    sources_block: list[str] = []
    sources_block.append(
        f'  {{"slug": "{primary.source_slug}", '
        f'"url": "{primary.source_url}", '
        f'"title": {json.dumps(primary.classification.title)}}}'
    )
    for r in related:
        sources_block.append(
            f'  {{"slug": "{r.source_slug}", '
            f'"url": "{r.source_url}", '
            f'"title": {json.dumps(r.classification.title)}}}'
        )

    parts: list[str] = [
        "You are running the **skill-builder** procedure on behalf of the user. "
        "Open the skill via `skill_manage` (action `view`, name `skill-builder`) "
        "if you don't already have its body, then follow it exactly.",
        "",
        "**Hard requirements (do not skip):**",
        '- The `skill_manage` create call MUST include `"trust": "user"`.',
        "- The `skill_manage` create call MUST include the `derived_from` block "
        "shown below verbatim.",
        '- After `skill_manage` returns ok, your reply text MUST be exactly '
        '`Built skill "<slug>"` (one line, the slug you passed to create).',
        "",
        "**`derived_from` to use (copy verbatim, only `wizard_built_at` "
        "stays as `<current ISO timestamp>`):**",
        "",
        "```json",
        "{",
        f'  "wizard_ask": {json.dumps(user_ask)},',
        '  "wizard_built_at": "<current ISO timestamp>",',
        '  "sources": [',
        ",\n".join(sources_block),
        "  ]",
        "}",
        "```",
        "",
        f"**User's request:** {user_ask}",
        f"**Output language:** {language}",
        "",
        "**Primary candidate (chosen by the user):**",
        f"- id: `{primary.id}`",
        f"- source slug: `{primary.source_slug}`",
        f"- source url: {primary.source_url}",
        f"- title: {primary.classification.title}",
        "",
        "Body:",
        "````markdown",
        _truncate_body(primary.body),
        "````",
        "",
    ]
    if related:
        parts.append(
            "**Related candidates** (analyze in parallel via "
            "`spawn_subagents` — one body per child):"
        )
        parts.append("")
        for r in related:
            parts.append(f"- id: `{r.id}`")
            parts.append(f"  source slug: `{r.source_slug}`")
            parts.append(f"  source url: {r.source_url}")
            parts.append(f"  title: {r.classification.title}")
            if r.classification.summary:
                parts.append(f"  summary: {r.classification.summary}")
            parts.append("  body:")
            parts.append("  ````markdown")
            for line in _truncate_body(r.body).splitlines():
                parts.append(f"  {line}")
            parts.append("  ````")
            parts.append("")
    parts.append("Build the derived skill now.")
    return "\n".join(parts)


def _truncate_body(body: str) -> str:
    if len(body) <= _SEED_BODY_CAP:
        return body
    head = body[: _SEED_BODY_CAP]
    return head.rstrip() + "\n\n[…body truncated for seed; full version stays on the source]"


# ── background turn ────────────────────────────────────────────────────────


async def _run_build_turn(
    *,
    session_id: str,
    seed_message: str,
    agent_: Agent,
    store: SessionStore,
) -> None:
    from ..services.background_turn import run_background_turn

    await run_background_turn(
        session_id=session_id,
        seed_message=seed_message,
        agent_=agent_,
        store=store,
        partial_status_note="wizard_build_interrupted",
    )
