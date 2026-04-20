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
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionEvent:
    """One event in a session's SSE stream."""

    kind: str
    data: dict[str, Any]

    def to_sse(self) -> bytes:
        """Format as a single SSE `event:` + `data:` block.

        Trailing blank line is mandatory — without it the browser
        buffers the chunk waiting for message terminator.
        """
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.kind}\ndata: {payload}\n\n".encode()
