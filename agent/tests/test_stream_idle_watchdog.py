"""Stream idle watchdog (LoomProviderAdapter).

Regression for the 2026-11-21 CFO incident: a local LM Studio request sat
silent for ~40 minutes (3 thinking deltas, then nothing) while the turn
froze — the only backstop was the provider's 600s httpx read timeout, and
that resets on every received byte, so a stream that talks once and then
stalls was effectively unbounded.
"""

from __future__ import annotations

import asyncio
import time

from nexus.agent._loom_bridge.adapter import (
    STREAM_IDLE_TIMEOUT_SECONDS,
    LoomProviderAdapter,
)
from nexus.agent.llm import LLMProvider, LLMTransportError


class _ScriptedProvider(LLMProvider):
    """Yields canned events with a controlled delay before each."""

    def __init__(self, script: list[tuple[float, dict]]):
        self._script = script

    async def chat(self, messages, *, tools=None, model=None, max_tokens=None):
        raise NotImplementedError

    async def chat_stream(self, messages, *, tools=None, model=None, max_tokens=None):
        for delay, ev in self._script:
            await asyncio.sleep(delay)
            yield ev


async def _collect(agen) -> tuple[list, Exception | None]:
    out: list = []
    error: Exception | None = None
    try:
        async for ev in agen:
            out.append(ev)
    except Exception as exc:  # noqa: BLE001 — the test asserts on the type
        error = exc
    return out, error


def test_stream_events_pass_through_untouched() -> None:
    provider = _ScriptedProvider([
        (0.0, {"type": "thinking_delta", "text": "hmm"}),
        (0.0, {"type": "delta", "text": "hello"}),
        (0.0, {"type": "finish", "finish_reason": "stop"}),
    ])
    adapter = LoomProviderAdapter(provider)  # type: ignore[arg-type]

    async def _run() -> tuple[list, Exception | None]:
        return await _collect(adapter.chat_stream(
            [], model=None,
        ))

    out, error = asyncio.run(_run())
    assert error is None
    # The delta content survives translation (ContentDeltaEvent for "hello").
    assert any(type(ev).__name__ == "ContentDeltaEvent" for ev in out)


def test_idle_stream_aborts_after_first_event() -> None:
    # Talks once (a real content delta — thinking deltas go to the sink, not
    # the stream), then goes silent far beyond the idle timeout.
    provider = _ScriptedProvider([
        (0.0, {"type": "delta", "text": "visible"}),
        (1.0, {"type": "delta", "text": "never arrives"}),
    ])
    adapter = LoomProviderAdapter(  # type: ignore[arg-type]
        provider, stream_idle_timeout=0.2,
    )

    async def _run() -> tuple[list, Exception | None]:
        start = time.monotonic()
        result = await _collect(adapter.chat_stream([], model=None))
        elapsed = time.monotonic() - start
        # Aborted at ~0.2s, NOT at the script's 1.0s mark — the watchdog
        # cancelled the pending event instead of waiting it out.
        assert elapsed < 0.9, f"watchdog took {elapsed:.2f}s"
        return result

    out, error = asyncio.run(_run())
    assert isinstance(error, LLMTransportError)
    assert "upstream hung" in str(error)
    assert len(out) >= 1  # the pre-silence delta was still delivered


def test_first_event_window_is_not_bounded() -> None:
    # Silence BEFORE the first event is the cold-load / buffered-thinking
    # window: the adapter leaves it to the provider's read timeout.
    provider = _ScriptedProvider([
        (0.5, {"type": "delta", "text": "late but alive"}),
    ])
    adapter = LoomProviderAdapter(  # type: ignore[arg-type]
        provider, stream_idle_timeout=0.2,
    )

    async def _run() -> tuple[list, Exception | None]:
        return await _collect(adapter.chat_stream([], model=None))

    out, error = asyncio.run(_run())
    assert error is None
    assert len(out) == 1


def test_default_timeout_constant_is_sane() -> None:
    # Generous enough for slow local hardware between tokens, well under the
    # provider's 600s read backstop.
    assert 60 <= STREAM_IDLE_TIMEOUT_SECONDS <= 300
