"""NexusCompactor model resolution + per-turn chosen-model isolation.

Regression for the 2026-11-21 incident: the builder constructed
``NexusCompactor(provider)`` with no model, so mid-turn compaction called
``provider.chat(model=None)`` on a provider built with ``model=""`` —
"No model specified" locally, compaction always degraded to ``hard_trim``
(159 messages elided from the coordinator master session). Separately, the
shared Agent's ``_chosen_model`` attribute let a newly launched session
hijack a concurrently running turn's model at the next ``choose_model``
evaluation (a glm turn silently switched to an LM Studio model mid-turn).
Also covers the compaction loop guard (same incident family): a turn whose
history re-grows past its budget after every force-trim must abort with a
visible ``context_overflow`` instead of thrashing hard_trim → re-read →
hard_trim until the iteration limit.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from nexus.agent.context import (
    current_chosen_model,
    reset_turn_compaction_rounds,
    set_chosen_model,
    turn_compaction_rounds,
)
from nexus.agent.llm import ChatMessage as NexusChatMessage
from nexus.agent.llm import Role
from nexus.agent.loop.compactor import (
    MAX_COMPACTION_ROUNDS_PER_TURN,
    CompactionLoopGuardError,
    NexusCompactor,
)

if TYPE_CHECKING:
    from loom.loop.compaction import CompactionRequest

_ONE_MSG = [NexusChatMessage(role=Role.USER, content="hello")]


def _compaction_request(attempt: int = 1) -> CompactionRequest:
    from loom.loop.compaction import CompactionRequest

    return CompactionRequest(
        messages=list(_ONE_MSG), estimated_tokens=1, context_window=1000,
        zone="orange", iteration=1, attempt=attempt,
    )


def _fake_report():
    return type("R", (), {
        "compact_report": type("C", (), {"compacted": 0, "saved_bytes": 0})(),
        "summarized": False, "hard_trimmed": False,
        "tokens_before": 0, "tokens_after": 0, "zone_after": "green",
        "still_overflowed": False,
    })()


def test_compactor_uses_resolver_at_call_time(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_compact(history, **kwargs):
        captured["model_id"] = kwargs.get("model_id")
        return list(history), _fake_report()

    monkeypatch.setattr(
        "nexus.agent.loop.compactor.compact_and_summarize", _fake_compact
    )

    # The resolver is evaluated per compaction, so it tracks whatever the
    # turn's model is at that moment (never a build-time frozen value).
    current = ["prov/turn-model"]
    compactor = NexusCompactor(model_resolver=lambda: current[0])

    request = _compaction_request()
    request.messages = list(_ONE_MSG)
    asyncio.run(compactor(request))
    assert captured["model_id"] == "prov/turn-model"

    current[0] = "other/model-later"
    asyncio.run(compactor(request))
    assert captured["model_id"] == "other/model-later"


def test_compactor_falls_back_to_static_model_id(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_compact(history, **kwargs):
        captured["model_id"] = kwargs.get("model_id")
        return list(history), _fake_report()

    monkeypatch.setattr(
        "nexus.agent.loop.compactor.compact_and_summarize", _fake_compact
    )

    compactor = NexusCompactor(model_id="static/model")
    request = _compaction_request()
    asyncio.run(compactor(request))
    assert captured["model_id"] == "static/model"


def test_chosen_model_contextvar_isolates_concurrent_turns() -> None:
    """Two tasks launched while a turn is running must not see each other's
    model — the instance attribute is shared, the ContextVar is task-local."""

    async def _scenario() -> tuple[str | None, str | None]:
        set_chosen_model("prov/first")          # turn 1 starts

        async def _second_turn() -> str | None:
            set_chosen_model("prov/second")     # turn 2 starts mid-turn-1
            await asyncio.sleep(0)
            return current_chosen_model(fallback="instance-shared")

        second = asyncio.create_task(_second_turn())
        await asyncio.sleep(0)
        first = current_chosen_model(fallback="instance-shared")
        return first, await second

    first, second = asyncio.run(_scenario())
    assert first == "prov/first"
    assert second == "prov/second"


def test_chosen_model_unset_falls_back_to_instance_state() -> None:
    # No turn entry ran in this task: the instance fallback wins (legacy
    # paths and tests that set Agent._chosen_model directly keep working).
    assert current_chosen_model(fallback="instance") == "instance"
    set_chosen_model(None)
    # An explicit None (default-model turn) must WIN over the fallback —
    # that's the whole point of the sentinel.
    assert current_chosen_model(fallback="instance") is None


# ── compaction loop guard ─────────────────────────────────────────────────


def _guard_scenario(monkeypatch, max_rounds: int):
    """Compactor wired to a counting fake; returns (compactor, calls list)."""
    calls: list[int] = []

    async def _fake_compact(history, **kwargs):
        calls.append(1)
        return list(history), _fake_report()

    monkeypatch.setattr(
        "nexus.agent.loop.compactor.compact_and_summarize", _fake_compact
    )
    compactor = NexusCompactor(
        model_id="m", max_rounds_per_turn=max_rounds,
    )
    return compactor, calls


def test_loop_guard_trips_after_max_rounds(monkeypatch) -> None:
    compactor, calls = _guard_scenario(monkeypatch, MAX_COMPACTION_ROUNDS_PER_TURN)

    async def _turn() -> None:
        # MAX full rounds fit within the budget (loom escalates attempts 2-3
        # inside each round; only attempt==1 starts a new round).
        for _ in range(MAX_COMPACTION_ROUNDS_PER_TURN):
            for attempt in (1, 2, 3):
                await compactor(_compaction_request(attempt))
        # The next round is refused BEFORE spending another compaction pass.
        with pytest.raises(CompactionLoopGuardError, match="compaction rounds"):
            await compactor(_compaction_request(attempt=1))

    asyncio.run(_turn())
    # MAX rounds × 3 attempts ran; the refused round never reached the pipeline.
    assert len(calls) == MAX_COMPACTION_ROUNDS_PER_TURN * 3


def test_loop_guard_message_carries_guidance(monkeypatch) -> None:
    compactor, _ = _guard_scenario(monkeypatch, 1)

    async def _turn() -> None:
        await compactor(_compaction_request(attempt=1))
        with pytest.raises(CompactionLoopGuardError) as exc_info:
            await compactor(_compaction_request(attempt=1))
        msg = str(exc_info.value)
        assert "context window" in msg
        assert "new session" in msg

    asyncio.run(_turn())


def test_loop_guard_disabled_with_zero(monkeypatch) -> None:
    compactor, calls = _guard_scenario(monkeypatch, 0)

    async def _turn() -> None:
        for _ in range(10):
            await compactor(_compaction_request(attempt=1))

    asyncio.run(_turn())
    assert len(calls) == 10


def test_loop_guard_counter_resets_per_turn_entry(monkeypatch) -> None:
    compactor, calls = _guard_scenario(monkeypatch, 1)

    async def _turn() -> None:
        await compactor(_compaction_request(attempt=1))
        # A new turn entry resets the budget (run_turn / _prepare_stream_turn /
        # the resume path all call reset_turn_compaction_rounds).
        reset_turn_compaction_rounds()
        await compactor(_compaction_request(attempt=1))

    asyncio.run(_turn())
    assert calls == [1, 1]


def test_loop_guard_isolated_between_turn_tasks(monkeypatch) -> None:
    """Concurrent turns each get their own budget: the boundary is the turn
    entry's reset (same mechanism as set_chosen_model) — a turn task that
    already burned its rounds must not poison a turn launched alongside it."""
    compactor, _ = _guard_scenario(monkeypatch, 1)

    async def _run_turn() -> None:
        # Turn entry: fresh budget (agent.py does this at all 3 entries).
        reset_turn_compaction_rounds()
        await compactor(_compaction_request(attempt=1))
        assert turn_compaction_rounds()[0] == 1
        # A second round in THIS turn must still be refused — the concurrent
        # turn's resets/compactions never touched this task's counter.
        with pytest.raises(CompactionLoopGuardError):
            await compactor(_compaction_request(attempt=1))

    async def _scenario() -> None:
        first = asyncio.create_task(_run_turn())
        second = asyncio.create_task(_run_turn())
        await asyncio.gather(first, second)

    asyncio.run(_scenario())
