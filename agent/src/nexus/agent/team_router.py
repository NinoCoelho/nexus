"""Team (group-chat) auto-router for ``mode="auto"`` teams.

Routes a message that @mentions nobody to the most semantically-relevant
team member via fastembed cosine similarity — local, CPU-only, ~15–30 ms,
no remote call, **zero new dependencies** (reuses the GraphRAG embedder
already on disk).

Fails open: on any error, low confidence, timeout, or single-member team,
the team lead answers (the current behaviour). Never blocks or delays a
turn — the decision runs inside a hard 1.5 s budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .builtin_embedder import get_builtin_embedder

log = logging.getLogger(__name__)

# ── Calibrated thresholds (from agent/scripts/calibrate_team_router.py) ──
# Grid search over ~90 labelled user messages from real project chats
# (5 bots, pt-BR): threshold 0.42 / margin 0.08 routed 0 messages to the
# wrong bot (wrong routing is the expensive failure — lead fallback is
# cheap). Raw embedder top-1 accuracy was 49% vs 20% chance; the threshold
# keeps the router conservative and fails open to the lead the rest.
THRESHOLD: float = 0.42   # min cosine for a member to be considered
MARGIN: float = 0.08      # min gap between top-1 and top-2 for a single pick
MAX_LATENCY_SECONDS: float = 1.5  # hard budget; never blocks the turn
RECENT_LINES: int = 6     # chat lines appended as context

ROUTER_DIR = Path.home() / ".nexus" / "team-router"


def _configured_thresholds() -> tuple[float, float, bool]:
    """Thresholds/gate from config ([agent].team_router*), best-effort.

    Falls back to the module constants when config is unavailable (tests).
    Returns (threshold, margin, enabled).
    """
    try:
        from ..config_file import load_cached

        cfg = load_cached()
        agent = getattr(cfg, "agent", None)
        if agent is None:
            return THRESHOLD, MARGIN, True
        return (
            float(getattr(agent, "team_router_threshold", THRESHOLD) or THRESHOLD),
            float(getattr(agent, "team_router_margin", MARGIN) or MARGIN),
            bool(getattr(agent, "team_router", True)),
        )
    except Exception:  # noqa: BLE001 — calibration is best-effort
        return THRESHOLD, MARGIN, True


@dataclass(frozen=True)
class RouteDecision:
    mode: str           # "single" | "several" | "lead"
    chosen: str | None  # project_id (single)
    several: tuple[str, str] | None  # (lead_id, second_id)
    score: float | None
    margin: float | None
    latency_ms: float | None


# Persona embedding cache: (project_id, sha256(persona_text)) -> vector
_persona_cache: dict[tuple[str, str], list[float]] = {}


def _persona_text(bot: Any) -> str:
    name = getattr(bot, "name", "") or getattr(bot, "handle", "") or ""
    handle = getattr(bot, "handle", "") or ""
    persona = getattr(bot, "persona", "") or ""
    handle_part = f" (@{handle})" if handle else ""
    return f"{name}{handle_part} — {persona}".strip() or name or handle


def _persona_key(bot: Any) -> tuple[str, str]:
    return (
        str(getattr(bot, "id", "")),
        hashlib.sha256(_persona_text(bot).encode("utf-8")).hexdigest()[:16],
    )


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.0 if False else sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def _embed(texts: list[str]) -> list[list[float]]:
    embedder = get_builtin_embedder()
    return await asyncio.wait_for(
        asyncio.to_thread(embedder.embed_sync, texts), timeout=MAX_LATENCY_SECONDS
    )


def _log_decision(team_id: str | None, decision: RouteDecision, state_hash: str) -> None:
    try:
        ROUTER_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y-%m")
        line = {
            "at": datetime.now().isoformat(),
            "team": team_id or "",
            "mode": decision.mode,
            "chosen": decision.chosen or "",
            "several": list(decision.several) if decision.several else [],
            "score": round(decision.score or 0.0, 4),
            "margin": round(decision.margin or 0.0, 4),
            "latency_ms": round(decision.latency_ms or 0.0, 1),
            "state_hash": state_hash,
        }
        with open(ROUTER_DIR / f"{day}.ndjson", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("[team-router] decision log write failed", exc_info=True)


async def route_team_message(
    *,
    team: dict | None,
    message: str,
    recent_lines: list[str] | None = None,
    project_store: Any = None,
) -> RouteDecision:
    """Decide who answers a team message with no @mention.

    Fail-open: any failure → ``mode="lead"`` (lead answers, current
    behaviour). Returns project IDs; callers resolve handles via
    ``project_store``.
    """
    start = time.monotonic()
    team_id = team.get("id") if isinstance(team, dict) else None

    _lead_only = RouteDecision(
        mode="lead", chosen=None, several=None,
        score=None, margin=None, latency_ms=None,
    )

    threshold, margin, enabled = _configured_thresholds()
    if not enabled:
        return _lead_only
    if not team or team.get("mode") != "auto":
        return _lead_only

    members = [str(m) for m in (team.get("members") or [])]
    if len(members) < 2:
        return _lead_only

    if project_store is None:
        from ..home import sessions_db
        from ..server.project_store import ProjectStore

        project_store = ProjectStore(sessions_db())

    bots: list[Any] = []
    for bot_id in members:
        try:
            proj = project_store.get(bot_id)
        except Exception:
            proj = None
        if proj is not None and proj.handle:
            bots.append(proj)
    if len(bots) < 2:
        return _lead_only

    # Build input: message + recent chat context.
    ctx = " ".join((recent_lines or [])[-RECENT_LINES:])
    input_text = f"{message}\n{ctx}".strip() if ctx else message

    try:
        # Batch-embed all uncached personas at once.
        uncached_idx: list[int] = []
        uncached_texts: list[str] = []
        for i, bot in enumerate(bots):
            key = _persona_key(bot)
            if key not in _persona_cache:
                uncached_idx.append(i)
                uncached_texts.append(_persona_text(bot))

        if uncached_texts:
            uncached_vecs = await _embed(uncached_texts)
            for idx, vec in zip(uncached_idx, uncached_vecs):
                _persona_cache[_persona_key(bots[idx])] = vec

        vectors = [_persona_cache[_persona_key(bot)] for bot in bots]
        input_vecs = await _embed([input_text])
        input_vec = input_vecs[0]

        scores = [_cos(input_vec, v) for v in vectors]
        ranked = sorted(zip(scores, bots), key=lambda x: x[0], reverse=True)

        top_score, top_bot = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        gap = top_score - second_score

        latency_ms = (time.monotonic() - start) * 1000

        state_hash = hashlib.sha256(
            f"{team_id}|{input_text[:200]}".encode()
        ).hexdigest()[:16]

        if top_score >= threshold and gap >= margin:
            decision: RouteDecision = RouteDecision(
                mode="single",
                chosen=str(top_bot.id),
                several=None,
                score=top_score,
                margin=gap,
                latency_ms=latency_ms,
            )
        elif (
            top_score >= threshold
            and second_score >= threshold
            and gap < margin
        ):
            decision = RouteDecision(
                mode="several",
                chosen=None,
                several=(str(top_bot.id), str(ranked[1][1].id)),
                score=top_score,
                margin=gap,
                latency_ms=latency_ms,
            )
        else:
            decision = RouteDecision(
                mode="lead",
                chosen=None,
                several=None,
                score=top_score,
                margin=gap,
                latency_ms=latency_ms,
            )

        _log_decision(team_id, decision, state_hash)
        return decision

    except Exception:
        log.debug("[team-router] routing failed, falling back to lead", exc_info=True)
        return RouteDecision(
            mode="lead", chosen=None, several=None,
            score=None, margin=None, latency_ms=None,
        )


async def route_team_decision(
    *,
    team: dict | None,
    message: str,
    recent_lines: list[str] | None = None,
    project_store: Any = None,
) -> tuple[RouteDecision, dict[str, str]]:
    """Like :func:`route_team_message` but also resolves
    ``{project_id: handle}`` for the chosen / several members.

    Callers use the handle to build the ``TEAM_MARKER@handle`` prefix.
    """
    decision = await route_team_message(
        team=team,
        message=message,
        recent_lines=recent_lines,
        project_store=project_store,
    )
    if project_store is None:
        from ..home import sessions_db
        from ..server.project_store import ProjectStore

        project_store = ProjectStore(sessions_db())

    handles: dict[str, str] = {}
    pids: list[str] = []
    if decision.chosen:
        pids.append(decision.chosen)
    if decision.several:
        pids.extend(decision.several)
    for pid in pids:
        try:
            proj = project_store.get(pid)
        except Exception:
            proj = None
        if proj is not None and proj.handle:
            handles[pid] = proj.handle
    return decision, handles
