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


# Tools a read-only sweep actually needs. Everything else in the registry
# (terminal, vault_write, datatable_manage, dashboard_manage, …) is schema the
# sweep pays for on every one of its iterations and must never use anyway —
# ~15K tokens of JSON down to roughly 2K.
SWEEP_TOOLS: frozenset[str] = frozenset(
    {
        "nexus_sessions",
        "vault_read",
        "vault_list",
        "vault_search",
        "calendar_manage",
    }
)


# Payload caps for ``nexus_sessions``. Every byte here lands in the model's
# context, and the coordinator reads broadly by design, so the list views stay
# summary-shaped and detail is fetched per item on demand.
_PROJECTS_LIMIT = 50
_PROJECT_DESC_CHARS = 300
_PROJECT_SESSIONS_LIMIT = 20
_SESSIONS_LIMIT = 100
_READ_MAX_TAIL = 50
_READ_CHARS_PER_MSG = 400


def _clip(text: str, limit: int = _PROJECT_DESC_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _flatten_content(msg: Any) -> str:
    """Message text as a string, tolerating multipart bodies.

    ``content`` is ``str | list[ContentPart] | None``; the old
    ``(getattr(m, "content", "") or "").strip()`` raised ``AttributeError`` on
    the list form, so reading any session that contained an attachment broke
    the tool outright.
    """
    from .agent.loop.relevance import _content_text

    try:
        return _content_text(msg) or ""
    except Exception:  # noqa: BLE001 — never fail a read over one odd message
        content = getattr(msg, "content", None)
        return content if isinstance(content, str) else ""


class CoordinatorService:
    def __init__(self, store: SessionStore, agent: Agent, tracker: Any) -> None:
        self._store = store
        self._agent = agent
        self._tracker = tracker
        self._sweep_agent: Agent | None = None

    @property
    def store(self) -> SessionStore:
        return self._store

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def sweep_agent(self) -> Agent:
        """Agent for periodic sweeps, with a read-only restricted toolset.

        A second ``Agent`` gets its own ``ToolRegistry``, which is the only
        way to vary the tool payload: the main registry is a singleton shared
        by every session, and the master chat (served by the main agent)
        genuinely needs the full set. Same construction as the sub-agent
        runner in ``server/app_subagents.py``.

        Built lazily and cached — lazily so it is created after MCP servers
        have registered into the *main* registry at startup, keeping MCP tool
        schemas out of the sweep's payload too.
        """
        if self._sweep_agent is None:
            from .agent.loop import Agent as _Agent

            main = self._agent
            self._sweep_agent = _Agent(
                provider=main._nexus_provider,
                registry=main._registry,
                provider_registry=main._provider_registry,
                nexus_cfg=main._nexus_cfg,
                home=main._home,
                permissions=main._permissions,
                tool_allowlist=set(SWEEP_TOOLS),
            )
            self._sweep_agent._sessions = self._store
            log.info("coordinator: built sweep agent with %d tools", len(SWEEP_TOOLS))
        return self._sweep_agent

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
        """Return the coordinator session id, creating + persisting it once.

        The session title tracks the configured ``name`` — renaming the
        coordinator in Settings renames the chat on the next sync.
        """
        cfg = load_cached()
        sid = cfg.coordinator.session_id
        existing = self._store.get(sid) if sid else None
        if existing is not None:
            name = (cfg.coordinator.name or "Master").strip() or "Master"
            if (existing.title or "") != name:
                try:
                    self._store.rename(sid, name)
                except Exception:
                    log.exception("coordinator: could not retitle the master session")
            return sid
        session = self._store.create(
            context=(
                "Coordinator master chat — the user's deputy with a view over "
                "every project and chat."
            ),
        )
        name = (cfg.coordinator.name or "Master").strip() or "Master"
        try:
            self._store.rename(session.id, name)
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
        updated_since: str = "",
        include_tools: bool = False,
    ) -> dict[str, Any]:
        if action == "projects":
            return self._inspect_projects(project_id)

        if action == "sessions":
            kwargs: dict[str, Any] = {}
            if project_id:
                kwargs["project_id"] = project_id
            rows = self._store.list(
                limit=_SESSIONS_LIMIT, offset=0, include_hidden=False, **kwargs
            )
            ql = q.strip().lower()
            if ql:
                rows = [r for r in rows if ql in (r.title or "").lower()]
            since = (updated_since or "").strip()
            if since:
                # Lexicographic compare works on the stored ISO timestamps and
                # lets a sweep ask only for what changed instead of pulling a
                # full page of rows it will ignore.
                rows = [r for r in rows if (r.updated_at or "") >= since]
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
            tail = max(1, min(int(tail), _READ_MAX_TAIL))
            lines = []
            skipped_tools = 0
            for m in history[-tail:]:
                role = getattr(getattr(m, "role", None), "value", "user")
                if not include_tools and str(role).lower() == "tool":
                    # Tool JSON dominates a tool-heavy tail and tells a digest
                    # nothing the assistant's own message doesn't.
                    skipped_tools += 1
                    continue
                content = _flatten_content(m).strip()
                if content:
                    lines.append(f"[{role}] {content[:_READ_CHARS_PER_MSG]}")
            out: dict[str, Any] = {
                "ok": True,
                "session_id": session_id,
                "title": session.title,
                "message_count": len(history),
                "tail": "\n\n".join(lines) or "(empty)",
            }
            if skipped_tools:
                out["skipped_tool_messages"] = skipped_tools
            return out

        return {"ok": False, "error": f"unknown action {action!r}"}

    def _inspect_projects(self, project_id: str = "") -> dict[str, Any]:
        """Project overview, or one project's full record.

        The list view deliberately omits ``instructions`` and truncates
        ``description``: it used to return every project's full instructions
        text untruncated for up to 200 projects, which was by far the largest
        uncapped payload the coordinator could pull into its context. Pass
        ``project_id`` to get one project in full.
        """
        from .home import sessions_db
        from .server.project_store import ProjectStore

        project_store = ProjectStore(sessions_db())

        if project_id:
            full = project_store.get(project_id)
            if full is None:
                return {"ok": False, "error": f"unknown project {project_id}"}
            sessions = self._store.list(
                limit=_SESSIONS_LIMIT, offset=0, include_hidden=False,
                project_id=project_id,
            )
            return {
                "ok": True,
                "project": {
                    "id": full.id,
                    "name": full.name,
                    "description": full.description,
                    "instructions": full.instructions,
                    "session_count": len(sessions),
                    "sessions": [
                        {"id": s.id, "title": s.title, "updated_at": s.updated_at}
                        for s in sessions[:_PROJECT_SESSIONS_LIMIT]
                    ],
                },
            }

        # Summaries already carry id/name/description, so there is no need for
        # the per-project get() this used to do (201 queries for 200 projects).
        summaries = project_store.list(limit=_PROJECTS_LIMIT)
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
                    "description": _clip(getattr(p, "description", "") or ""),
                    "session_count": counts.get(p.id, 0),
                    "latest_chat": last_titles.get(p.id),
                }
                for p in summaries
            ],
            "hint": (
                "Descriptions are truncated and instructions omitted. Call "
                "nexus_sessions(action='projects', project_id=...) for one "
                "project's full record."
            ),
        }

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
            origin="coordinator",
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
