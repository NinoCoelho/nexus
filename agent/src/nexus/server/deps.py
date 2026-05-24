"""FastAPI dependency getters for shared app state.

All route modules import their dependencies from here so that
handler functions never reference ``app.py`` directly (avoids circular imports).
Each getter reads from ``request.app.state`` which ``create_app()`` populates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from ..agent.loop import Agent
    from ..skills.registry import SkillRegistry
    from .session_store import SessionStore
    from .settings import SettingsStore


def get_agent(request: Request) -> "Agent":
    return request.app.state.agent


def get_sessions(request: Request) -> "SessionStore":
    if getattr(request.app.state, "multi_user", False):
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return request.app.state.session_registry.get(user_id)
    return request.app.state.sessions


class SessionStoreProxy:
    """Transparent proxy that delegates to the correct per-user SessionStore.

    In single-user mode (``multi_user`` is False or no registry), every
    attribute access resolves to the default store — identical to the
    pre-proxy behaviour.

    In multi-user mode, each attribute access reads ``CURRENT_SESSION_ID``
    from the current asyncio context and resolves the session owner's store
    via the ``UserSessionRegistry``.  This ensures that HITL publishes,
    broker registrations, pending-future management, and snapshot
    persistence all land in the per-user ``sessions.sqlite`` whose SSE
    subscribers are the ones who should see the events.

    The proxy is read-only: all attribute access delegates to the resolved
    store.  Attributes that are set by callers (e.g.
    ``store._latest_input_mode``) must be set on the actual per-user store,
    not on the proxy.  Route handlers already receive the correct store via
    ``get_sessions()``, so this is not a problem in practice.
    """

    __slots__ = ("_default", "_app_state")

    def __init__(self, default: "SessionStore", app_state: Any) -> None:
        object.__setattr__(self, "_default", default)
        object.__setattr__(self, "_app_state", app_state)

    def _resolve(self, session_id: str | None = None) -> "SessionStore":
        state = object.__getattribute__(self, "_app_state")
        if not getattr(state, "multi_user", False):
            return object.__getattribute__(self, "_default")
        sid = session_id
        if sid is None:
            from ..agent.context import CURRENT_SESSION_ID
            sid = CURRENT_SESSION_ID.get()
        if not sid:
            return object.__getattribute__(self, "_default")
        registry = getattr(state, "session_registry", None)
        user_store = getattr(state, "user_store", None)
        if registry is None or user_store is None:
            return object.__getattribute__(self, "_default")
        owner = registry.store_for_session(sid, user_store)
        return owner or object.__getattribute__(self, "_default")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def claim_for_parent(self, session_id: str) -> None:
        """Claim *session_id* for the same user that owns the current session.

        Used by vault dispatch / calendar triggers that create new sessions
        inside a turn.  In single-user mode this is a silent no-op.
        """
        state = object.__getattribute__(self, "_app_state")
        if not getattr(state, "multi_user", False):
            return
        from ..agent.context import CURRENT_SESSION_ID
        parent_sid = CURRENT_SESSION_ID.get()
        if not parent_sid:
            return
        user_store = getattr(state, "user_store", None)
        if not user_store:
            return
        owner_id = user_store.session_owner(parent_sid)
        if owner_id:
            user_store.claim_session(session_id, owner_id)

    def __repr__(self) -> str:
        return f"SessionStoreProxy(default={object.__getattribute__(self, '_default')!r})"


def get_sessions_for_session(request: Request, session_id: str) -> "SessionStore":
    if getattr(request.app.state, "multi_user", False):
        registry = request.app.state.session_registry
        user_store = request.app.state.user_store
        owner_store = registry.store_for_session(session_id, user_store)
        if owner_store:
            return owner_store
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return registry.get(user_id)
    return request.app.state.sessions


def get_settings_store(request: Request) -> "SettingsStore":
    return request.app.state.settings_store


def get_registry(request: Request) -> "SkillRegistry":
    return request.app.state.registry


def get_app_state(request: Request) -> dict[str, Any]:
    """Return the mutable cfg+prov_reg dict (by reference)."""
    return request.app.state.mutable_state


def get_graphrag_cfg(request: Request) -> Any:
    return request.app.state.graphrag_cfg


def get_job_tracker(request: Request) -> Any:
    return request.app.state.job_tracker


def get_locale(request: Request) -> str:
    """Resolve the request's preferred language.

    Order: X-Locale request header → ``cfg.ui.language`` → ``"en"``. Coerced
    to a supported language by ``i18n.normalize`` so a stray header value
    can't propagate further.
    """
    from ..i18n import normalize

    header = request.headers.get("x-locale") or request.headers.get("X-Locale")
    if header:
        return normalize(header)
    cfg = request.app.state.mutable_state.get("cfg") if hasattr(request.app.state, "mutable_state") else None
    return normalize(getattr(getattr(cfg, "ui", None), "language", None))
