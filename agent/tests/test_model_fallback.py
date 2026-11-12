"""Dead-model fallback tests — the turn must not block on HITL.

Background: when ``default_model`` (or a pinned per-request model) points at
a model the provider no longer serves (removed from the catalog → upstream
404 / "model does not exist"), the turn used to die with a terminal error
event and the UI parked the message behind a "How do you want to proceed?"
banner until the user intervened. The glm-4.7-flash-mlx removal produced
15+ such stuck turns.

The contract now (one fallback attempt per turn, never for transient
errors):

  1. A model-not-found failure switches to a valid model (the configured
     ``default_model`` when it isn't the one that failed, else the first
     available) and the turn CONTINUES on it.
  2. The user is notified non-blockingly: a ``reconnecting`` SSE event with
     ``reason: model_fallback:<new>`` carrying a short human-readable
     ``notice`` — the same channel the server already uses for auto-retry
     hints.
  3. The ORIGINAL failure is still recorded in ``llm_errors``.
  4. If the fallback model also fails, the normal terminal-error path runs
     (no infinite loop).
  5. Transient errors (429/500/timeout) never trigger a model switch.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nexus.agent.llm import (
    ChatResponse,
    LLMProvider,
    StopReason,
    StreamEvent,
)
from nexus.agent.loop import Agent
from nexus.skills.registry import SkillRegistry


class _NoopProvider(LLMProvider):
    async def chat(self, messages, *, tools=None, model=None, max_tokens=None) -> ChatResponse:
        return ChatResponse(content="", stop_reason=StopReason.STOP)

    async def chat_stream(
        self, messages, *, tools=None, model=None, max_tokens=None,
    ) -> AsyncIterator[StreamEvent]:
        if False:
            yield {}  # pragma: no cover

    async def aclose(self) -> None:
        pass


class _FakeProviderRegistry:
    def __init__(self, models: list[str]) -> None:
        self._models = models

    def available_model_ids(self) -> list[str]:
        return list(self._models)


class _FakeSessionStore:
    """Captures log_error calls (llm_errors audit trail)."""

    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []

    def log_error(self, session_id, error_type, *, message=None, status_code=None,
                  provider=None, model=None, retryable=None, retry_attempt=None,
                  tokens_in=None, context_window=None, **_kw) -> None:
        self.errors.append({
            "session_id": session_id,
            "error_type": error_type,
            "message": message,
            "model": model,
            "retryable": retryable,
        })


def _build_agent(
    tmp_path: Path,
    available: list[str],
    default_model: str,
) -> tuple[Agent, _FakeSessionStore]:
    cfg = SimpleNamespace(
        agent=SimpleNamespace(
            default_model=default_model,
            tool_budget_tokens=0,
            session_tool_budget_tokens=0,
            auto_compact_threshold_pct=0,
            default_max_output_tokens=0,
            max_iterations=None,
        ),
        models=[],
        scrape=SimpleNamespace(max_scrape_calls=0),
        search=None,
        browser=None,
        ui=None,
    )
    store = _FakeSessionStore()
    agent = Agent(
        provider=_NoopProvider(),
        registry=SkillRegistry(tmp_path / "skills"),
        provider_registry=_FakeProviderRegistry(available),
        nexus_cfg=cfg,
    )
    agent._sessions = store  # type: ignore[attr-defined]
    return agent, store


# ── 1+2+3: dead model → fallback completes the turn ───────────────────────


async def test_dead_model_falls_back_and_completes_turn(
    tmp_path: Path, monkeypatch,
) -> None:
    """A 404 'model does not exist' on the pinned model must switch to the
    configured default, finish the turn on it, emit a non-blocking notice,
    and record the original failure in llm_errors — no HITL, no terminal
    error."""
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _real_sleep(0))

    agent, store = _build_agent(
        tmp_path,
        available=["openai/gpt-5-mini", "anthropic/claude-haiku"],
        default_model="openai/gpt-5-mini",
    )

    invocations: list[list[Any]] = []
    models_per_call: list[str | None] = []

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        invocations.append([m.model_dump() for m in messages])
        models_per_call.append(model_id)
        if len(invocations) == 1:
            # First call: the pinned model is dead — provider 404s.
            yield {
                "type": "error",
                "message": "model 'glm-4.7-flash-mlx' does not exist",
                "reason": "model_not_found",
                "retryable": False,
                "status_code": 404,
            }
            yield {
                "type": "done",
                "stop_reason": None,
                "context": {"partial": False},
                "model": "glm-4.7-flash-mlx",
                "iterations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_calls": 0,
            }
            return

        # Second call (after fallback): clean completion.
        yield {"type": "content_delta", "delta": "hello from the fallback"}
        yield {
            "type": "done",
            "stop_reason": "stop",
            "context": {"messages": [m for m in invocations[-1]]},
            "model": "openai/gpt-5-mini",
            "iterations": 1,
            "input_tokens": 10,
            "output_tokens": 5,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events: list[dict[str, Any]] = []
    async for ev in agent.run_turn_stream(
        "summarize the deck",
        history=None,
        context=None,
        session_id="s_fallback",
        model_id="zai/glm-4.7-flash-mlx",
    ):
        events.append(ev)

    # 1. The turn continued on the fallback model (exactly 2 loom calls,
    #    the second carrying the default model id).
    assert len(invocations) == 2
    assert models_per_call == ["zai/glm-4.7-flash-mlx", "openai/gpt-5-mini"]

    # 2. Non-blocking notice via the existing reconnecting channel.
    fallback_events = [e for e in events if e.get("type") == "reconnecting"
                       and str(e.get("reason", "")).startswith("model_fallback:")]
    assert len(fallback_events) == 1
    fb = fallback_events[0]
    assert fb["fallback_model"] == "openai/gpt-5-mini"
    assert fb["failed_model"] == "zai/glm-4.7-flash-mlx"
    assert "unavailable" in fb["notice"]
    assert "openai/gpt-5-mini" in fb["notice"]

    # 3. The original failure is recorded in llm_errors — not silenced.
    assert len(store.errors) == 1
    err = store.errors[0]
    assert err["error_type"] == "model_not_found"
    assert err["model"] == "zai/glm-4.7-flash-mlx"
    assert "does not exist" in (err["message"] or "")

    # No terminal error surfaced (that's the blocking HITL banner).
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events == [], (
        f"dead model must fall back, not surface a blocking error: {error_events}"
    )

    # The turn completed normally.
    done = [e for e in events if e.get("type") == "done"]
    assert done and done[0]["reply"] == "hello from the fallback"
    assert done[0]["usage"]["model"] == "openai/gpt-5-mini"


async def test_message_pattern_falls_back_when_reason_is_generic(
    tmp_path: Path, monkeypatch,
) -> None:
    """Some providers answer a dead model with a non-retryable 400 whose
    message matches (e.g. "unknown model") without loom classifying it as
    model_not_found — the message patterns must still trigger fallback."""
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _real_sleep(0))

    agent, _store = _build_agent(
        tmp_path,
        available=["openai/gpt-5-mini", "anthropic/claude-haiku"],
        default_model="openai/gpt-5-mini",
    )

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        if model_id != "openai/gpt-5-mini":
            yield {
                "type": "error",
                "message": "400 Bad Request: unknown model",
                "reason": "format_error",
                "retryable": False,
                "status_code": 400,
            }
            yield {
                "type": "done",
                "stop_reason": None,
                "context": {"partial": False},
                "model": model_id,
                "iterations": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_calls": 0,
            }
            return
        yield {"type": "content_delta", "delta": "ok"}
        yield {
            "type": "done",
            "stop_reason": "stop",
            "context": {"messages": [m.model_dump() for m in messages]},
            "model": "openai/gpt-5-mini",
            "iterations": 1,
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events = []
    async for ev in agent.run_turn_stream(
        "hi",
        history=None,
        context=None,
        session_id="s_pattern",
        model_id="zai/glm-4.7-flash-mlx",
    ):
        events.append(ev)

    fallback = [e for e in events if str(e.get("reason", "")).startswith("model_fallback:")]
    assert fallback, f"expected a model_fallback reconnecting event, got {events}"
    assert fallback[0]["fallback_model"] == "openai/gpt-5-mini"
    done = [e for e in events if e.get("type") == "done"]
    assert done and done[0]["reply"] == "ok"


# ── 4: fallback model dead too → terminal error, no loop ─────────────────


async def test_fallback_failure_surfaces_error_without_loop(
    tmp_path: Path, monkeypatch,
) -> None:
    """If the fallback model ALSO fails with model-not-found, exactly one
    fallback attempt happens and the normal terminal-error path runs."""
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _real_sleep(0))

    agent, store = _build_agent(
        tmp_path,
        available=["openai/gpt-5-mini", "anthropic/claude-haiku"],
        default_model="openai/gpt-5-mini",
    )

    invocations: list[str | None] = []

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        invocations.append(model_id)
        # Every model 404s — the fallback can't save this turn.
        yield {
            "type": "error",
            "message": "model does not exist",
            "reason": "model_not_found",
            "retryable": False,
            "status_code": 404,
        }
        yield {
            "type": "done",
            "stop_reason": None,
            "context": {"partial": False},
            "model": model_id,
            "iterations": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events = []
    async for ev in agent.run_turn_stream(
        "hi",
        history=None,
        context=None,
        session_id="s_double_fail",
        model_id="zai/glm-4.7-flash-mlx",
    ):
        events.append(ev)

    # Exactly one fallback attempt (2 calls total), then the error surfaces.
    assert invocations == ["zai/glm-4.7-flash-mlx", "openai/gpt-5-mini"]
    error_events = [e for e in events if e.get("type") == "error"]
    assert error_events, "second failure must surface as a normal terminal error"

    # Both original failures are recorded; no third attempt was made.
    assert len(store.errors) == 2
    assert [e["model"] for e in store.errors] == [
        "zai/glm-4.7-flash-mlx", "openai/gpt-5-mini",
    ]

    done = [e for e in events if e.get("type") == "done"]
    assert done, "terminal done still emitted"


# ── 5: transient errors never switch models ──────────────────────────────


async def test_rate_limit_never_falls_back(tmp_path: Path, monkeypatch) -> None:
    """A 429 whose message mentions capacity must NOT trigger a model
    switch — rate limits keep the existing retry/cooldown behaviour."""
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _real_sleep(0))

    agent, _store = _build_agent(
        tmp_path,
        available=["openai/gpt-5-mini", "anthropic/claude-haiku"],
        default_model="openai/gpt-5-mini",
    )

    invocations: list[str | None] = []

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        invocations.append(model_id)
        yield {"type": "content_delta", "delta": "partial "}
        yield {
            "type": "error",
            "message": "no capacity for model right now",
            "reason": "rate_limit",
            "retryable": True,
            "status_code": 429,
        }
        yield {
            "type": "done",
            "stop_reason": None,
            "context": {"partial": True},
            "model": model_id,
            "iterations": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events = []
    async for ev in agent.run_turn_stream(
        "hi",
        history=None,
        context=None,
        session_id="s_429",
        model_id="openai/gpt-5-mini",
    ):
        events.append(ev)

    # No fallback: one loom call, the same model, error surfaces as usual.
    assert invocations == ["openai/gpt-5-mini"]
    fallback = [e for e in events if str(e.get("reason", "")).startswith("model_fallback:")]
    assert fallback == []
    assert any(e.get("type") == "error" for e in events)


async def test_timeout_never_falls_back(tmp_path: Path, monkeypatch) -> None:
    """Network timeouts keep the retry path — no model switch."""
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_k: _real_sleep(0))

    agent, _store = _build_agent(
        tmp_path,
        available=["openai/gpt-5-mini", "anthropic/claude-haiku"],
        default_model="openai/gpt-5-mini",
    )

    async def _fake_loom_stream(messages, *, model_id=None):  # type: ignore[no-untyped-def]
        yield {
            "type": "error",
            "message": "peer closed connection without sending complete message body",
            "reason": "timeout",
            "retryable": True,
            "status_code": None,
        }
        yield {
            "type": "done",
            "stop_reason": None,
            "context": {"partial": False},
            "model": model_id,
            "iterations": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
        }

    agent._loom.run_turn_stream = _fake_loom_stream  # type: ignore[attr-defined]

    events = []
    async for ev in agent.run_turn_stream(
        "hi",
        history=None,
        context=None,
        session_id="s_timeout",
        model_id="openai/gpt-5-mini",
    ):
        events.append(ev)

    fallback = [e for e in events if str(e.get("reason", "")).startswith("model_fallback:")]
    assert fallback == []
    # The normal clean-retry reconnecting hint fired instead.
    retry = [e for e in events if e.get("type") == "reconnecting"]
    assert retry and retry[0]["reason"] == "timeout"


# ── fallback selection preference ─────────────────────────────────────────


def test_select_fallback_prefers_default_model(tmp_path: Path) -> None:
    agent, _ = _build_agent(
        tmp_path,
        available=["anthropic/claude-haiku", "openai/gpt-5-mini"],
        default_model="openai/gpt-5-mini",
    )
    # Pinned model died → the configured default wins over "first available".
    assert agent._select_fallback_model("zai/glm-dead", "") == "openai/gpt-5-mini"


def test_select_fallback_when_default_is_the_dead_one(tmp_path: Path) -> None:
    agent, _ = _build_agent(
        tmp_path,
        available=["openai/gpt-5-mini", "anthropic/claude-haiku"],
        default_model="openai/gpt-5-mini",
    )
    # The default itself died → first available that isn't the default.
    assert agent._select_fallback_model(None, "") == "anthropic/claude-haiku"
    assert agent._select_fallback_model("openai/gpt-5-mini", "") == "anthropic/claude-haiku"


def test_select_fallback_none_when_nothing_available(tmp_path: Path) -> None:
    agent, _ = _build_agent(tmp_path, available=[], default_model="openai/gpt-5-mini")
    assert agent._select_fallback_model("openai/gpt-5-mini", "") is None
