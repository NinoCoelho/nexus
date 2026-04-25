"""Session store package — re-exports ``SessionStore`` for backwards compatibility.

All importers that used ``from nexus.server.session_store import SessionStore``
continue to work without modification.
"""

from .models import Session, SessionSummary
from .store import SessionStore

__all__ = ["SessionStore", "Session", "SessionSummary"]
