"""Compatibility shim — delegates to :mod:`loom.errors`.

The classifier used to live here; it now lives in Loom so every Loom
consumer gets the same taxonomy. This module re-exports the same public
API so existing Nexus callers (``agent/loop.py``, ``server/app.py``)
import unchanged.
"""

from __future__ import annotations

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
]
