"""Compatibility shim — delegates to :mod:`loom.errors`.

The classifier used to live here; it now lives in Loom so every Loom
consumer gets the same taxonomy. This module re-exports the same public
API so existing Nexus callers (``agent/loop.py``, ``server/app.py``)
import unchanged.

Nexus-specific additions live here too — e.g. budget/quota detection
that Loom's generic classifier doesn't cover, and rate-limit reset-time
extraction for friendly user-facing countdowns.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from loom.errors import (
    ClassifiedError,
    FailoverReason,
    RecoveryAction,
    classify_api_error,
)

__all__ = [
    "ClassifiedError",
    "FailoverReason",
    "RecoveryAction",
    "classify_api_error",
    "is_budget_exceeded",
    "budget_exceeded_detail",
    "extract_rate_limit_reset",
    "classify_with_reset",
    "friendly_error_text",
    "format_cooldown_notice",
]


_BUDGET_CODES = frozenset({
    "budget_exceeded",
    "budget_exhausted",
    "spending_limit_reached",
    "usage_cap_reached",
    "monthly_limit_exceeded",
})

_BUDGET_MESSAGES = (
    "budget has been exceeded",
    "budget has been exhausted",
    "spending limit",
    "usage limit reached",
    "credit balance",
    "insufficient credits",
    "quota has been exceeded",
    "monthly spending",
)

# Matches "reset at YYYY-MM-DD HH:MM:SS" or "reset at YYYY-MM-DDTHH:MM:SS"
_RESET_AT_PATTERN = re.compile(
    r"reset\s+at\s+(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2})",
    re.IGNORECASE,
)


def _error_body(exc: BaseException) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        import json
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def is_budget_exceeded(exc: BaseException) -> bool:
    body = _error_body(exc)
    if not body:
        return False
    err = body.get("error", {})
    if isinstance(err, dict):
        code = str(err.get("code") or err.get("type") or "").lower()
        if code in _BUDGET_CODES:
            return True
        msg = str(err.get("message") or "").lower()
        if any(p in msg for p in _BUDGET_MESSAGES):
            return True
    return False


def budget_exceeded_detail(exc: BaseException) -> str | None:
    body = _error_body(exc)
    if not body:
        return None
    err = body.get("error", {})
    if isinstance(err, dict):
        msg = err.get("message", "")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:500]
    return None


def extract_rate_limit_reset(exc: BaseException) -> datetime | None:
    """Extract the rate limit reset time from an exception's error message.

    Looks for patterns like "Your limit will reset at 2026-10-15 23:56:50"
    in the error message. Returns a timezone-aware UTC datetime if found.
    """
    body = _error_body(exc)
    if not body:
        return None
    err = body.get("error", {})
    if isinstance(err, dict):
        msg = str(err.get("message") or "")
    else:
        msg = str(body.get("message") or "")
    if not msg:
        return None

    match = _RESET_AT_PATTERN.search(msg)
    if not match:
        return None

    date_str, time_str = match.groups()
    try:
        # Parse as UTC since the provider's reset time is typically in UTC
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def classify_with_reset(
    error: BaseException,
    *,
    provider: str = "",
    model: str = "",
    approx_tokens: int = 0,
    context_length: int = 200_000,
    num_messages: int = 0,
) -> ClassifiedError:
    """Classify an API error and attach rate-limit reset time if available.

    This wraps Loom's ``classify_api_error`` and adds a ``rate_limit_reset``
    field to ``error_context`` when the error is a rate limit with a
    parseable reset timestamp in the message.
    """
    classified = classify_api_error(
        error,
        provider=provider,
        model=model,
        approx_tokens=approx_tokens,
        context_length=context_length,
        num_messages=num_messages,
    )

    if classified.reason == FailoverReason.RATE_LIMIT:
        reset_dt = extract_rate_limit_reset(error)
        if reset_dt:
            # Store as ISO string for JSON serialization
            classified.error_context["rate_limit_reset"] = reset_dt.isoformat()

    return classified


# ── User-facing error formatting ────────────────────────────────────────────
# Server-side twin of the web's ``prettifyStreamError`` (types/chat.ts) so
# non-HTTP surfaces (Telegram bubbles) get the same quality of message
# instead of raw ``HTTP 429: {"error":{...}}`` payloads.

_REASON_BANNERS: dict[str, str] = {
    "rate_limit": "Provider rate limit hit. Wait for the cooldown, or switch to a different model.",
    "auth": "Authentication failed — check the API key or re-run the provider wizard.",
    "auth_permanent": "Authentication failed — check the API key or re-run the provider wizard.",
    "billing": "Your API budget has been exceeded. Top up your credits or switch providers to continue.",
    "budget_exceeded": "Your API budget has been exceeded. Top up your credits or switch providers to continue.",
    "bad_request": "Provider rejected the request — usually a model name, parameter, or message-shape issue.",
    "format_error": "Provider rejected the request — usually a model name, parameter, or message-shape issue.",
    "transport": "Couldn't reach the provider — check your network or the base URL.",
    "timeout": "Provider took too long to respond. Try again, or reduce context size.",
    "upstream_timeout": "Provider took too long to respond. Try again, or reduce context size.",
    "context_overflow": (
        "Conversation is too long for the model's context window. "
        "Compact history or start a new session."
    ),
    "payload_too_large": (
        "This message is too large to send. Try shortening it or compacting the conversation first."
    ),
    "message_too_large": (
        "This message is too large to send. Try shortening it or compacting the conversation first."
    ),
    "model_not_found": "Model not found or unavailable — check the model name or switch models.",
    "overloaded": "Provider is overloaded right now — try again in a minute.",
    "server_error": "Provider returned a server error — try again in a minute.",
    "empty_response": "The model returned an empty response. Sometimes a retry helps.",
    "length": "The response was cut off because it hit the model's output limit.",
    "iteration_limit": "Agent stopped after hitting the per-turn iteration limit.",
    "llm_error": "Provider error.",
    "hook_error": "Pre-call hook failed before the model was invoked.",
    "unknown": "Something went wrong.",
}

# ``HTTP 429: {json}`` / ``Error code: 429 - {json}`` prefixes — the summary
# prepend (``user_facing_summary (HTTP ...)``) means the prefix may not sit at
# the start of the string, so search anywhere and take the payload after it.
_PAYLOAD_PREFIX = re.compile(r"(?:HTTP\s+\d+\s*:|Error\s+code:\s*\d+\s*-)\s*", re.IGNORECASE)
_B_REPR_WRAPPER = re.compile(r"^b(['\"])(.*)\1$", re.DOTALL)
_INNER_MESSAGE_KEY = re.compile(r"['\"]message['\"]\s*:\s*(['\"])(.*?)\1", re.DOTALL)


def _extract_inner_message(body: str) -> str:
    """Lift the upstream ``error.message`` out of a JSON-ish error body.

    Three passes, mirroring the web client: strict JSON parse, a
    Python-repr → JSON conversion, then a tolerant regex grab.
    """
    import json as _json

    stripped = body.strip()
    m = _B_REPR_WRAPPER.match(stripped)
    if m:
        stripped = m.group(2)

    # Pass 1: strict JSON.
    try:
        parsed = _json.loads(stripped)
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
            msg = parsed.get("message") or parsed.get("detail")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
    except (ValueError, TypeError):
        pass

    # Pass 2: Python-repr → JSON (single quotes, None/True/False literals).
    try:
        as_json = (
            stripped.replace("None", "null").replace("True", "true").replace("False", "false")
        )
        as_json = as_json.replace("'", '"')
        parsed = _json.loads(as_json)
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                if isinstance(msg, str) and msg.strip():
                    return msg.strip()
    except (ValueError, TypeError):
        pass

    # Pass 3: regex grab of the ``message`` value.
    m = _INNER_MESSAGE_KEY.search(stripped)
    if m and m.group(2).strip():
        return m.group(2).replace('\\"', '"').replace("\\'", "'").strip()
    return ""


def _is_substantive(s: str) -> bool:
    """≥20 chars and not a placeholder like ``Error`` — worth showing verbatim."""
    t = s.strip()
    return len(t) >= 20 and t.lower() != "error"


def _user_tz() -> Any:
    """The configured timezone (``[location].timezone``), else the host's."""
    try:
        from .location import get_location

        tz_name = get_location().timezone
        if tz_name:
            from zoneinfo import ZoneInfo

            return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — best-effort
        pass
    return datetime.now().astimezone().tzinfo


def _humanize_duration(secs: float) -> str:
    s = max(0, int(secs))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"0:{sec:02d}"


def _format_reset_window(reset_iso: str) -> str | None:
    """Render a reset ISO timestamp as ``resets at 20:56 (in 11h 59m)`` local time."""
    try:
        reset = datetime.fromisoformat(reset_iso)
    except (ValueError, TypeError):
        return None
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=timezone.utc)
    tz = _user_tz()
    try:
        reset_local = reset.astimezone(tz)
    except Exception:  # noqa: BLE001 — bad tz config
        reset_local = reset
    now = datetime.now(timezone.utc)
    remaining = (reset - now).total_seconds()
    when = reset_local.strftime("%H:%M")
    today_local = datetime.now(tz).date() if tz else None
    if today_local is not None and reset_local.date() != today_local:
        when = reset_local.strftime("%a %d %b, %H:%M")
    return f"resets at {when} (in {_humanize_duration(remaining)})"


def friendly_error_text(
    detail: str,
    reason: str | None,
    rate_limit_reset: str | None = None,
) -> str:
    """Turn a raw provider error string into a user-facing sentence.

    Priority: rate-limit reset window → upstream ``error.message`` lifted
    from the JSON payload → a per-reason banner → the raw detail.
    """
    # 1. Rate limit with a parseable reset — the richest, most actionable text.
    if rate_limit_reset:
        window = _format_reset_window(rate_limit_reset)
        if window:
            return f"Rate limit reached — {window}. Your message is saved; try again after the cooldown."

    # 2. Lift the upstream's actual error.message from the payload.
    if detail:
        m = _PAYLOAD_PREFIX.search(detail)
        body = detail[m.end():] if m else ""
        if body:
            inner = _extract_inner_message(body)
            if _is_substantive(inner):
                # A rate-limit message often embeds the reset timestamp —
                # convert it into the local-time window instead of showing
                # the raw UTC sentence.
                if reason == "rate_limit":
                    rm = _RESET_AT_PATTERN.search(inner)
                    if rm:
                        try:
                            dt = datetime.strptime(
                                f"{rm.group(1)} {rm.group(2)}", "%Y-%m-%d %H:%M:%S"
                            ).replace(tzinfo=timezone.utc)
                            window = _format_reset_window(dt.isoformat())
                            if window:
                                return (
                                    f"Rate limit reached — {window}. "
                                    "Your message is saved; try again after the cooldown."
                                )
                        except ValueError:
                            pass
                return inner

    # 3. Per-reason banner (rate limits without a reset time land here).
    if reason and reason in _REASON_BANNERS:
        return _REASON_BANNERS[reason]

    if detail and detail.strip():
        return detail.strip()
    return _REASON_BANNERS["unknown"]


def format_cooldown_notice(retry_after_iso: str) -> str:
    """The ⏳ bubble for a ``paused_for_cooldown`` event (Telegram)."""
    window = _format_reset_window(retry_after_iso) if retry_after_iso else None
    if window:
        return f"⏳ Rate limit reached — the quota {window}. Your turn is paused; try again after that."
    return "⏳ Rate limit reached — the turn is paused for a cooldown. Try again in a few minutes."
