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
