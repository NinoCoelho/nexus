"""Routes for the bots surface.

Projects are the persistence backbone for bots (persona + instructions +
vault folder, sessions linked via ``project_id``). This module exposes them
under a bot-oriented API: a unified list with the coordinator ("main") first,
per-bot activity monitoring (hidden internal sessions), and cleanup helpers
that archive everything but the most recent active chats so each bot returns
to a single conversation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..deps import get_sessions
from ..project_store import ProjectStore
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()

_DB_PATH = Path("~/.nexus/sessions.sqlite").expanduser()

MAIN_BOT_ID = "main"


def _project_store() -> ProjectStore:
    return ProjectStore(_DB_PATH)


def _coordinator_cfg(request: Request) -> Any:
    cfg = request.app.state.mutable_state.get("cfg")
    return getattr(cfg, "coordinator", None)


@router.get("/bots")
async def list_bots(request: Request) -> list[dict]:
    """Unified bot list — the coordinator ("main") first, then every project.

    ``kind`` distinguishes the coordinator entry (``"main"``, carries the
    master ``session_id``) from project-backed bots. ``handle`` is the
    @mention handle; ``session_count`` counts active (visible, unarchived)
    chats only.
    """
    bots: list[dict] = []

    coord = _coordinator_cfg(request)
    if coord is not None and coord.enabled:
        from ...agent.bots import coordinator_identity

        ident = coordinator_identity() or {"handle": "main", "name": "Main"}
        bots.append(
            {
                "id": MAIN_BOT_ID,
                "kind": "coordinator",
                "name": coord.name or "Main",
                "description": "",
                "persona": coord.persona or "",
                # Handle derives from the configured name (@nico, @grono…) —
                # "main" is only the internal id, never the shown handle.
                "handle": ident["handle"],
                "color": "",
                "icon": "",
                "session_id": coord.session_id or "",
                "session_count": 1 if coord.session_id else 0,
                "vault_path": "",
                "created_at": 0,
                "updated_at": 0,
            }
        )

    store = _project_store()
    summaries = await asyncio.to_thread(store.list, limit=200)
    for s in summaries:
        bots.append(
            {
                "id": s.id,
                "kind": "bot",
                "name": s.name,
                "description": s.description,
                "persona": s.persona,
                "handle": s.handle,
                "color": s.color,
                "icon": s.icon,
                "session_id": "",
                "session_count": s.session_count,
                "vault_path": "",
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
        )
    return bots


async def _delete_bot_impl(store: SessionStore, bot_id: str, chats: str) -> dict:
    """Shared bot-deletion core — used by the HTTP route and the coordinator's
    ``bot_config`` tool. ``chats``: archive (default) | delete | keep."""
    if bot_id == MAIN_BOT_ID:
        raise HTTPException(status_code=422, detail="the main bot cannot be deleted")
    if chats not in ("archive", "delete", "keep"):
        raise HTTPException(status_code=422, detail="chats must be archive|delete|keep")
    pstore = _project_store()
    project = await asyncio.to_thread(pstore.get, bot_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    sessions = await asyncio.to_thread(
        store.list, 9999, 0, project_id=bot_id, include_hidden=True, archived=None
    )
    archived_count = 0
    deleted_count = 0
    if chats == "archive":
        for s in sessions:
            await asyncio.to_thread(store.mark_archived, s.id, True)
            archived_count += 1
    elif chats == "delete":
        for s in sessions:
            await asyncio.to_thread(store.delete, s.id)
            deleted_count += 1

    # Detach remaining chats (keep mode, or archive keeps them findable only
    # via main's archived view) and unlink Telegram bindings before the row
    # goes — nothing may reference a dead bot id.
    conn = pstore._connect()
    try:
        conn.execute(
            "UPDATE telegram_bindings SET project_id = NULL WHERE project_id = ?",
            (bot_id,),
        )
        conn.commit()
    finally:
        conn.close()
    await asyncio.to_thread(pstore.delete, bot_id)
    return {
        "deleted": True,
        "sessions_archived": archived_count,
        "sessions_deleted": deleted_count,
    }


@router.delete("/bots/{bot_id}")
async def delete_bot(
    bot_id: str,
    chats: str = "archive",
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Delete a bot (project) and handle its chats.

    ``chats``: ``"archive"`` (default — sessions archived + detached from the
    bot, restorable from main's archived view), ``"delete"`` (sessions and
    their messages permanently removed), or ``"keep"`` (sessions survive as
    unprojected active chats). Telegram bindings to the bot are unlinked.
    """
    return await _delete_bot_impl(store, bot_id, chats)


async def _create_team_impl(store: SessionStore, body: dict) -> dict:
    """Shared team-creation core — HTTP route and the coordinator's ``teams``
    tool both land here so the invariants live once."""
    name = (body.get("name") or "").strip()
    members = [str(m) for m in (body.get("members") or []) if str(m)]
    mode = body.get("mode", "lead")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if mode not in ("lead", "round"):
        raise HTTPException(status_code=422, detail="mode must be lead|round")
    pstore = _project_store()
    valid: list[str] = []
    for mid in members:
        if mid not in valid and await asyncio.to_thread(pstore.get, mid) is not None:
            valid.append(mid)
    if not valid:
        raise HTTPException(status_code=422, detail="members must list at least one existing bot")

    session = await asyncio.to_thread(
        store.create, f"Team chat: {name}", valid[0]
    )
    team = {"name": name, "members": valid, "mode": mode}
    await asyncio.to_thread(store.set_team, session.id, team)
    await asyncio.to_thread(store.rename, session.id, name)
    return {"session_id": session.id, "team": team}


@router.post("/teams", status_code=status.HTTP_201_CREATED)
async def create_team(
    body: dict,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Create a group-chat (team) session with several bot members.

    Body: ``{"name": str, "members": [bot_id...], "mode": "lead"|"round"}``.
    The first member is the **lead** — it answers messages that don't @mention
    anyone; the session lives under it. ``mode=round`` makes every member
    answer in turn (lead first, then the others via synthetic mention passes).
    """
    return await _create_team_impl(store, body)


@router.patch("/sessions/{session_id}/team")
async def update_team(
    session_id: str,
    body: dict,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Edit (or clear) a session's team. Body fields all optional:
    ``{"name", "members", "mode", "clear": bool}``.
    """
    existing = await asyncio.to_thread(store.get_team, session_id)
    if existing is None and not body.get("clear"):
        raise HTTPException(status_code=404, detail="Session has no team")
    if body.get("clear"):
        await asyncio.to_thread(store.set_team, session_id, None)
        return {"team": None}
    pstore = _project_store()
    team = dict(existing or {})
    if "name" in body:
        team["name"] = (body.get("name") or "").strip() or team.get("name", "Team")
    if "mode" in body and body["mode"] in ("lead", "round"):
        team["mode"] = body["mode"]
    if "members" in body:
        valid: list[str] = []
        for mid in [str(m) for m in (body.get("members") or []) if str(m)]:
            if mid not in valid and await asyncio.to_thread(pstore.get, mid) is not None:
                valid.append(mid)
        if valid:
            team["members"] = valid
    # Keep the session under the current lead.
    if team.get("members"):
        await asyncio.to_thread(pstore.move_session, session_id, team["members"][0])
    await asyncio.to_thread(store.set_team, session_id, team)
    if "name" in body:
        await asyncio.to_thread(store.rename, session_id, team["name"])
    return {"team": team}


@router.get("/teams")
async def list_teams(
    store: SessionStore = Depends(get_sessions),
) -> list[dict]:
    """Every visible team (group chat) session, newest first.

    Members are enriched with ``{id, handle, name}`` so consumers (Telegram
    ``/team``, the UI) can render them without a second round-trip.
    """
    sessions = await asyncio.to_thread(store.list, 200)
    pstore = _project_store()
    out: list[dict] = []
    for s in sessions:
        # ``SessionSummary.team`` is the raw column — a JSON *string* until
        # ``get_team`` parses it. Parse defensively; skip non-dicts.
        raw_team = getattr(s, "team", None)
        if isinstance(raw_team, str):
            try:
                raw_team = json.loads(raw_team)
            except (TypeError, ValueError):
                raw_team = None
        if not isinstance(raw_team, dict):
            continue
        members: list[dict] = []
        for mid in [str(m) for m in (raw_team.get("members") or [])]:
            try:
                p = await asyncio.to_thread(pstore.get, mid)
            except Exception:  # noqa: BLE001
                p = None
            if p is not None:
                members.append({"id": p.id, "handle": p.handle, "name": p.name})
        out.append({
            "session_id": s.id,
            "title": s.title,
            "mode": raw_team.get("mode", "lead"),
            "name": raw_team.get("name", s.title),
            "members": members,
            "updated_at": s.updated_at,
        })
    return out


async def _team_continue_core(
    agent: Any,
    store: SessionStore,
    tracker: Any,
    session: Any,
) -> dict:
    """Shared team-cascade continuation core (HTTP route + Telegram).

    Returns ``{"continued": True, "mode": …}`` or raises ``ValueError``
    with a user-facing message when there is nothing to continue.
    """
    from ...agent.bots import cascade_mentions, turn_speaker_id
    from ..services.chat_turn_runner import get_running_turn

    team = store.get_team(session.id)
    if team is None:
        raise ValueError("Session has no team")

    runner = get_running_turn(session.id)
    if runner is not None:
        budget = runner.extend_cascade()
        return {"continued": True, "mode": "extended", "budget": budget}

    reply = ""
    last_user = ""
    for m in reversed(session.history or []):
        role = str(getattr(m.role, "value", m.role) or "").lower()
        if not reply and role == "assistant":
            content = m.content
            if isinstance(content, str):
                reply = content
        elif role == "user":
            content = m.content
            last_user = content if isinstance(content, str) else ""
            break
    pstore = ProjectStore(store._db_path)
    speaker = turn_speaker_id(
        last_user, team=team, own_project_id=session.project_id, project_store=pstore
    )
    targets = cascade_mentions(
        reply, team=team, speaker_id=speaker, project_store=pstore
    )
    if not targets:
        raise ValueError(
            "No team member is @mentioned in the last reply — nothing to continue."
        )

    from ..services.turn_launcher import launch_turn

    queued: list[str] = []
    for t in targets:
        outcome = await launch_turn(
            agent=agent,
            store=store,
            tracker=tracker,
            session=session,
            message=t["marker"],
            origin="team",
            chain_only=True,
        )
        if outcome.error is None:
            queued.append(t["bot"]["handle"])
    return {"continued": True, "mode": "relaunched", "queued": queued}


@router.post("/sessions/{session_id}/team/continue")
async def team_continue(
    request: Request,
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Grant the team's bot→bot conversation more rounds.

    Two paths, one endpoint:
    - a runner is still alive → extend its cascade budget in place;
    - the runner settled (the usual case — the cap ended it) → relaunch a
      chained marker turn for each team member @mentioned in the last
      assistant reply, on a fresh runner with a fresh budget.

    ``422`` when the session has no team or the last reply mentions no
    teammate (nothing to continue).
    """
    session = await asyncio.to_thread(store.get, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        return await _team_continue_core(
            request.app.state.agent, store, request.app.state.job_tracker, session
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/bots/{bot_id}/activity")
async def bot_activity(
    bot_id: str,
    limit: int = 100,
    store: SessionStore = Depends(get_sessions),
) -> list[dict]:
    """Hidden internal sessions belonging to a bot (runs/logs), newest first."""
    if bot_id == MAIN_BOT_ID:
        return []
    pstore = _project_store()
    project = await asyncio.to_thread(pstore.get, bot_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return await asyncio.to_thread(store.list_activity, bot_id, limit=max(1, min(limit, 500)))


@router.post("/bots/{bot_id}/cleanup")
async def bot_cleanup(
    bot_id: str,
    body: dict,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Archive all but the most recent ``keep`` active chats of a bot.

    Body: ``{"keep": 1, "dry_run": false}``. Dry-run returns the preview
    without touching anything. Telegram topic/group side conversations are
    treated like any other chat here — most-recent wins, the rest archive.
    """
    if bot_id == MAIN_BOT_ID:
        raise HTTPException(status_code=422, detail="main bot is not archivable here")
    keep = max(1, int(body.get("keep", 1)))
    dry_run = bool(body.get("dry_run", False))
    pstore = _project_store()
    project = await asyncio.to_thread(pstore.get, bot_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    sessions = await asyncio.to_thread(
        store.list, 9999, 0, project_id=bot_id, include_hidden=False, archived=False
    )
    candidates = [s for s in sessions[keep:]]
    ids = [s.id for s in candidates]
    if not dry_run:
        for sid in ids:
            await asyncio.to_thread(store.mark_archived, sid, True)
    return {
        "kept": [s.id for s in sessions[:keep]],
        "archived": ids,
        "dry_run": dry_run,
    }


@router.post("/bots/cleanup-all")
async def cleanup_all_bots(
    body: dict,
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Global cleanup: one active chat per bot + tidy unprojected chats.

    Body: ``{"keep": 1, "include_unprojected": true, "dry_run": false}``.

    - Per project-bot: archive all but the most recent ``keep`` active chats.
    - Unprojected chats (when ``include_unprojected``): archive all but the
      most recent ``keep`` — the coordinator master session is always kept.
    Archived chats stay restorable via ``PATCH /sessions/{id}/archive``.
    """
    keep = max(1, int(body.get("keep", 1)))
    include_unprojected = bool(body.get("include_unprojected", True))
    dry_run = bool(body.get("dry_run", False))

    coord = _coordinator_cfg(request)
    master_sid = getattr(coord, "session_id", "") or ""

    archived: list[dict] = []
    pstore = _project_store()
    for s in await asyncio.to_thread(pstore.list, limit=200):
        sessions = await asyncio.to_thread(
            store.list, 9999, 0, project_id=s.id, include_hidden=False, archived=False
        )
        for cand in sessions[keep:]:
            archived.append({"id": cand.id, "title": cand.title, "bot_id": s.id})

    if include_unprojected:
        unprojected = await asyncio.to_thread(
            store.list, 9999, 0, include_hidden=False, archived=False, has_project=False
        )
        protected = {master_sid}
        remaining = [s for s in unprojected if s.id not in protected]
        for cand in remaining[keep:]:
            archived.append({"id": cand.id, "title": cand.title, "bot_id": None})

    ids = [a["id"] for a in archived]
    if not dry_run:
        for sid in ids:
            await asyncio.to_thread(store.mark_archived, sid, True)
    return {"archived": archived, "count": len(ids), "dry_run": dry_run}
