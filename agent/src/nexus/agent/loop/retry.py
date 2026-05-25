"""Retry logic for the agent streaming loop.

Encapsulates the three retry paths used when loom emits a retryable error
mid-stream:

1. **Clean retry** — no content has been streamed yet; silently restart.
2. **Mid-stream disconnect** — content already rendered in the UI;
   materialise the partial assistant, inject a continuation prompt, restart.
3. **Post-retry compaction** — all retries exhausted on a server/timeout
   error; compact working messages and retry once more with a smaller
   payload.

All three paths share a cleanup pattern (clear pending translator state,
restart the loom iterator) which is delegated to the caller.
"""

from __future__ import annotations

from typing import Any


class RetryManager:
    MAX_RETRIES: int = 3
    BACKOFFS: tuple[float, ...] = (2.0, 5.0, 12.0)
    MID_STREAM_SIGNALS: tuple[str, ...] = (
        "peer closed connection",
        "incomplete chunked read",
        "server disconnected",
        "connection reset by peer",
        "connection was closed",
        "unexpected eof",
    )

    def __init__(self) -> None:
        self._attempts: int = 0
        self.delta_emitted: bool = False
        self._post_compaction_done: bool = False

    @property
    def attempts(self) -> int:
        return self._attempts

    def get_backoff(self) -> float:
        return self.BACKOFFS[min(self._attempts, len(self.BACKOFFS) - 1)]

    def increment(self) -> None:
        self._attempts += 1

    def reset_iteration(self) -> None:
        self.delta_emitted = False

    def reset_all(self) -> None:
        self._attempts = 0
        self.delta_emitted = False

    def mark_post_compaction(self) -> None:
        self._post_compaction_done = True

    @property
    def post_compaction_done(self) -> bool:
        return self._post_compaction_done

    def should_clean_retry(self, ev: dict[str, Any]) -> bool:
        return (
            bool(ev.get("retryable", False))
            and not self.delta_emitted
            and self._attempts < self.MAX_RETRIES
        )

    def should_mid_stream_retry(self, ev: dict[str, Any]) -> bool:
        return (
            bool(ev.get("retryable", False))
            and self.delta_emitted
            and self._is_mid_stream_disconnect(ev)
            and self._attempts < self.MAX_RETRIES
        )

    def should_post_retry_compaction(self, ev: dict[str, Any]) -> bool:
        return (
            bool(ev.get("retryable", False))
            and not self.delta_emitted
            and self._attempts >= self.MAX_RETRIES
            and not self._post_compaction_done
            and ev.get("reason") in ("server_error", "timeout")
        )

    @classmethod
    def _is_mid_stream_disconnect(cls, ev: dict[str, Any]) -> bool:
        msg = (ev.get("message") or "").lower()
        return any(s in msg for s in cls.MID_STREAM_SIGNALS)
