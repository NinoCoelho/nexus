"""Routes for chat (non-streaming): /health, /skills, /chat, /chat/{sid}/* endpoints.

The streaming endpoint POST /chat/stream lives in chat_stream.py,
which imports the shared tracking dicts from this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse

from pydantic import BaseModel

from ..deps import get_agent, get_sessions, get_registry, get_app_state
from ..schemas import (
    ChatReply,
    ChatRequest,
    DerivedFromDTO,
    DerivedFromSourceDTO,
    Health,
    RespondPayload,
    SkillDetail,
    SkillInfo,
)
from ._sse import keepalive
from ._streaming import TurnAccumulator, build_done_sse, build_error_sse
from ...skills.types import Skill as _SkillModel
from ...agent.context import CURRENT_SESSION_ID
from ...agent.llm import LLMTransportError, MalformedOutputError
from ...agent.loop import Agent
from ...skills.manager import SkillManager
from ...skills.registry import SkillRegistry
from ..session_store import SessionStore


def _maybe_resolve_owner_store(
    request: Request, session_id: str, fallback: SessionStore
) -> SessionStore:
    if not getattr(request.app.state, "multi_user", False):
        return fallback
    role = getattr(request.state, "user_role", None)
    if role != "admin":
        return fallback
    registry = request.app.state.session_registry
    user_store = request.app.state.user_store
    owner_store = registry.store_for_session(session_id, user_store)
    return owner_store if owner_store else fallback


class SkillUpdate(BaseModel):
    body: str

# Mirrors ``nexus.skills.registry._NAME_RE`` — kept local so the import
# route can validate without reaching into a private module attribute.
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

log = logging.getLogger(__name__)

router = APIRouter()

# Tracks the in-flight turn's asyncio.Task per session so /chat/{sid}/cancel
# can interrupt a long-running turn. Populated by the chat_stream generator
# on entry; removed in its finally block.
_inflight_turns: dict[str, asyncio.Task[Any]] = {}
# Session ids where /chat/{sid}/cancel was explicitly invoked, so the
# stream generator's CancelledError handler can distinguish a user-clicked
# Stop from a client-disconnect (browser reload / tab close). Both trigger
# ``task.cancel()`` but the persisted status label should differ.
_user_cancelled: set[str] = set()

_trajectory_logger = (
    __import__("nexus.trajectory", fromlist=["TrajectoryLogger"]).TrajectoryLogger()
    if os.environ.get("NEXUS_TRAJECTORIES") == "1"
    else None
)


@router.get("/health", response_model=Health)
async def health() -> Health:
    import nexus
    return Health(version=nexus.__version__)


def _derived_from_dto(skill: _SkillModel) -> DerivedFromDTO | None:
    df = skill.derived_from
    if df is None:
        return None
    return DerivedFromDTO(
        wizard_ask=df.wizard_ask,
        wizard_built_at=df.wizard_built_at,
        sources=[
            DerivedFromSourceDTO(slug=s.slug, url=s.url, title=s.title)
            for s in df.sources
        ],
    )


@router.get("/skills", response_model=list[SkillInfo])
async def list_skills(registry: SkillRegistry = Depends(get_registry)) -> list[SkillInfo]:
    return [
        SkillInfo(
            name=s.name,
            description=s.description,
            trust=s.trust,
            derived_from=_derived_from_dto(s),
        )
        for s in registry.list()
    ]


@router.get("/skills/{name}", response_model=SkillDetail)
async def get_skill(name: str, registry: SkillRegistry = Depends(get_registry)) -> SkillDetail:
    try:
        s = registry.get(name)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
    return SkillDetail(
        name=s.name,
        description=s.description,
        trust=s.trust,
        body=s.body,
        derived_from=_derived_from_dto(s),
    )


@router.put("/skills/{name}", response_model=SkillDetail)
async def update_skill(
    name: str,
    payload: SkillUpdate,
    registry: SkillRegistry = Depends(get_registry),
) -> SkillDetail:
    """Replace a skill's SKILL.md body. Runs the same guard scan as the
    agent's skill_manage tool; returns 400 if the new content is rejected
    or fails frontmatter validation."""
    try:
        registry.get(name)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
    manager = SkillManager(registry)
    result = manager.invoke("edit", {"name": name, "content": payload.body})
    if not result.ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.message)
    s = registry.get(name)
    return SkillDetail(
        name=s.name,
        description=s.description,
        trust=s.trust,
        body=s.body,
        derived_from=_derived_from_dto(s),
    )


@router.delete("/skills/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    name: str,
    registry: SkillRegistry = Depends(get_registry),
) -> Response:
    """Remove a skill directory from disk and reload the registry.

    Mirrors the agent's ``skill_manage("delete")`` action — including
    the manager's ``shutil.rmtree`` of the skill directory — so deleting
    via the UI and via the agent loop produce the same end state. Bundled
    skills are deletable by design: the seeded-marker still records they
    were copied once, so they don't get re-seeded on the next daemon
    start (only newly-bundled skills do).
    """
    try:
        registry.get(name)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
    manager = SkillManager(registry)
    result = manager.invoke("delete", {"name": name})
    if not result.ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.message)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/skills/export/archive")
async def export_skills_archive(
    registry: SkillRegistry = Depends(get_registry),
) -> Response:
    """Stream a ZIP of every skill directory under ``~/.nexus/skills/``.

    Bundled, user-edited, and agent-authored skills all ride in the same
    archive so the round-trip via :func:`import_skills_archive` produces
    a faithful snapshot — including ``meta.json`` (trust tier, authored
    timestamp, derived-from provenance). The seeded-builtins marker file
    is intentionally excluded so importing on a fresh host re-asserts
    seeding behavior from the new bundle.
    """
    import io
    import zipfile

    skills_dir: Path = registry._dir
    if not skills_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="skills directory does not exist",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            # Skip the seeded-builtins marker — it's regenerated per host
            # and shipping it would mislead the import on a different box.
            for file in entry.rglob("*"):
                if not file.is_file():
                    continue
                arcname = file.relative_to(skills_dir).as_posix()
                zf.write(file, arcname=arcname)

    archive = buf.getvalue()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"nexus-skills-{timestamp}.zip"
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(archive)),
        },
    )


@router.post("/skills/import/archive")
async def import_skills_archive(
    request: Request,
    registry: SkillRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Extract an uploaded ZIP into ``~/.nexus/skills/`` and reload.

    Accepts ``multipart/form-data`` with a single ``file`` field. The
    archive's top-level entries are treated as skill directory names —
    each must contain a ``SKILL.md`` to be considered valid. Existing
    skills with the same name are **overwritten** (the user opted into
    the import; matching the desktop "open zip → replace" mental model).

    Returns a summary of what was imported / skipped so the UI can show
    a useful toast. Path-traversal-safe: every member is resolved against
    the skills root and rejected if it escapes.
    """
    import io
    import zipfile

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="expected multipart/form-data with a 'file' field",
        )
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="missing 'file' field",
        )
    raw = await upload.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="empty upload",
        )

    skills_root: Path = registry._dir
    skills_root_real = Path(os.path.realpath(skills_root))

    imported: list[str] = []
    skipped: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # Group members by their top-level directory (the skill name).
            by_skill: dict[str, list[zipfile.ZipInfo]] = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.split("/", 1)
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    continue
                by_skill.setdefault(parts[0], []).append(info)

            for skill_name, members in by_skill.items():
                if not _NAME_RE.match(skill_name):
                    skipped.append({"name": skill_name, "reason": "invalid name"})
                    continue
                has_skill_md = any(
                    m.filename == f"{skill_name}/SKILL.md" for m in members
                )
                if not has_skill_md:
                    skipped.append({"name": skill_name, "reason": "no SKILL.md"})
                    continue

                # Wipe any pre-existing skill of the same name first so an
                # import is a clean replace, not a partial overlay.
                dest = skills_root / skill_name
                if dest.exists():
                    shutil.rmtree(dest)
                dest.mkdir(parents=True)

                for m in members:
                    rel = m.filename[len(skill_name) + 1:]  # drop leading "<skill>/"
                    target = (dest / rel).resolve()
                    try:
                        target.relative_to(skills_root_real / skill_name)
                    except ValueError:
                        skipped.append(
                            {"name": skill_name, "reason": "path traversal"}
                        )
                        shutil.rmtree(dest)
                        break
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(m) as src, open(target, "wb") as out:
                        out.write(src.read())
                else:
                    imported.append(skill_name)
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid zip archive: {exc}",
        )

    if imported:
        registry.reload()
    return {"imported": imported, "skipped": skipped}


@router.post("/chat", response_model=ChatReply)
async def chat(
    req: ChatRequest,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
    app_state: dict[str, Any] = Depends(get_app_state),
) -> ChatReply:
    session = store.get_or_create(req.session_id, context=req.context, project_id=req.project_id)
    # Bind the session to this request context. Tools that need to
    # address the session (ask_user, trace publish) read it from
    # the ContextVar. Reset on exit so follow-up code — and
    # concurrent unrelated requests — don't inherit stale state.
    token = CURRENT_SESSION_ID.set(session.id)

    plan_data: list[dict[str, Any]] | None = None

    # Resolve any attachments from the request into ``ContentPart``s so the
    # agent loop can build a multipart user message. Empty list when no
    # attachments — call signature stays backwards-compatible.
    attachment_parts: list[Any] = []
    if req.attachments:
        from ...agent.llm import ContentPart as _CP
        from ...multimodal import sniff_mime as _sniff_mime

        for att in req.attachments:
            mime = att.mime_type or _sniff_mime(att.vault_path)
            kind = (
                "image" if mime.startswith("image/")
                else "audio" if mime.startswith("audio/")
                else "document"
            )
            attachment_parts.append(
                _CP(kind=kind, vault_path=att.vault_path, mime_type=mime)
            )

    try:
        turn = await a.run_turn(
            req.message,
            history=session.history,
            context=session.context,
            attachments=attachment_parts or None,
        )
    except LLMTransportError as exc:
        from ...error_classifier import is_budget_exceeded, budget_exceeded_detail
        if is_budget_exceeded(exc):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=budget_exceeded_detail(exc) or "API budget has been exceeded.",
            )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except MalformedOutputError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    finally:
        CURRENT_SESSION_ID.reset(token)
    store.replace_history(session.id, turn.messages)
    # Fold the turn's usage into the session — see session_store.bump_usage.
    store.bump_usage(
        session.id,
        model=turn.model,
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
        tool_calls=turn.tool_calls,
    )
    if _trajectory_logger:
        from .chat_helpers import log_trajectory
        log_trajectory(
            trajectory_logger=_trajectory_logger,
            session_id=session.id,
            turn_index=len(session.history) // 2,
            user_message=req.message,
            history_length=len(session.history),
            context=req.context or "",
            reply=turn.reply or "",
            model=turn.model or "",
            iterations=turn.iterations,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            tool_calls=turn.tool_calls,
        )
    return ChatReply(
        session_id=session.id,
        reply=turn.reply,
        trace=turn.trace,
        skills_touched=turn.skills_touched,
        iterations=turn.iterations,
        plan=plan_data,
    )


@router.get("/chat/{session_id}/pending")
async def chat_pending(
    session_id: str,
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, Any]:
    """Return the current pending ``ask_user`` request, if any.

    Lets the UI recover a modal that would otherwise be missed if
    the ``/chat/{sid}/events`` EventSource wasn't open at publish
    time (page reload, late subscribe, tab restore). The publish
    bus is fire-and-forget; this endpoint is the authoritative
    snapshot.
    """
    ask_user_handler = request.app.state.ask_user_handler
    requests = store.broker.pending(session_id)
    if not requests:
        return {"pending": None}
    r = requests[0]
    payload: dict[str, Any] = {
        "request_id": r.request_id,
        "prompt": r.prompt,
        "kind": r.kind,
        "choices": r.choices,
        "default": r.default,
        "timeout_seconds": r.timeout_seconds,
    }
    extras = ask_user_handler._form_extras.get(r.request_id)
    if extras:
        payload.update(extras)
    return {"pending": payload}


@router.post("/chat/{session_id}/hitl/{request_id}/cancel")
async def chat_hitl_cancel(
    session_id: str,
    request_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, Any]:
    """Cancel a specific HITL request from the bell.

    Two cases:

    * **parked** — durable row in ``hitl_pending``. The agent's turn
      already ended. We just mark the row cancelled and broadcast
      ``user_request_cancelled`` so the bell drops it.
    * **live** — request still held in-memory by the broker; the
      agent's turn is blocked waiting for an answer. Cancel the
      future *and* the inflight turn task so the whole operation
      unwinds (matches the user-facing "cancel this operation"
      meaning of the bell's ✕ button).
    """
    from ..events import SessionEvent

    parked_row = store.get_hitl_pending(request_id)
    if parked_row is not None and parked_row.get("status") == "parked":
        store.cancel_hitl_pending(request_id, reason="user_cancelled")
        store.publish(
            session_id,
            SessionEvent(
                kind="user_request_cancelled",
                data={"request_id": request_id, "reason": "user_cancelled"},
            ),
        )
        return {"ok": True, "cancelled": "parked"}

    # Live path: drop the broker future first so ask_user returns
    # quickly, then abort the inflight turn so the agent stops.
    cancelled_future = store.cancel_pending(session_id, request_id)
    store.publish(
        session_id,
        SessionEvent(
            kind="user_request_cancelled",
            data={"request_id": request_id, "reason": "user_cancelled"},
        ),
    )
    # Try detached ChatTurnRunner first, then inline SSE task.
    from ..services.chat_turn_runner import cancel_running_turn
    cancelled_task = cancel_running_turn(session_id)
    if not cancelled_task:
        task = _inflight_turns.get(session_id)
        if task is not None and not task.done():
            _user_cancelled.add(session_id)
            task.cancel()
            cancelled_task = True
    if not cancelled_future and not cancelled_task:
        # Nothing to cancel — request already resolved or never existed.
        # Returning ok=True keeps the UI state consistent (bell row will
        # already be in answered/cancelled status).
        return {"ok": True, "cancelled": "already_resolved"}
    return {
        "ok": True,
        "cancelled": "live",
        "future_cancelled": cancelled_future,
        "task_cancelled": cancelled_task,
    }


@router.post("/chat/{session_id}/cancel")
async def chat_cancel(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict[str, bool]:
    """Interrupt the currently-streaming turn for this session.

    Cancels any pending HITL wait (so ``ask_user`` returns fast) and
    cancels the task driving the turn. For the main chat path, this is
    the ChatTurnRunner's detached task; for HITL-resume / slash commands,
    it falls back to the SSE generator task. The client will see an
    ``error`` (reason=cancelled) + ``done`` before the stream closes.
    """
    from ..services.chat_turn_runner import cancel_running_turn

    # Unblock any HITL future first so the tool dispatch stops waiting.
    try:
        store.broker.cancel_session(session_id, reason="user_cancelled")
    except Exception:  # noqa: BLE001 — best-effort
        log.exception("broker.cancel_session failed")

    # Try the detached ChatTurnRunner first (main chat path).
    cancelled = cancel_running_turn(session_id)

    # Fall back to inline SSE task (HITL-resume, slash commands).
    if not cancelled:
        task = _inflight_turns.get(session_id)
        if task is not None and not task.done():
            _user_cancelled.add(session_id)
            task.cancel()
            cancelled = True
    return {"ok": True, "cancelled": cancelled}


@router.post("/chat/{session_id}/terminal/{call_id}/kill")
async def kill_terminal(
    session_id: str,
    call_id: str,
    request: Request,
) -> dict[str, Any]:
    from loom.tools.terminal import kill_proc_group

    proc_registry: dict[str, asyncio.subprocess.Process] | None = getattr(
        request.app.state, "terminal_procs", None
    )
    if proc_registry is None:
        raise HTTPException(status_code=404, detail="no terminal registry")
    key = f"{session_id}:{call_id}"
    proc = proc_registry.get(key)
    if proc is None:
        raise HTTPException(status_code=404, detail="no running terminal process")
    kill_proc_group(proc)
    proc_registry.pop(key, None)
    return {"ok": True}


@router.get("/chat/{session_id}/events")
async def chat_events(session_id: str, store: SessionStore = Depends(get_sessions)) -> StreamingResponse:
    """SSE stream of in-turn events for one session. The UI opens
    this once and receives every ``iter``, ``tool_call``,
    ``tool_result``, ``user_request``, and ``reply`` until the
    client disconnects.

    The subscribe queue is an in-memory pub-sub keyed by session
    id, so the UI can open this stream *before* sending the first
    ``/chat`` message without a chicken-and-egg problem — no DB
    row is required to subscribe. The session is materialized
    lazily by ``POST /chat/stream``. This keeps page-reloads from
    littering the store with empty sessions.

    Note: ``store`` is injected via Depends here. FastAPI's
    dependency resolution on streaming endpoints interacts badly
    with some ASGI transports (the helyx port documented this as
    an ``httpx.ASGITransport`` hang) — direct closure capture is
    the same object at runtime and avoids the pitfall.
    """

    async def stream() -> AsyncIterator[bytes]:
        yield b": subscribed\n\n"
        async for event in keepalive(store.subscribe(session_id), interval=20.0):
            if event is None:
                yield b": ping\n\n"
                continue
            yield event.to_sse()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx: don't buffer SSE
        },
    )


@router.get("/chat/{session_id}/turn/active")
async def turn_active(session_id: str) -> dict[str, Any]:
    """Check whether a background chat turn is still running for this session.

    Called by the frontend after a stream drop to decide whether to attempt
    a reconnect (``GET /chat/{sid}/turn/stream``) or fall back to history reload.
    """
    from ..services.chat_turn_runner import get_running_turn

    runner = get_running_turn(session_id)
    if runner is not None:
        return {
            "running": True,
            "started_at": runner.started_at.isoformat(),
        }
    return {"running": False}


@router.get("/chat/{session_id}/turn/stream")
async def turn_stream(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> StreamingResponse:
    """Reconnect to a running background turn.

    Subscribes to the session bus with replay so the client sees the full
    turn from the beginning (deltas, tools, done/error). If no turn is
    active, returns an immediate ``done`` event so the client closes cleanly.
    """
    from ..services.chat_turn_runner import get_running_turn
    from ._streaming import TurnAccumulator

    runner = get_running_turn(session_id)

    async def stream() -> AsyncIterator[bytes]:
        if runner is None:
            payload = json.dumps({
                "session_id": session_id,
                "reply": "",
                "trace": [],
                "skills_touched": [],
                "iterations": 0,
                "usage": {},
                "model": "",
            })
            yield f"event: done\ndata: {payload}\n\n".encode()
            return

        acc = TurnAccumulator()
        async for sevent in keepalive(
            store.subscribe_with_replay(session_id), interval=15.0,
        ):
            if sevent is None:
                yield b": ping\n\n"
                continue
            for frame in acc.process_event(sevent.data):
                yield frame.encode()
            if sevent.data.get("type") == "done":
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/chat/{session_id}/respond", status_code=status.HTTP_204_NO_CONTENT
)
async def chat_respond(
    session_id: str,
    body: RespondPayload,
    request: Request,
    store: SessionStore = Depends(get_sessions),
) -> None:
    store = _maybe_resolve_owner_store(request, session_id, store)
    """Resolve a pending ``ask_user`` request. 404 when the request
    is unknown — most commonly because it timed out or the session
    was reset before the user clicked through.

    For requests that have *parked* (the agent ended the turn waiting
    for an async answer), this endpoint returns 409 with the parked
    request id; the UI must resume via
    ``POST /chat/{session_id}/hitl/{request_id}/answer``.
    """
    parked = store.get_hitl_pending(body.request_id)
    if parked is not None and parked.get("status") == "parked":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": "parked",
                "request_id": body.request_id,
                "session_id": parked.get("session_id"),
                "message": (
                    "request is parked; answer via "
                    f"/chat/{parked.get('session_id')}/hitl/"
                    f"{body.request_id}/answer to resume the turn"
                ),
            },
        )
    resolved = store.resolve_pending(
        session_id, body.request_id, body.answer
    )
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no pending request {body.request_id!r} on session "
                f"{session_id!r} (timed out or already resolved)"
            ),
        )


@router.post("/chat/{session_id}/hitl/{request_id}/answer")
async def chat_hitl_answer(
    session_id: str,
    request_id: str,
    body: RespondPayload,
    request: Request,
    a: Agent = Depends(get_agent),
    store: SessionStore = Depends(get_sessions),
) -> StreamingResponse:
    store = _maybe_resolve_owner_store(request, session_id, store)
    """Resume a parked ``ask_user`` request and stream the agent's
    continuation as SSE.

    Two outcomes:

    1. **Idempotent duplicate** — another client already answered. Returns
       the recorded answer in a single ``done`` SSE frame and closes.
    2. **First answer wins** — marks the row answered, decodes the answer
       into a tool result, drives ``Agent.continue_after_hitl`` and forwards
       events identically to ``/chat/stream``.

    A 404 means the request was never parked (timed out without parking,
    or already cleaned up).
    """
    if request_id != body.request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path request_id and body.request_id must match",
        )

    parked = store.get_hitl_pending(request_id)
    if parked is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no parked request {request_id!r}",
        )
    if parked.get("session_id") != session_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request_id belongs to a different session",
        )

    # Decode the answer the same way ask_user_tool decodes broker answers
    # (form payloads come in as JSON strings; everything else as plain text).
    raw_answer = body.answer
    decoded: Any = raw_answer
    if isinstance(raw_answer, str):
        try:
            decoded = json.loads(raw_answer)
        except (json.JSONDecodeError, ValueError):
            decoded = raw_answer

    row = store.mark_hitl_pending_answered(request_id, decoded)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no parked request {request_id!r}",
        )

    already_answered = bool(row.get("already_answered"))

    async def event_generator() -> AsyncIterator[str]:
        if already_answered:
            payload = {
                "session_id": session_id,
                "reply": "",
                "duplicate": True,
                "answer": row.get("answer_json"),
            }
            yield f"event: done\ndata: {json.dumps(payload)}\n\n"
            return

        token = CURRENT_SESSION_ID.set(session_id)
        current = asyncio.current_task()
        if current is not None:
            _inflight_turns[session_id] = current
        acc = TurnAccumulator()
        try:
            async for event in a.continue_after_hitl(
                session_id=session_id,
                request_id=request_id,
                answer=decoded,
            ):
                etype = event.get("type")
                if etype == "done":
                    usage = event.get("usage") or {}
                    try:
                        store.bump_usage(
                            session_id,
                            model=usage.get("model"),
                            input_tokens=int(usage.get("input_tokens") or 0),
                            output_tokens=int(usage.get("output_tokens") or 0),
                            tool_calls=int(usage.get("tool_calls") or 0),
                        )
                    except Exception:  # noqa: BLE001
                        log.exception("bump_usage failed (resume)")
                    done_payload = {
                        "session_id": event.get("session_id") or session_id,
                        "reply": event.get("reply", ""),
                        "trace": event.get("trace", []),
                        "skills_touched": event.get("skills_touched", []),
                        "iterations": event.get("iterations", 0),
                        "usage": usage,
                        "model": usage.get("model"),
                    }
                    yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"
                else:
                    for frame in acc.process_event(event):
                        yield frame
        except (LLMTransportError, MalformedOutputError) as exc:
            acc.partial_status = "llm_error"
            yield build_error_sse(detail=str(exc))
        except asyncio.CancelledError:
            acc.partial_status = "cancelled"
            yield build_error_sse(detail="cancelled by user", reason="cancelled")
            yield build_done_sse(session_id=session_id, reply="")
        except Exception as exc:  # noqa: BLE001
            acc.partial_status = "crashed"
            log.exception("hitl resume crashed")
            yield build_error_sse(detail=f"{type(exc).__name__}: {exc}")
            yield build_done_sse(session_id=session_id, reply="")
        finally:
            if acc.final_messages is not None:
                try:
                    store.replace_history(session_id, acc.final_messages)
                except Exception:  # noqa: BLE001
                    log.exception("replace_history (resume) failed")
            elif acc.accumulated_text or acc.accumulated_tools:
                try:
                    sess = store.get(session_id)
                    base = list(sess.history) if sess else []
                    store.persist_partial_turn(
                        session_id,
                        base_history=base,
                        user_message="",
                        assistant_text=acc.accumulated_text,
                        tool_calls=acc.accumulated_tools,
                        status_note=acc.partial_status,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("persist_partial_turn (resume) failed")
            try:
                CURRENT_SESSION_ID.reset(token)
            except ValueError:
                log.debug("CURRENT_SESSION_ID reset across contexts (resume)")
            if _inflight_turns.get(session_id) is current:
                _inflight_turns.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
