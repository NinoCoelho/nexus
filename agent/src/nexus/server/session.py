"""In-memory session store."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from threading import Lock

from ..agent.llm import ChatMessage


@dataclass
class Session:
    id: str
    history: list[ChatMessage] = field(default_factory=list)
    context: str | None = None


class SessionStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, Session] = {}

    def create(self, context: str | None = None) -> Session:
        sid = uuid.uuid4().hex
        with self._lock:
            s = Session(id=sid, context=context)
            self._sessions[sid] = s
            return s

    def get_or_create(self, session_id: str | None, context: str | None = None) -> Session:
        if session_id is None:
            return self.create(context=context)
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                if existing.context is None and context is not None:
                    existing.context = context
                return existing
            s = Session(id=session_id, context=context)
            self._sessions[session_id] = s
            return s

    def replace_history(self, session_id: str, history: list[ChatMessage]) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].history = history

    def reset(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id].history = []
