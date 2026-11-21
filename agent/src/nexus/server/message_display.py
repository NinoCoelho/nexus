"""Display-time cleaning for user messages.

The Chrome side panel prepends a ``<context>...</context>`` pointer block
to every message it sends (URL/title/excerpt + standing instructions).
That block is **persisted as part of the message content** because the
LLM needs it on every subsequent turn — but it must never be shown raw
in a chat transcript. The web UI strips it client-side
(``ui/src/contextPreamble.ts``); every other surface (iOS, the panel on
history reload, the Telegram echo of web/panel turns) gets the cleaned
text from the server instead, so this module is the single server-side
source of truth.

Only display paths may use it — never the agent's history, compaction,
recall or summarization (they must keep the full pointer).
"""

from __future__ import annotations

import re

# Mirrors the web client's regex: a leading <context>…</context> block,
# tolerant to leading blanks, consumed with its trailing blank lines.
_CONTEXT_BLOCK_RE = re.compile(
    r"^[ \t]*<context>[\s\S]*?</context>[ \t]*(?:\n+|$)", re.IGNORECASE
)
# Synthetic team round-pass marker (agent/bots.py TEAM_MARKER) — the
# server queues "<!-- nx:team -->@handle" to hand the turn to the next
# bot in a group chat. In team sessions it PRECEDES the user text, which
# also breaks leading-<context> detection — strip it first.
_TEAM_MARKER_RE = re.compile(r"^[ \t]*<!-- nx:team -->[ \t]*(@[^\s]+)?[ \t]*(?:\n+|$)")
_URL_LINE_RE = re.compile(r"^\s*url:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_TITLE_LINE_RE = re.compile(r"^\s*title:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def strip_team_pass_marker(text: str) -> tuple[str, str | None]:
    """Remove a leading team-pass marker; return ``(clean, handle)``.

    ``handle`` is None when the message carries no marker.
    """
    if not text or not text.startswith("<!--"):
        return text, None
    match = _TEAM_MARKER_RE.match(text)
    if match is None:
        return text, None
    return text[match.end():].lstrip("\n"), match.group(1)


def display_user_text(text: str) -> str:
    """Display form of a user message: marker + context preamble gone.

    Order matters — in team sessions the pass marker precedes the
    ``<context>`` block, so stripping the context first would leave the
    block non-leading and unmatched. When nothing but the marker
    remains (a pure pass), the handle stays visible instead of an
    empty bubble.
    """
    stripped, handle = strip_team_pass_marker(text)
    stripped = strip_context_preamble(stripped)
    if not stripped and handle:
        return handle
    return stripped


def display_fields(text: str) -> tuple[str, str | None]:
    """Display text + structured team-pass handle for event payloads.

    Lets live-event consumers (the web chip) keep rendering the pass
    without parsing the raw marker out of message content.
    """
    stripped, handle = strip_team_pass_marker(text)
    stripped = strip_context_preamble(stripped)
    if not stripped and handle:
        return handle, handle
    return stripped, handle


def strip_context_preamble(text: str) -> str:
    """Remove a leading ``<context>`` block from a message for display.

    Cheap no-op for anything that doesn't carry the marker. For team
    messages (marker + context) use :func:`display_user_text`, which
    strips in the right order.
    """
    if not text or "<context>" not in text.lower():
        return text
    return _CONTEXT_BLOCK_RE.sub("", text).lstrip("\n")


def context_preamble_meta(text: str) -> dict[str, str] | None:
    """Extract ``{"url", "title"}`` from a leading context block.

    Returns None when there is no block or nothing useful inside —
    callers use it to offer a "Chrome tab: <title>" affordance instead
    of the raw pointer.
    """
    if not text:
        return None
    match = _CONTEXT_BLOCK_RE.match(text)
    if match is None:
        return None
    block = match.group(0)
    meta: dict[str, str] = {}
    url = _URL_LINE_RE.search(block)
    title = _TITLE_LINE_RE.search(block)
    if url is not None:
        value = url.group(1).strip()
        if value and value != "(unknown)":
            meta["url"] = value
    if title is not None:
        value = title.group(1).strip()
        if value and value != "(untitled)":
            meta["title"] = value
    return meta or None
