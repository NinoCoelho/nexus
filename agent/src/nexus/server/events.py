"""Session events published through the SSE side-channel.

Events are what the server emits to UI subscribers during a live
chat turn. They cover three distinct things:

* **Trace**: incidental narration for the UI's transparency view
  (``iter``, ``tool_call``, ``tool_result``, ``reply``). Informational.
* **HITL request**: the agent has paused and needs the user to pick
  an answer (``user_request``). The UI is expected to react (show a
  dialog) — these are never purely informational.
* **HITL cancel**: a pending request was cancelled (timeout, session
  reset, etc.) — lets the UI close the dialog gracefully.

Kept as a plain dataclass + SSE formatter rather than a full event bus
so the shape is obvious from one file. When the event vocabulary grows
past ~8 kinds, consider promoting `kind` to an Enum.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

log = logging.getLogger(__name__)


def _json_default(obj: Any) -> Any:
    """Last-resort coercion for objects ``json.dumps`` can't handle.

    A non-serializable payload must never kill an SSE channel (the
    2026-11 "spinner forever" bug was exactly a ``ChatMessage`` riding
    a ``done`` event into ``json.dumps``).
    """
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except Exception:  # noqa: BLE001 — fall through to str()
            pass
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return json_safe(asdict(obj))
        except Exception:  # noqa: BLE001
            pass
    value = getattr(obj, "value", None)  # Enum
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(obj)


def json_safe(obj: Any) -> Any:
    """Recursively convert ``obj`` into JSON-native types.

    Publish-side sanitiser: event publishers call this so subscribers
    (``to_sse``, the Telegram streamer, replay buffers) never see a
    live domain object.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return _json_default(obj)


@dataclass(frozen=True)
class SessionEvent:
    """One event in a session's SSE stream."""

    kind: str
    data: dict[str, Any]

    def to_sse(self) -> bytes:
        """Format as a single SSE `event:` + `data:` block.

        Trailing blank line is mandatory — without it the browser
        buffers the chunk waiting for message terminator. Never
        raises: an unserializable frame is logged and replaced by a
        placeholder so one bad event can't take the channel down.
        """
        try:
            payload = json.dumps(self.data, ensure_ascii=False, default=_json_default)
        except (TypeError, ValueError):  # circular refs etc.
            log.exception(
                "session event %s failed to serialize; emitting placeholder", self.kind
            )
            payload = json.dumps({"type": "unserializable_event", "kind": self.kind})
        return f"event: {self.kind}\ndata: {payload}\n\n".encode()
