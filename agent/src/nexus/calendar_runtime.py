"""Singleton wiring between the calendar heartbeat driver and the live server.

The :mod:`loom.heartbeat` loader instantiates ``Driver`` from
``driver.py`` at registry-scan time, before the FastAPI app has any agent or
session store wired. The driver therefore can't capture them as constructor
args; instead the server lifespan stashes the dispatcher and notifier
callables here, and the driver pulls them lazily on every tick. If they
haven't been set yet (server still starting up), the driver simply skips.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# Signature: (path, event_id, mode) -> dict (with at least "session_id")
Dispatcher = Callable[..., Awaitable[dict[str, Any]]]
# Signature: (payload) -> None. Publishes a calendar_alert notification on
# the cross-session channel so the UI can show a toast.
Notifier = Callable[[dict[str, Any]], None]

_dispatcher: Dispatcher | None = None
_notifier: Notifier | None = None


def set_dispatcher(fn: Dispatcher) -> None:
    global _dispatcher
    _dispatcher = fn


def get_dispatcher() -> Dispatcher | None:
    return _dispatcher


def set_notifier(fn: Notifier) -> None:
    global _notifier
    _notifier = fn


def get_notifier() -> Notifier | None:
    return _notifier
