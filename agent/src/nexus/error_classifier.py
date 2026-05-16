"""Compatibility shim — delegates to :mod:`loom.errors`.

The classifier used to live here; it now lives in Loom so every Loom
consumer gets the same taxonomy. This module re-exports the same public
API so existing Nexus callers (``agent/loop.py``, ``server/app.py``)
import unchanged.

Nexus-specific additions live here too — e.g. budget/quota detection
that Loom's generic classifier doesn't cover.
"""

from __future__ import annotations

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
