"""HITL prompt routing across surfaces.

Decides which surface "owns" a session's HITL prompts so a question asked
during a turn pops up only in the chat/channel the turn originated from —
never duplicated everywhere:

- Telegram-bound sessions → the Telegram forwarder owns the prompt
  (``session_is_telegram_routed``; see ``telegram/hitl.py``).
- Chrome side-panel sessions → the panel owns the prompt. ``POST
  /chat/stream`` stamps brand-new sessions created by the panel with the
  ``sessions.context`` prefix ``CHROME_PANEL_CONTEXT`` (panel sends
  ``surface: "chrome-extension"``), so ownership survives daemon restarts
  without extra state. The web UI's global approval popup, the pending
  recovery listing, and web push / APNs all stay silent for these. The
  prompt is still recoverable where answerable: the panel re-renders it
  on reopen (``loadHistory`` → ``sess.pending``), the per-session events
  channel and the in-chat recovery work when the session is opened on
  the web, and the bell history row is still written (audit).

``suppress_web_prompt`` is the single predicate used by the global
notifications channel (``routes/notifications.py``) and the push fan-out
(``pubsub._on_global_hitl_event``). Best-effort like the Telegram check:
any failure reads as "not suppressed" so a prompt still surfaces
somewhere rather than vanishing.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ``sessions.context`` prefix stamped on sessions created by the Chrome
# side panel (chat_stream._SURFACE_CONTEXTS). Prefix match so the string
# can be extended later (e.g. with tab info) without breaking the check.
CHROME_PANEL_CONTEXT = "Chrome side panel"


def session_is_chrome_panel(session_id: str) -> bool:
    """True when ``session_id`` was created by the Chrome side panel.

    Reads ``sessions.context`` straight from the sessions DB (short-lived
    connection, same pattern as the Telegram binding check) — the row is
    the durable ownership record, so gating keeps working after restarts
    and for parked prompts re-published by the startup sweep.
    """
    try:
        import sqlite3

        from ..home import sessions_db

        conn = sqlite3.connect(str(sessions_db()))
        try:
            row = conn.execute(
                "SELECT context FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        finally:
            conn.close()
        context = (row[0] or "") if row else ""
        return context.startswith(CHROME_PANEL_CONTEXT)
    except Exception:
        log.debug("hitl_routing: chrome-panel check failed", exc_info=True)
        return False


def suppress_web_prompt(session_id: str) -> bool:
    """True when this session's HITL prompts belong to another surface.

    The web UI must not duplicate prompts that a different channel
    delivers: Telegram forwarder (binding) or Chrome side panel (context
    prefix). Used by the global notifications SSE, ``/notifications/pending``
    and the web push / APNs fan-out. The bell history is never suppressed.
    """
    # Chrome first — a single indexed SELECT is cheaper than the binding
    # lookup, and most prompts come from web/panel sessions.
    if session_is_chrome_panel(session_id):
        return True
    from ..telegram.bindings import session_is_telegram_routed

    return session_is_telegram_routed(session_id)
