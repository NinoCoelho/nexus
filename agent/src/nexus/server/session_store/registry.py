"""Per-user session store registry for multi-user mode.

In single-user mode the server holds one ``SessionStore`` instance.
In multi-user mode each user gets their own ``sessions.sqlite`` under
``~/.nexus/users/<id>/``, created lazily on first request.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from .store import SessionStore as _SessionStore
from ...home import _ROOT


class UserSessionRegistry:
    def __init__(self) -> None:
        self._stores: dict[str, _SessionStore] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str) -> _SessionStore:
        s = self._stores.get(user_id)
        if s is not None:
            return s
        with self._lock:
            s = self._stores.get(user_id)
            if s is not None:
                return s
            db_path = _ROOT / "users" / user_id / "sessions.sqlite"
            s = _SessionStore(db_path=db_path)
            self._stores[user_id] = s
            return s

    def store_for_session(self, session_id: str, user_store: Any) -> _SessionStore | None:
        owner_id = user_store.session_owner(session_id)
        if not owner_id:
            return None
        return self.get(owner_id)

    def all_stores(self) -> dict[str, _SessionStore]:
        return dict(self._stores)

    def close_all(self) -> None:
        for s in self._stores.values():
            try:
                s._loom.close()
            except Exception:
                pass
        self._stores.clear()
