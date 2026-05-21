"""Nexus home directory resolution.

In single-user mode (the default) every path resolves to ``~/.nexus/`` directly —
identical to the pre-multi-user behaviour.  In multi-user mode each user gets
an isolated subtree at ``~/.nexus/users/<user_id>/``.

The switch is driven entirely by the ``CURRENT_USER_ID`` ContextVar set by the
multi-user auth middleware.  When it is ``None`` (single-user, or a test that
doesn't set it) the early-return fires and no per-user logic runs.
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

_ROOT = Path.home() / ".nexus"

_USER_HOME_DIR: ContextVar[Path | None] = ContextVar(
    "nexus_user_home_dir", default=None,
)


def set_user_home(path: Path | None) -> None:
    _USER_HOME_DIR.set(path)


def _base() -> Path:
    p = _USER_HOME_DIR.get(None)
    if p is not None:
        return p
    return _ROOT


def root() -> Path:
    return _base()


def vault_root() -> Path:
    p = _base()
    p = p / "vault"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sessions_db() -> Path:
    return _base() / "sessions.sqlite"


def skills_dir() -> Path:
    return _base() / "skills"


def dream_db() -> Path:
    return _base() / "dream_state.sqlite"


def memory_dir() -> Path:
    return _base() / "memory"


def memory_index_db() -> Path:
    return _base() / "memory" / "_index" / "memory.sqlite"


def vault_index_db() -> Path:
    return _base() / "vault_index.sqlite"


def vault_meta_db() -> Path:
    return _base() / "vault_meta.sqlite"


def vault_history_dir() -> Path:
    return _base() / ".vault-history"


def vault_history_cursors() -> Path:
    return _base() / ".vault-history-cursors.json"


def vault_tool_cache() -> Path:
    return _base() / "vault" / ".tool-cache"


def vault_session_memory() -> Path:
    return _base() / "vault" / ".session-memory"


def dreams_dir() -> Path:
    return _base() / "vault" / "dreams"


def dream_suggestions_dir() -> Path:
    return _base() / "vault" / "dreams" / "suggestions"


def dream_insights_dir() -> Path:
    return _base() / "vault" / "memory" / "dream-insights"


def precomputed_dir() -> Path:
    return _base() / "vault" / "memory" / "precomputed"


def trajectories_dir() -> Path:
    return _base() / "trajectories"


def venvs_dir() -> Path:
    return _base() / "venvs"


def shared_vault_root() -> Path:
    p = _ROOT / "shared" / "vault"
    p.mkdir(parents=True, exist_ok=True)
    return p
