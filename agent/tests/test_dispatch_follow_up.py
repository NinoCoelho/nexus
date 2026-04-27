"""Tests for the post-turn follow-up that re-runs a card on the same session
when the agent moves it into a different lane during its own turn.

We exercise ``_maybe_follow_up_after_move`` directly with a stubbed
``run_background_agent_turn`` so the test stays independent of the real
agent loop / session store.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import nexus.vault as vault_module
from nexus import vault_kanban
from nexus.agent.context import DISPATCH_CHAIN
from nexus.server.routes import vault_dispatch_helpers as helpers


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


class _StubAgent:
    _provider_registry = None
    _nexus_cfg = None


class _StubStore:
    pass


@pytest.fixture
def captured_follow_ups(monkeypatch):
    """Replace run_background_agent_turn with a stub that records every call.

    We patch the module-level binding inside vault_dispatch_helpers so the
    recursion in ``_maybe_follow_up_after_move`` lands here instead of the
    real implementation (which would need a live Agent + SessionStore).
    """
    calls: list[dict[str, Any]] = []

    async def stub(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(helpers, "run_background_agent_turn", stub)
    return calls


async def test_follow_up_runs_on_same_session_when_card_moves_to_prompted_lane(
    captured_follow_ups,
):
    vault_kanban.create_empty("b.md", columns=["Todo", "Doing"])
    vault_kanban.update_lane("b.md", "doing", {"prompt": "Continue work."})
    card = vault_kanban.add_card("b.md", "todo", "T")
    # Simulate the agent moving it during its turn.
    vault_kanban.move_card("b.md", card.id, "doing")

    await helpers._maybe_follow_up_after_move(
        session_id="sess-1",
        card_path="b.md",
        card_id=card.id,
        starting_lane_id="todo",
        agent_=_StubAgent(),
        store=_StubStore(),
    )

    assert len(captured_follow_ups) == 1
    call = captured_follow_ups[0]
    assert call["session_id"] == "sess-1"  # same session, not a new one
    assert call["card_id"] == card.id
    assert "Continue work." in call["seed_message"]
    assert call["entity_kind"] == "card"


async def test_follow_up_skips_when_card_did_not_move(captured_follow_ups):
    vault_kanban.create_empty("b.md", columns=["Todo", "Doing"])
    vault_kanban.update_lane("b.md", "doing", {"prompt": "Continue."})
    card = vault_kanban.add_card("b.md", "todo", "T")
    # No move — card still in 'todo'.

    await helpers._maybe_follow_up_after_move(
        session_id="sess-1",
        card_path="b.md",
        card_id=card.id,
        starting_lane_id="todo",
        agent_=_StubAgent(),
        store=_StubStore(),
    )
    assert captured_follow_ups == []


async def test_follow_up_skips_when_destination_lane_has_no_prompt(captured_follow_ups):
    vault_kanban.create_empty("b.md", columns=["Todo", "Doing"])
    # 'doing' has no prompt configured.
    card = vault_kanban.add_card("b.md", "todo", "T")
    vault_kanban.move_card("b.md", card.id, "doing")

    await helpers._maybe_follow_up_after_move(
        session_id="sess-1",
        card_path="b.md",
        card_id=card.id,
        starting_lane_id="todo",
        agent_=_StubAgent(),
        store=_StubStore(),
    )
    assert captured_follow_ups == []


async def test_follow_up_respects_max_depth(captured_follow_ups):
    vault_kanban.create_empty("b.md", columns=["Todo", "Doing"])
    vault_kanban.update_lane("b.md", "doing", {"prompt": "Continue."})
    card = vault_kanban.add_card("b.md", "todo", "T")
    vault_kanban.move_card("b.md", card.id, "doing")

    # Saturate the dispatch chain.
    saturated = tuple(f"c{i}" for i in range(helpers.MAX_FOLLOW_UP_DEPTH))
    token = DISPATCH_CHAIN.set(saturated)
    try:
        await helpers._maybe_follow_up_after_move(
            session_id="sess-1",
            card_path="b.md",
            card_id=card.id,
            starting_lane_id="todo",
            agent_=_StubAgent(),
            store=_StubStore(),
        )
    finally:
        DISPATCH_CHAIN.reset(token)
    assert captured_follow_ups == []


async def test_follow_up_skips_when_card_was_deleted(captured_follow_ups):
    vault_kanban.create_empty("b.md", columns=["Todo", "Doing"])
    vault_kanban.update_lane("b.md", "doing", {"prompt": "Continue."})
    card = vault_kanban.add_card("b.md", "todo", "T")
    vault_kanban.delete_card("b.md", card.id)

    await helpers._maybe_follow_up_after_move(
        session_id="sess-1",
        card_path="b.md",
        card_id=card.id,
        starting_lane_id="todo",
        agent_=_StubAgent(),
        store=_StubStore(),
    )
    assert captured_follow_ups == []


async def test_follow_up_marks_card_running_again(captured_follow_ups):
    vault_kanban.create_empty("b.md", columns=["Todo", "Doing"])
    vault_kanban.update_lane("b.md", "doing", {"prompt": "Continue."})
    card = vault_kanban.add_card("b.md", "todo", "T")
    vault_kanban.move_card("b.md", card.id, "doing")
    vault_kanban.update_card("b.md", card.id, {"status": "done"})

    await helpers._maybe_follow_up_after_move(
        session_id="sess-1",
        card_path="b.md",
        card_id=card.id,
        starting_lane_id="todo",
        agent_=_StubAgent(),
        store=_StubStore(),
    )
    board = vault_kanban.read_board("b.md")
    assert board.lanes[1].cards[0].status == "running"
