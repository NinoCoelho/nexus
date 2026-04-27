"""Slash-command handlers invoked from /chat/stream.

These bypass the agent loop entirely — they're deterministic operations on
the session that don't need an LLM round-trip. Each handler is an async
generator yielding SSE-formatted bytes the same way ``event_generator`` in
chat_stream.py does, and is responsible for writing the resulting history
back via the session store.

The ``SLASH_COMMANDS`` registry is the single source of truth: it drives
both server-side dispatch and the UI picker (``GET /commands``). To add a
new command, append a ``SlashCommand`` entry and wire its handler into
``dispatch``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Callable

from fastapi import APIRouter

from ...agent.llm import ChatMessage, Role
from ...agent.loop.compact import (
    DEFAULT_COMPACT_THRESHOLD_BYTES,
    compact_history,
)
from ..session_store import SessionStore

log = logging.getLogger(__name__)

router = APIRouter()

SlashHandler = Callable[..., AsyncIterator[str]]


@dataclass(frozen=True)
class SlashCommand:
    """Metadata + dispatch for one slash command.

    Surfaced verbatim to the UI picker via ``GET /commands``, so keep
    descriptions short (one line) and args_hint conventional (``<required>``,
    ``[optional]``).
    """
    name: str
    description: str
    args_hint: str = ""


SLASH_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "compact",
        "Shrink oversized tool results to free up the context window",
        "[aggressive]",
    ),
    SlashCommand(
        "clear",
        "Reset this session — wipes all messages from history",
    ),
    SlashCommand(
        "title",
        "Rename this session",
        "<new title>",
    ),
    SlashCommand(
        "usage",
        "Show token + tool-call totals for this session",
    ),
    SlashCommand(
        "help",
        "List available slash commands",
    ),
)
_BY_NAME: dict[str, SlashCommand] = {c.name: c for c in SLASH_COMMANDS}


def is_slash_command(message: str) -> str | None:
    """Return the canonical slash-command name if ``message`` starts with one,
    else None. Recognises the command only when it's the leading token —
    we deliberately don't try to detect commands mid-message because ``/``
    is too common in URLs and paths."""
    stripped = message.lstrip()
    if not stripped.startswith("/"):
        return None
    head = stripped[1:].split(None, 1)[0].lower() if len(stripped) > 1 else ""
    return head if head in _BY_NAME else None


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _format_int(n: int) -> str:
    return f"{n:,}".replace(",", "_")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _done_payload(session_id: str, reply: str, **extra) -> dict:
    payload = {
        "session_id": session_id,
        "reply": reply,
        "trace": [],
        "skills_touched": [],
        "iterations": 0,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
            "model": "",
        },
        "model": "",
    }
    payload.update(extra)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# /compact
# ─────────────────────────────────────────────────────────────────────────────

def _parse_compact_args(message: str) -> dict[str, int]:
    """Parse trailing args on ``/compact``.

    Recognised:
      - ``/compact`` → defaults
      - ``/compact aggressive`` → 8KB threshold (catches medium tool results)

    Anything else is ignored — unknown args don't fail the command.
    """
    parts = message.lstrip().split()
    if len(parts) >= 2 and parts[1].lower() == "aggressive":
        return {"threshold_bytes": 8 * 1024, "head_keep": 1024}
    return {"threshold_bytes": DEFAULT_COMPACT_THRESHOLD_BYTES, "head_keep": 2 * 1024}


async def handle_compact(
    *,
    store: SessionStore,
    session_id: str,
    pre_turn_history: list[ChatMessage],
    user_message: str,
) -> AsyncIterator[str]:
    args = _parse_compact_args(user_message)
    new_history, report = compact_history(pre_turn_history, **args)

    if report.compacted == 0:
        if report.skipped_already_compacted > 0:
            status = (
                f"Nothing new to compact — {report.skipped_already_compacted} "
                f"tool result(s) already compacted in earlier passes."
            )
        else:
            status = (
                f"No oversized tool results found (inspected "
                f"{report.inspected}, threshold "
                f"{_format_bytes(args['threshold_bytes'])}). Try "
                f"`/compact aggressive` for a smaller threshold."
            )
    else:
        status = (
            f"Compacted {report.compacted} tool result"
            f"{'s' if report.compacted != 1 else ''} — saved "
            f"{_format_bytes(report.saved_bytes)} "
            f"({_format_bytes(report.bytes_before)} → "
            f"{_format_bytes(report.bytes_after)}). "
            f"You can continue the conversation now."
        )

    final_messages = new_history + [
        ChatMessage(role=Role.USER, content=user_message),
        ChatMessage(role=Role.ASSISTANT, content=status),
    ]
    try:
        store.replace_history(session_id, final_messages)
    except Exception:  # noqa: BLE001
        log.exception("compact replace_history failed")

    yield _sse("delta", {"text": status})
    yield _sse("done", _done_payload(
        session_id, status,
        compact_report={
            "inspected": report.inspected,
            "compacted": report.compacted,
            "skipped_already_compacted": report.skipped_already_compacted,
            "bytes_before": report.bytes_before,
            "bytes_after": report.bytes_after,
            "saved_bytes": report.saved_bytes,
        },
    ))


# ─────────────────────────────────────────────────────────────────────────────
# /clear
# ─────────────────────────────────────────────────────────────────────────────

async def handle_clear(
    *,
    store: SessionStore,
    session_id: str,
    pre_turn_history: list[ChatMessage],
    user_message: str,
) -> AsyncIterator[str]:
    """Wipe session history. The ``/clear`` command and its confirmation are
    the only messages left — the user can immediately start a fresh
    conversation in the same session id."""
    wiped = len(pre_turn_history)
    status = (
        f"Cleared session — wiped {wiped} message{'s' if wiped != 1 else ''}. "
        f"Start a fresh conversation."
    )
    final_messages = [
        ChatMessage(role=Role.USER, content=user_message),
        ChatMessage(role=Role.ASSISTANT, content=status),
    ]
    try:
        store.replace_history(session_id, final_messages)
    except Exception:  # noqa: BLE001
        log.exception("clear replace_history failed")
    yield _sse("delta", {"text": status})
    yield _sse("done", _done_payload(session_id, status, cleared_messages=wiped))


# ─────────────────────────────────────────────────────────────────────────────
# /title <new>
# ─────────────────────────────────────────────────────────────────────────────

async def handle_title(
    *,
    store: SessionStore,
    session_id: str,
    pre_turn_history: list[ChatMessage],
    user_message: str,
) -> AsyncIterator[str]:
    """Rename the current session. Title is the rest of the line after
    ``/title``. Empty title → echo current title with usage hint."""
    parts = user_message.lstrip().split(None, 1)
    new_title = parts[1].strip() if len(parts) >= 2 else ""

    if not new_title:
        sess = store.get(session_id)
        current = sess.title if sess else "(unknown)"
        status = (
            f"Current title: **{current}**. "
            f"Usage: `/title <new title>` — sets a new session title."
        )
    else:
        try:
            store.rename(session_id, new_title)
            status = f"Session renamed to **{new_title}**."
        except Exception as exc:  # noqa: BLE001
            log.exception("title rename failed")
            status = f"Rename failed: {exc}"
            new_title = ""  # don't pretend it succeeded

    final_messages = list(pre_turn_history) + [
        ChatMessage(role=Role.USER, content=user_message),
        ChatMessage(role=Role.ASSISTANT, content=status),
    ]
    try:
        store.replace_history(session_id, final_messages)
    except Exception:  # noqa: BLE001
        log.exception("title replace_history failed")
    yield _sse("delta", {"text": status})
    yield _sse("done", _done_payload(
        session_id, status, new_title=new_title or None,
    ))


# ─────────────────────────────────────────────────────────────────────────────
# /usage
# ─────────────────────────────────────────────────────────────────────────────

async def handle_usage(
    *,
    store: SessionStore,
    session_id: str,
    pre_turn_history: list[ChatMessage],
    user_message: str,
) -> AsyncIterator[str]:
    """Stream a markdown summary of session-level token + tool counts."""
    row = store._loom._db.execute(
        "SELECT model, input_tokens, output_tokens, tool_call_count "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        status = "Session not found."
    else:
        model = row[0] or "(no model recorded)"
        in_tok = int(row[1] or 0)
        out_tok = int(row[2] or 0)
        tool_calls = int(row[3] or 0)
        status = (
            f"## Session usage\n\n"
            f"| | |\n"
            f"|---|---|\n"
            f"| Model | `{model}` |\n"
            f"| Input tokens | {_format_int(in_tok)} |\n"
            f"| Output tokens | {_format_int(out_tok)} |\n"
            f"| Tool calls | {_format_int(tool_calls)} |\n"
            f"| Messages in history | {_format_int(len(pre_turn_history))} |\n"
        )

    final_messages = list(pre_turn_history) + [
        ChatMessage(role=Role.USER, content=user_message),
        ChatMessage(role=Role.ASSISTANT, content=status),
    ]
    try:
        store.replace_history(session_id, final_messages)
    except Exception:  # noqa: BLE001
        log.exception("usage replace_history failed")
    yield _sse("delta", {"text": status})
    yield _sse("done", _done_payload(session_id, status))


# ─────────────────────────────────────────────────────────────────────────────
# /help
# ─────────────────────────────────────────────────────────────────────────────

async def handle_help(
    *,
    store: SessionStore,
    session_id: str,
    pre_turn_history: list[ChatMessage],
    user_message: str,
) -> AsyncIterator[str]:
    """Stream a markdown table of all registered commands."""
    rows = "\n".join(
        f"| `/{c.name}{(' ' + c.args_hint) if c.args_hint else ''}` | {c.description} |"
        for c in SLASH_COMMANDS
    )
    status = (
        "## Available slash commands\n\n"
        "| Command | Description |\n"
        "|---|---|\n"
        f"{rows}\n"
    )
    final_messages = list(pre_turn_history) + [
        ChatMessage(role=Role.USER, content=user_message),
        ChatMessage(role=Role.ASSISTANT, content=status),
    ]
    try:
        store.replace_history(session_id, final_messages)
    except Exception:  # noqa: BLE001
        log.exception("help replace_history failed")
    yield _sse("delta", {"text": status})
    yield _sse("done", _done_payload(session_id, status))


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────────────────

_DISPATCH: dict[str, SlashHandler] = {
    "compact": handle_compact,
    "clear": handle_clear,
    "title": handle_title,
    "usage": handle_usage,
    "help": handle_help,
}


def dispatch(name: str) -> SlashHandler | None:
    """Return the handler for ``name`` or None. Used by chat_stream.py."""
    return _DISPATCH.get(name)


# Safety: every registered command must have a handler. Caught at import time
# so a typo in the registry surfaces immediately, not at first user invocation.
_missing = [c.name for c in SLASH_COMMANDS if c.name not in _DISPATCH]
assert not _missing, f"slash commands missing handlers: {_missing}"


@router.get("/commands")
async def list_commands() -> list[dict]:
    """Return the slash-command registry for the UI picker.

    Loopback-only by routing convention (no proxy header check needed —
    this is just metadata). Static for the lifetime of the process, so
    callers can cache it.
    """
    return [asdict(c) for c in SLASH_COMMANDS]
