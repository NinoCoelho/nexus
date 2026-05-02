"""NOTIFY_USER_TOOL ToolSpec definition.

Lets the agent push a quick status update to the user mid-turn — e.g.
"hold on, this might take a minute, I'm checking three sources" before
launching a slow research call. The handler routes the message to:

* **audio** when the originating message was voice (so the user, who
  dictated, gets a spoken update without looking at the screen);
* **a toast** in every case, so the user sees the update even if they've
  switched to a different tab or session.

The tool is fire-and-forget — the agent gets a tiny ack back and keeps
working. It is NOT a way to ask questions (use ``ask_user`` for that).
"""

from __future__ import annotations

from .llm import ToolSpec

NOTIFY_USER_TOOL = ToolSpec(
    name="notify_user",
    description=(
        "Send a brief status update to the user without pausing the "
        "agent. Use this when you're about to start a long-running step "
        "(web research, big data ops, multi-tool plans) OR mid-flight "
        "to reassure the user something is still happening. The message "
        "is shown as a toast in the UI; if the user dictated their "
        "request by voice, the message is also spoken aloud through TTS. "
        "Keep messages short (under 20 words) and casual — they're "
        "ambient signals, not formal announcements. Do NOT use this "
        "tool to ask questions (use ask_user) or to deliver final "
        "results (those go in your reply). Returns immediately."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": (
                    "The status update to surface. Plain language in the "
                    "user's own language. Examples: \"Looking that up — "
                    "this might take a minute.\" / \"Tô buscando, vai "
                    "demorar uns instantes.\" / \"Already pulled the "
                    "headlines, now drafting the summary.\""
                ),
            },
        },
        "required": ["message"],
    },
)
