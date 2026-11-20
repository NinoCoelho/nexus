"""Tests for the persistent flow store (save_flow / replay raw material)."""

from __future__ import annotations

from pathlib import Path

from nexus.browser import flows as flow_store


def test_save_flow_filters_perception_and_redacts_secrets(tmp_path: Path, monkeypatch) -> None:
    fd = tmp_path / "flows"
    fd.mkdir()
    monkeypatch.setattr(flow_store, "flows_dir", lambda: fd)
    steps = [
        {"action": "read", "args": {}},
        {"action": "find_element", "args": {"role": "button"}},
        {"action": "navigate", "args": {"url": "https://admin.example/"}},
        {"action": "type", "args": {"selector": "#user", "text": "admin@x.com"}},
        {"action": "type", "args": {"selector": "#pass", "text": "s3cret-pw"}},
        {"action": "click", "args": {"selector": "#go"}},
        {"action": "replay", "args": {"name": "meta-never-recorded"}},
    ]
    path = flow_store.save_flow("add perm!", "ext", steps)
    assert path.exists()
    saved = flow_store.load_flow("add perm!")
    assert saved is not None and saved["surface"] == "ext"
    names = [s["action"] for s in saved["steps"]]
    # perception dropped, meta never recorded, state-changing kept
    assert names == ["navigate", "type", "type", "click"]
    # typed values redacted + flagged
    for s in saved["steps"]:
        if s["action"] == "type":
            assert s["needs_value"] is True
            assert not any(s["args"].get(k) for k in ("text", "password", "value"))
    assert "s3cret-pw" not in path.read_text()

    listed = flow_store.list_flows()
    assert len(listed) == 1 and listed[0]["name"] == "add perm!"
    assert listed[0]["steps"] == 4 and listed[0]["surface"] == "ext"


def test_load_missing_flow_returns_none(tmp_path: Path, monkeypatch) -> None:
    fd = tmp_path / "flows"
    fd.mkdir()
    monkeypatch.setattr(flow_store, "flows_dir", lambda: fd)
    assert flow_store.load_flow("nope") is None
    assert flow_store.list_flows() == []
