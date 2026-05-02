"""NOTIFY_USER tool — fire-and-forget status update from the agent.

Routes the agent-supplied message through the voice_ack pipeline:

* If the originating turn was a voice message, the message is also
  synthesized via Piper so the user hears it.
* In every case the event is published on the per-session pub/sub bus,
  which the UI surfaces as a toast (visible across views/sessions).

The handler returns immediately — the agent doesn't wait on TTS / publish.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .context import CURRENT_SESSION_ID
from .notify_user_schema import NOTIFY_USER_TOOL  # noqa: F401 — re-exported

log = logging.getLogger(__name__)


class NotifyUserHandler:
    """Wires the notify_user tool into the live SessionStore + voice_ack.

    The store is injected at server wire time. The handler fishes the
    current ``session_id`` out of the contextvar and the original
    ``input_mode`` out of a small per-session dict the chat_stream route
    populates on each turn.
    """

    def __init__(self, *, session_store: Any = None) -> None:
        self._sessions = session_store

    def set_session_store(self, store: Any) -> None:
        self._sessions = store

    async def invoke(self, args: dict[str, Any]) -> str:
        message = (args.get("message") or "").strip()
        if not message:
            return json.dumps({"ok": False, "error": "message required"})
        store = self._sessions
        if store is None:
            log.warning("[notify_user] no session_store wired — dropping message")
            return json.dumps({"ok": False, "error": "session store not wired"})

        session_id = CURRENT_SESSION_ID.get(None)
        if not session_id:
            log.warning("[notify_user] no current session — dropping message")
            return json.dumps({"ok": False, "error": "no current session"})

        # input_mode for the active turn lives in store._latest_input_mode
        # (set by chat_stream.py on every POST). Voice → speak the
        # message; text → toast only.
        #
        # Sessions started via vault_dispatch / kanban-card spawn don't
        # go through chat_stream, so the per-session map has no entry.
        # Fall back to the LAST global input_mode the daemon saw — if the
        # user has been doing voice today, dispatch-spawned sessions also
        # speak; if they've been typing, they stay silent.
        input_mode_map = getattr(store, "_latest_input_mode", None)
        session_known = (
            isinstance(input_mode_map, dict) and session_id in input_mode_map
        )
        if session_known:
            input_mode = input_mode_map[session_id]
            source = "session"
        else:
            input_mode = getattr(store, "_last_global_input_mode", None) or "text"
            source = "global-fallback" if input_mode != "text" else "default"

        # WARNING-level so it survives the daemon's default log filter and
        # the user can grep ``~/.nexus/nexus-daemon.log`` to verify the
        # detected mode when "toast appeared but no audio" happens.
        log.warning(
            "[notify_user] invoke sess=%s input_mode=%s source=%s msg=%r",
            session_id, input_mode, source, message[:80],
        )

        # Fire-and-forget: don't keep the agent waiting on Piper synth.
        # The publish itself is fast; TTS adds 100-500ms which we don't
        # want to block tool dispatch on.
        from ..voice_ack import emit_user_notification
        asyncio.create_task(emit_user_notification(
            store=store,
            session_id=session_id,
            message=message,
            speak=(input_mode == "voice"),
        ))
        return json.dumps({"ok": True})


__all__ = ["NotifyUserHandler", "NOTIFY_USER_TOOL"]
