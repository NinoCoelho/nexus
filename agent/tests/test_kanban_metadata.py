"""Tests for card metadata round-trip + cross-board kanban_query."""

from __future__ import annotations

from pathlib import Path

import pytest

import nexus.vault as vault_module
from nexus import vault_kanban
from nexus.tools.kanban_query_tool import handle_kanban_query_tool
from nexus.tools.kanban_tool import handle_kanban_tool
import json


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    return vault_root


def test_card_metadata_round_trip():
    vault_kanban.create_empty("a.md")
    card = vault_kanban.add_card("a.md", "todo", "Plan trip")
    vault_kanban.update_card("a.md", card.id, {
        "due": "2026-05-01",
        "priority": "high",
        "labels": ["travel", "personal"],
        "assignees": ["nino"],
    })
    board = vault_kanban.read_board("a.md")
    found = board.lanes[0].cards[0]
    assert found.due == "2026-05-01"
    assert found.priority == "high"
    assert found.labels == ["travel", "personal"]
    assert found.assignees == ["nino"]


def test_card_metadata_clears():
    vault_kanban.create_empty("b.md")
    card = vault_kanban.add_card("b.md", "todo", "x")
    vault_kanban.update_card("b.md", card.id, {"priority": "low", "labels": ["a"]})
    vault_kanban.update_card("b.md", card.id, {"priority": "", "labels": []})
    board = vault_kanban.read_board("b.md")
    found = board.lanes[0].cards[0]
    assert found.priority is None
    assert found.labels == []


def test_card_invalid_priority_raises():
    vault_kanban.create_empty("c.md")
    card = vault_kanban.add_card("c.md", "todo", "x")
    with pytest.raises(ValueError, match="invalid priority"):
        vault_kanban.update_card("c.md", card.id, {"priority": "bogus"})


def test_query_boards_cross_board():
    vault_kanban.create_empty("work.md")
    vault_kanban.create_empty("home.md")
    work = vault_kanban.add_card("work.md", "todo", "Ship feature X")
    vault_kanban.update_card("work.md", work.id, {
        "labels": ["urgent"], "due": "2026-04-30", "priority": "high",
    })
    home = vault_kanban.add_card("home.md", "todo", "Buy milk")
    vault_kanban.update_card("home.md", home.id, {"labels": ["errand"]})

    hits = vault_kanban.query_boards()
    assert len(hits) == 2
    paths = sorted(h["path"] for h in hits)
    assert paths == ["home.md", "work.md"]

    hits_label = vault_kanban.query_boards(label="urgent")
    assert len(hits_label) == 1
    assert hits_label[0]["card_id"] == work.id

    hits_due = vault_kanban.query_boards(due_before="2026-05-01")
    assert len(hits_due) == 1
    assert hits_due[0]["card_id"] == work.id

    hits_text = vault_kanban.query_boards(text="MILK")  # case-insensitive
    assert len(hits_text) == 1
    assert hits_text[0]["card_id"] == home.id


def test_query_boards_lane_filter():
    vault_kanban.create_empty("p.md", columns=["Backlog", "Doing"])
    backlog_card = vault_kanban.add_card("p.md", "backlog", "A")
    vault_kanban.add_card("p.md", "doing", "B")
    hits = vault_kanban.query_boards(lane="backlog")
    assert len(hits) == 1
    assert hits[0]["card_id"] == backlog_card.id
    # Title also matches
    hits_title = vault_kanban.query_boards(lane="Backlog")
    assert len(hits_title) == 1


def test_kanban_query_tool():
    vault_kanban.create_empty("q.md")
    c = vault_kanban.add_card("q.md", "todo", "Triage P0s")
    vault_kanban.update_card("q.md", c.id, {"priority": "urgent"})
    out = json.loads(handle_kanban_query_tool({"priority": "urgent"}))
    assert out["ok"]
    assert out["count"] == 1
    assert out["hits"][0]["title"] == "Triage P0s"


def test_kanban_manage_tool_update_lane():
    vault_kanban.create_empty("k.md", columns=["Triage"])
    out = json.loads(handle_kanban_tool({
        "action": "update_lane", "path": "k.md", "lane": "triage",
        "prompt": "Summarise the card.", "model": "claude-sonnet-4-6",
    }))
    assert out["ok"] is True
    assert out["lane"]["prompt"] == "Summarise the card."
    assert out["lane"]["model"] == "claude-sonnet-4-6"

    # Clearing the prompt
    out = json.loads(handle_kanban_tool({
        "action": "update_lane", "path": "k.md", "lane": "triage", "prompt": "",
    }))
    assert out["ok"] is True
    assert "prompt" not in out["lane"]


def test_kanban_manage_tool_update_lane_requires_lane():
    vault_kanban.create_empty("k.md")
    out = json.loads(handle_kanban_tool({
        "action": "update_lane", "path": "k.md", "prompt": "x",
    }))
    assert out["ok"] is False
    assert "lane" in out["error"]


def test_lane_model_round_trip():
    vault_kanban.create_empty("lm.md", columns=["Triage"])
    vault_kanban.update_lane("lm.md", "triage", {
        "prompt": "Summarise this card.", "model": "claude-sonnet-4-6",
    })
    board = vault_kanban.read_board("lm.md")
    assert board.lanes[0].prompt == "Summarise this card."
    assert board.lanes[0].model == "claude-sonnet-4-6"

    # Clearing model leaves prompt intact
    vault_kanban.update_lane("lm.md", "triage", {"model": ""})
    board = vault_kanban.read_board("lm.md")
    assert board.lanes[0].model is None
    assert board.lanes[0].prompt == "Summarise this card."


def test_move_lane_reorders_columns():
    vault_kanban.create_empty("ml.md", columns=["Todo", "Doing", "Done"])
    # Move "Done" to the front.
    vault_kanban.move_lane("ml.md", "done", 0)
    board = vault_kanban.read_board("ml.md")
    assert [ln.id for ln in board.lanes] == ["done", "todo", "doing"]

    # Position past the end appends.
    vault_kanban.move_lane("ml.md", "done", 99)
    board = vault_kanban.read_board("ml.md")
    assert [ln.id for ln in board.lanes] == ["todo", "doing", "done"]

    # Position None also appends.
    vault_kanban.move_lane("ml.md", "todo", None)
    board = vault_kanban.read_board("ml.md")
    assert [ln.id for ln in board.lanes] == ["doing", "done", "todo"]


def test_move_lane_unknown_raises():
    vault_kanban.create_empty("mu.md", columns=["A"])
    with pytest.raises(KeyError):
        vault_kanban.move_lane("mu.md", "missing", 0)


def test_kanban_manage_tool_move_lane():
    vault_kanban.create_empty("mlt.md", columns=["A", "B", "C"])
    out = json.loads(handle_kanban_tool({
        "action": "move_lane", "path": "mlt.md", "lane": "c", "position": 0,
    }))
    assert out["ok"] is True
    board = vault_kanban.read_board("mlt.md")
    assert [ln.id for ln in board.lanes] == ["c", "a", "b"]

    # Missing lane id fails fast.
    out = json.loads(handle_kanban_tool({
        "action": "move_lane", "path": "mlt.md", "position": 0,
    }))
    assert out["ok"] is False
    assert "lane" in out["error"]


def test_kanban_manage_tool_metadata():
    vault_kanban.create_empty("m.md")
    add = json.loads(handle_kanban_tool({
        "action": "add_card", "path": "m.md", "lane": "todo", "title": "T",
    }))
    cid = add["card"]["id"]
    upd = json.loads(handle_kanban_tool({
        "action": "update_card", "path": "m.md", "card_id": cid,
        "due": "2026-04-25", "priority": "med", "labels": ["x", "y"],
    }))
    assert upd["ok"]
    assert upd["card"]["due"] == "2026-04-25"
    assert upd["card"]["priority"] == "med"
    assert upd["card"]["labels"] == ["x", "y"]
