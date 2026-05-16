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

Dispatcher = Callable[..., Awaitable[dict[str, Any]]]
Notifier = Callable[[dict[str, Any]], None]

_dispatcher: Dispatcher | None = None
_notifier: Notifier | None = None
_alarm_store: Any = None


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


def set_alarm_store(store: Any) -> None:
    global _alarm_store
    _alarm_store = store


def get_alarm_store() -> Any:
    return _alarm_store
