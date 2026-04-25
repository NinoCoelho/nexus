"""Tests for the move_card lane-change hook."""

from __future__ import annotations

from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import vault_kanban


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    yield vault_root
    # Always unregister the hook between tests so a leak doesn't cross over.
    vault_kanban.set_lane_change_hook(None)


def test_hook_fires_on_cross_lane_move():
    # Set up first, then register the hook — so we only capture the move,
    # not the add_card that also fires the hook now.
    vault_kanban.create_empty("b.md", columns=["Todo", "Done"])
    card = vault_kanban.add_card("b.md", "todo", "X")
    vault_kanban.update_lane("b.md", "done", {"prompt": "Summarise"})

    calls: list[dict] = []
    vault_kanban.set_lane_change_hook(lambda **kw: calls.append(kw))
    vault_kanban.move_card("b.md", card.id, "done")

    assert len(calls) == 1
    c = calls[0]
    assert c["path"] == "b.md"
    assert c["card_id"] == card.id
    assert c["src_lane_id"] == "todo"
    assert c["dst_lane_id"] == "done"
    assert c["dst_lane_prompt"] == "Summarise"


def test_hook_not_fired_on_same_lane_reorder():
    vault_kanban.create_empty("b.md", columns=["Todo"])
    a = vault_kanban.add_card("b.md", "todo", "A")
    vault_kanban.add_card("b.md", "todo", "B")

    calls: list[dict] = []
    vault_kanban.set_lane_change_hook(lambda **kw: calls.append(kw))
    # Same-lane reorder: position changes, lane does not.
    vault_kanban.move_card("b.md", a.id, "todo", position=1)

    assert calls == []


def test_hook_receives_none_prompt_when_dst_lane_has_no_prompt():
    vault_kanban.create_empty("b.md", columns=["Todo", "Done"])
    card = vault_kanban.add_card("b.md", "todo", "X")

    calls: list[dict] = []
    vault_kanban.set_lane_change_hook(lambda **kw: calls.append(kw))
    # Done lane has no prompt configured.
    vault_kanban.move_card("b.md", card.id, "done")

    assert len(calls) == 1
    assert calls[0]["dst_lane_prompt"] is None


def test_hook_exception_does_not_break_move():
    vault_kanban.create_empty("b.md", columns=["Todo", "Done"])
    card = vault_kanban.add_card("b.md", "todo", "X")

    def boom(**kw):
        raise RuntimeError("hook crashed")

    vault_kanban.set_lane_change_hook(boom)
    # Move must succeed even though the hook raises.
    moved = vault_kanban.move_card("b.md", card.id, "done")
    assert moved.id == card.id

    board = vault_kanban.read_board("b.md")
    assert board.lanes[1].cards[0].id == card.id


def test_hook_fires_on_add_card_into_prompt_lane():
    calls: list[dict] = []
    vault_kanban.set_lane_change_hook(lambda **kw: calls.append(kw))

    vault_kanban.create_empty("b.md", columns=["Triage"])
    vault_kanban.update_lane("b.md", "triage", {"prompt": "Look at this"})
    card = vault_kanban.add_card("b.md", "triage", "New ticket")

    assert len(calls) == 1
    c = calls[0]
    assert c["card_id"] == card.id
    assert c["src_lane_id"] == ""
    assert c["dst_lane_id"] == "triage"
    assert c["dst_lane_prompt"] == "Look at this"


def test_hook_fires_on_add_card_with_no_prompt_too():
    # Hook still receives the call (with prompt=None) — the *server-side*
    # guard decides whether to act. Keeps vault_kanban policy-free.
    calls: list[dict] = []
    vault_kanban.set_lane_change_hook(lambda **kw: calls.append(kw))

    vault_kanban.create_empty("b.md", columns=["Plain"])
    vault_kanban.add_card("b.md", "plain", "X")

    assert len(calls) == 1
    assert calls[0]["dst_lane_prompt"] is None


def test_hook_unregister():
    calls: list[dict] = []
    vault_kanban.set_lane_change_hook(lambda **kw: calls.append(kw))
    vault_kanban.set_lane_change_hook(None)

    vault_kanban.create_empty("b.md", columns=["Todo", "Done"])
    card = vault_kanban.add_card("b.md", "todo", "X")
    vault_kanban.move_card("b.md", card.id, "done")
    assert calls == []
