"""Coordinator ("master chat") service.

One designated session that acts as the user's always-available deputy:
it can inspect projects/sessions, dispatch turns into any session, and —
via the ``coordinator_sweep`` heartbeat driver — run periodic read-only
sweeps whose digest is delivered to the owner's Telegram DM.

Identity lives in ``[coordinator]`` config (``session_id`` auto-provisioned
on first use and persisted back to the config file). The service instance
is created once in ``app_lifespan`` and exposed module-level
(``get_service()``) so heartbeat drivers — which are constructed without
app dependencies — can reach it.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, time as dt_time
from typing import TYPE_CHECKING, Any

from .config_file import load_cached, save as save_config

if TYPE_CHECKING:
    from .agent.loop import Agent
    from .server.session_store import SessionStore

log = logging.getLogger(__name__)

_service: "CoordinatorService | None" = None

# Set during proactive sweeps: the dispatch tool refuses to run so a sweep
# is guaranteed read-only no matter what the sweep prompt says.
_SWEEP_MODE: ContextVar[bool] = ContextVar("coordinator_sweep_mode", default=False)


def get_service() -> "CoordinatorService | None":
    return _service


def set_service(service: "CoordinatorService | None") -> None:
    global _service
    _service = service


def in_sweep_mode() -> bool:
    return _SWEEP_MODE.get()


@contextmanager
def sweep_mode():
    token = _SWEEP_MODE.set(True)
    try:
        yield
    finally:
        _SWEEP_MODE.reset(token)


def _parse_quiet_hours(spec: str) -> tuple[dt_time, dt_time] | None:
    """Parse "23:00-08:00" into (start, end); None when unset/invalid."""
    if not spec.strip():
        return None
    try:
        a, b = spec.strip().split("-", 1)
        return dt_time.fromisoformat(a.strip()), dt_time.fromisoformat(b.strip())
    except ValueError:
        log.warning("coordinator: bad quiet_hours %r — ignoring", spec)
        return None


def in_quiet_hours(spec: str, now: datetime | None = None) -> bool:
    window = _parse_quiet_hours(spec)
    if window is None:
        return False
    start, end = window
    t = (now or datetime.now()).time()
    if start <= end:
        return start <= t < end
    # Wraps midnight (e.g. 23:00-08:00).
    return t >= start or t < end


class CoordinatorService:
    def __init__(self, store: SessionStore, agent: Agent, tracker: Any) -> None:
        self._store = store
        self._agent = agent
        self._tracker = tracker

    @property
    def store(self) -> SessionStore:
        return self._store

    @property
    def agent(self) -> Agent:
        return self._agent

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def config(self) -> Any:
        return load_cached().coordinator

    @property
    def session_id(self) -> str | None:
        sid = self.config.session_id
        return sid or None

    def is_coordinator(self, session_id: str | None) -> bool:
        sid = self.session_id
        return bool(sid and session_id and session_id == sid)

    def ensure_session(self) -> str:
        """Return the coordinator session id, creating + persisting it once."""
        cfg = load_cached()
        sid = cfg.coordinator.session_id
        if sid and self._store.get(sid) is not None:
            return sid
        session = self._store.create(
            context=(
                "Coordinator master chat — the user's deputy with a view over "
                "every project and chat."
            ),
        )
        try:
            self._store.rename(session.id, "Master")
        except Exception:
            log.exception("coordinator: could not title the master session")
        sid = session.id
        cfg.coordinator.session_id = sid
        try:
            save_config(cfg)
        except Exception:
            log.exception("coordinator: could not persist session_id to config")
        log.info("coordinator: provisioned master session %s", sid)
        return sid

    def adopt_dm_bindings(self) -> int:
        """Rebind existing DM bindings to the master session.

        A DM used before the coordinator was enabled keeps its old
        throwaway binding — without this migration the owner's Telegram
        conversation would keep landing in the old chat while the master
        sits empty. Called whenever the service is wired (boot + hot
        enable). Topic/group bindings are left alone. Returns the number
        of bindings migrated.
        """
        from .telegram.bindings import TelegramBindingStore

        sid = self.session_id
        if not sid:
            return 0
        store = TelegramBindingStore()
        migrated = 0
        for b in store.list_all():
            if b.kind == "dm" and b.active_session_id != sid:
                store.set_active_session(b.chat_id, b.thread_id, sid)
                migrated += 1
        if migrated:
            log.info("coordinator: adopted %d dm binding(s) into the master session", migrated)
        return migrated

    # ------------------------------------------------------------------
    # nexus_sessions tool
    # ------------------------------------------------------------------

    def inspect(
        self,
        *,
        action: str,
        project_id: str = "",
        session_id: str = "",
        q: str = "",
        tail: int = 20,
    ) -> dict[str, Any]:
        if action == "projects":
            from .home import sessions_db
            from .server.project_store import ProjectStore

            project_store = ProjectStore(sessions_db())
            projects = []
            for summary in project_store.list(limit=200):
                full = project_store.get(summary.id)
                projects.append(full if full is not None else summary)
            sessions = self._store.list(limit=9999, offset=0, include_hidden=False)
            counts: dict[str, int] = {}
            last_titles: dict[str, str] = {}
            for s in sessions:
                if s.project_id:
                    counts[s.project_id] = counts.get(s.project_id, 0) + 1
                    last_titles.setdefault(s.project_id, s.title or "Untitled")
            return {
                "ok": True,
                "projects": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "instructions": p.instructions,
                        "session_count": counts.get(p.id, 0),
                        "latest_chat": last_titles.get(p.id),
                    }
                    for p in projects
                ],
            }

        if action == "sessions":
            kwargs: dict[str, Any] = {}
            if project_id:
                kwargs["project_id"] = project_id
            rows = self._store.list(limit=100, offset=0, include_hidden=False, **kwargs)
            ql = q.strip().lower()
            if ql:
                rows = [r for r in rows if ql in (r.title or "").lower()]
            return {
                "ok": True,
                "sessions": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "message_count": s.message_count,
                        "project_id": s.project_id,
                        "updated_at": s.updated_at,
                    }
                    for s in rows
                ],
            }

        if action == "read":
            if not session_id:
                return {"ok": False, "error": "session_id is required"}
            session = self._store.get(session_id)
            if session is None:
                return {"ok": False, "error": f"unknown session {session_id}"}
            history = list(session.history)
            tail = max(1, min(int(tail), 50))
            lines = []
            for m in history[-tail:]:
                role = getattr(getattr(m, "role", None), "value", "user")
                content = (getattr(m, "content", "") or "").strip()
                if content:
                    lines.append(f"[{role}] {content[:600]}")
            return {
                "ok": True,
                "session_id": session_id,
                "title": session.title,
                "message_count": len(history),
                "tail": "\n\n".join(lines) or "(empty)",
            }

        return {"ok": False, "error": f"unknown action {action!r}"}

    # ------------------------------------------------------------------
    # session_dispatch tool
    # ------------------------------------------------------------------

    def _dispatch_approved(self, project_id: str | None) -> bool:
        """Whether dispatching into ``project_id`` is pre-approved.

        ``[coordinator].auto_approve`` holds ``"all"`` or a list of project
        ids that may be dispatched without an explicit user confirmation.
        Empty list (default) = every dispatch must be confirmed.
        """
        rules = list(getattr(self.config, "auto_approve", None) or [])
        if not rules:
            return False
        normalized = {str(r).strip().lower() for r in rules}
        if "all" in normalized or "*" in normalized:
            return True
        return bool(project_id and project_id.lower() in normalized)

    async def dispatch(
        self,
        *,
        session_id: str,
        message: str,
        wait: bool = True,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        if in_sweep_mode():
            return {
                "ok": False,
                "error": "dispatch is disabled during proactive sweeps (read-only mode)",
            }
        if self.is_coordinator(session_id):
            return {"ok": False, "error": "refusing to dispatch into the coordinator itself"}
        if not message.strip():
            return {"ok": False, "error": "message is required"}
        session = self._store.get(session_id)
        if session is None:
            return {"ok": False, "error": f"unknown session {session_id}"}

        if not self._dispatch_approved(getattr(session, "project_id", None)) and not confirmed:
            return {
                "ok": False,
                "needs_confirmation": True,
                "error": (
                    "This dispatch is not pre-approved. Use the ask_user tool to "
                    "confirm with the user first (the prompt reaches their Telegram "
                    "or the web UI), then retry session_dispatch with confirmed=true. "
                    "Never set confirmed=true without an explicit yes."
                ),
                "session_id": session_id,
            }

        from .server.services.turn_launcher import launch_turn

        outcome = await launch_turn(
            agent=self._agent,
            store=self._store,
            tracker=self._tracker,
            session=session,
            message=message,
        )
        if outcome.error:
            return {"ok": False, "error": outcome.error}
        if outcome.queued:
            return {"ok": True, "queued": True, "session_id": session_id}
        if not wait or outcome.runner is None:
            return {"ok": True, "dispatched": True, "session_id": session_id}

        try:
            await outcome.runner.task
        except Exception as exc:
            return {"ok": False, "error": f"turn failed: {exc}", "session_id": session_id}
        refreshed = self._store.get(session_id)
        reply = ""
        if refreshed is not None:
            for m in reversed(list(refreshed.history)):
                role = getattr(getattr(m, "role", None), "value", "")
                content = (getattr(m, "content", "") or "").strip()
                if role == "assistant" and content:
                    reply = content
                    break
        return {"ok": True, "session_id": session_id, "reply": reply[:4000]}
