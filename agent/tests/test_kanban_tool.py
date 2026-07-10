"""kanban_manage `view` — large-board truncation.

A `view` of a big board used to dump the entire structure (every card body in
full), which could flood the context window — a 247-card job-search board was
~560KB / 187K tokens (94% of a 200K window). The view now returns a compact
summary by default, with an opt-in `full=true` escape hatch.
"""

from __future__ import annotations

import json

from nexus.tools.kanban_tool import _compact_board_dict, _view


def _board_dict(n_lanes: int, cards_per_lane: int, body_len: int = 500) -> dict:
    return {
        "title": "job-search",
        "lanes": [
            {
                "id": f"l{i}",
                "title": f"Lane {i}",
                "cards": [
                    {"id": f"c{i}-{j}", "title": f"card {j}", "body": "B" * body_len}
                    for j in range(cards_per_lane)
                ],
            }
            for i in range(n_lanes)
        ],
    }


def test_compact_board_dict_caps_cards_and_previews_bodies() -> None:
    bd = _board_dict(n_lanes=3, cards_per_lane=40, body_len=500)
    _compact_board_dict(bd)
    assert bd["truncated"] is True
    assert bd["total_cards"] == 120
    for ln in bd["lanes"]:
        assert ln["card_count"] == 40
        assert len(ln["cards"]) == 10          # capped at _VIEW_CARDS_PER_LANE
        assert ln["cards_truncated"] == 30
        for c in ln["cards"]:
            # body previewed, not verbatim
            assert len(c["body"]) < 250
            assert "[+" in c["body"]


def test_compact_board_dict_small_lanes_not_truncated() -> None:
    bd = _board_dict(n_lanes=2, cards_per_lane=3, body_len=50)
    _compact_board_dict(bd)
    # No cards elided (under the per-lane cap), bodies short enough to keep.
    for ln in bd["lanes"]:
        assert "cards_truncated" not in ln
        assert len(ln["cards"]) == 3
    assert bd["truncated"] is True  # flag still set, but nothing dropped


def test_view_returns_verbatim_when_small(monkeypatch) -> None:
    """A small board is returned unchanged — no behavior regression."""
    small = _board_dict(n_lanes=2, cards_per_lane=2, body_len=20)

    class _FakeBoard:
        def to_dict(self):
            return small

    monkeypatch.setattr(
        "nexus.vault_kanban.read_board", lambda path: _FakeBoard()
    )
    out = json.loads(_view({"action": "view", "path": "boards/x.md"}))
    assert out["ok"] is True
    assert out["board"] == small
    assert "truncated" not in out["board"]


def test_view_truncates_large_board_by_default(monkeypatch) -> None:
    big = _board_dict(n_lanes=9, cards_per_lane=80, body_len=600)
    verbatim_size = len(json.dumps(big, ensure_ascii=False))

    class _FakeBoard:
        def to_dict(self):
            return big

    monkeypatch.setattr(
        "nexus.vault_kanban.read_board", lambda path: _FakeBoard()
    )
    out = json.loads(_view({"action": "view", "path": "boards/job-search.md"}))
    board = out["board"]
    assert board["truncated"] is True
    assert board["total_cards"] == 9 * 80
    for ln in board["lanes"]:
        assert ln["card_count"] == 80
        assert len(ln["cards"]) <= 10
    # Dramatically smaller than the verbatim dump (>90% reduction).
    compacted_size = len(json.dumps(out, ensure_ascii=False))
    assert compacted_size < verbatim_size // 10


def test_view_full_true_returns_verbatim_even_when_large(monkeypatch) -> None:
    big = _board_dict(n_lanes=9, cards_per_lane=80, body_len=600)

    class _FakeBoard:
        def to_dict(self):
            return big

    monkeypatch.setattr(
        "nexus.vault_kanban.read_board", lambda path: _FakeBoard()
    )
    out = json.loads(
        _view({"action": "view", "path": "boards/job-search.md", "full": True})
    )
    assert "truncated" not in out["board"]
    # Every card body kept in full.
    assert any(len(c["body"]) == 600 for ln in out["board"]["lanes"] for c in ln["cards"])
