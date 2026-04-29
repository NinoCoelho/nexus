"""Tabs-state persistence tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import nexus.agent.graphrag_manager as grm
from nexus.agent.folder_graph import _tabs_state


@pytest.fixture(autouse=True)
def isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``get_home`` to a temp dir so we don't pollute the user's state."""
    monkeypatch.setattr(grm, "_home", tmp_path)
    yield tmp_path


def test_list_tabs_empty_when_no_state_file(tmp_path: Path) -> None:
    assert _tabs_state.list_tabs() == []


def test_set_tabs_persists_and_normalizes(tmp_path: Path) -> None:
    folder_a = tmp_path / "alpha"
    folder_a.mkdir()
    folder_b = tmp_path / "beta"
    folder_b.mkdir()

    written = _tabs_state.set_tabs([
        {"path": str(folder_a) + "/", "label": "Alpha"},
        {"path": str(folder_b)},
    ])

    # Trailing slashes resolved away
    assert written[0]["path"] == str(folder_a)
    assert written[0]["label"] == "Alpha"
    assert written[1]["path"] == str(folder_b)
    assert written[1]["label"] == "beta"  # default label = basename


def test_add_tab_idempotent(tmp_path: Path) -> None:
    folder = tmp_path / "alpha"
    folder.mkdir()

    _tabs_state.add_tab(str(folder), "Alpha")
    _tabs_state.add_tab(str(folder))  # second call should not duplicate
    tabs = _tabs_state.list_tabs()
    assert len(tabs) == 1
    assert tabs[0]["path"] == str(folder.resolve())


def test_remove_tab_drops_entry(tmp_path: Path) -> None:
    folder = tmp_path / "alpha"
    folder.mkdir()
    _tabs_state.add_tab(str(folder), "Alpha")
    after = _tabs_state.remove_tab(str(folder))
    assert after == []
    assert _tabs_state.list_tabs() == []


def test_remove_tab_noop_for_unknown(tmp_path: Path) -> None:
    folder = tmp_path / "alpha"
    folder.mkdir()
    _tabs_state.add_tab(str(folder), "Alpha")
    after = _tabs_state.remove_tab(str(tmp_path / "ghost"))
    assert len(after) == 1


def test_corrupt_state_file_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "folder_graphs.json").write_text("not json", encoding="utf-8")
    assert _tabs_state.list_tabs() == []
