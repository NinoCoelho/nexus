"""Tests for opt-in vault history (git-backed)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nexus import config_file, vault, vault_history


@pytest.fixture
def isolated_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect vault root, history repo, cursors file, and config path."""
    if shutil.which("git") is None:
        pytest.skip("git not installed; vault history requires it")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    monkeypatch.setattr(vault, "_VAULT_ROOT", vault_root)
    monkeypatch.setattr(vault_history, "_VAULT_ROOT", vault_root)
    monkeypatch.setattr(vault_history, "_HISTORY_DIR", tmp_path / ".vault-history")
    monkeypatch.setattr(
        vault_history, "_CURSORS_PATH", tmp_path / ".vault-history-cursors.json"
    )
    monkeypatch.setattr(config_file, "CONFIG_PATH", tmp_path / "config.toml")
    return vault_root


def test_disabled_by_default(isolated_history: Path) -> None:
    assert vault_history.is_enabled() is False
    s = vault_history.status()
    assert s["enabled"] is False
    assert s["repo_exists"] is False


def test_enable_creates_repo_and_bootstrap_commit(isolated_history: Path) -> None:
    s = vault_history.enable()
    assert s["enabled"] is True
    assert s["repo_exists"] is True
    assert s["commit_count"] == 1
    assert s["last_commit"]["action"] == "enable"


def test_record_runs_per_save(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("a.md", "v1")
    vault.write_file("a.md", "v2")
    commits = vault_history.log(path="a.md")
    assert len(commits) == 2
    assert commits[0].action == "write"
    assert commits[0].message == "write: a.md"


def test_undo_steps_back_one_commit(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("a.md", "v1")
    vault.write_file("a.md", "v2")

    result = vault_history.undo("a.md")
    assert result.undone is True
    assert (isolated_history / "a.md").read_text() == "v1"

    # commit count grew by 1 (the "undo: a.md" commit)
    log = vault_history.log()
    assert any(c.message == "undo: a.md" for c in log)


def test_undo_walks_back_through_multiple_real_commits(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("a.md", "v1")
    vault.write_file("a.md", "v2")
    vault.write_file("a.md", "v3")

    r1 = vault_history.undo("a.md")
    assert r1.undone is True
    assert (isolated_history / "a.md").read_text() == "v2"

    r2 = vault_history.undo("a.md")
    assert r2.undone is True
    assert (isolated_history / "a.md").read_text() == "v1"


def test_undo_runs_out_of_history(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("a.md", "only-version")
    # Bootstrap commit covered an empty vault, so a.md exists in only one
    # real commit. Undo should signal no_history (we don't pre-creation-undo).
    r = vault_history.undo("a.md")
    assert r.undone is False
    assert r.reason == "no_history"


def test_undo_resurrects_deleted_file(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("a.md", "alive")
    vault.delete("a.md")
    assert not (isolated_history / "a.md").exists()
    r = vault_history.undo("a.md")
    assert r.undone is True
    assert (isolated_history / "a.md").read_text() == "alive"


def test_undo_folder_reverts_children(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("f/a.md", "a-v1")
    vault.write_file("f/b.md", "b-v1")
    vault.write_file("f/a.md", "a-v2")
    vault.write_file("f/b.md", "b-v2")

    r = vault_history.undo("f")
    assert r.undone is True
    # The most recent real commit touching `f/` was `write: f/b.md` which only
    # changed b.md — so undoing the folder rolls b.md back to b-v1, while a.md
    # stays at a-v2 (it wasn't part of the commit being undone).
    assert (isolated_history / "f" / "b.md").read_text() == "b-v1"


def test_disable_stops_recording(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("a.md", "v1")
    n_before = vault_history.status()["commit_count"]
    vault_history.disable()
    vault.write_file("a.md", "v2")
    # repo still exists, commit count unchanged
    n_after = vault_history.status()["commit_count"]
    assert n_after == n_before


def test_path_escape_rejected(isolated_history: Path) -> None:
    vault_history.enable()
    with pytest.raises(ValueError):
        vault_history.undo("../etc/passwd")


def test_log_filter_by_path(isolated_history: Path) -> None:
    vault_history.enable()
    vault.write_file("a.md", "a")
    vault.write_file("b.md", "b")
    only_a = vault_history.log(path="a.md")
    assert all(c.message == "write: a.md" or c.action == "enable" for c in only_a)
    assert any(c.message == "write: a.md" for c in only_a)


def test_record_is_noop_when_disabled(isolated_history: Path) -> None:
    # Without enable(), record() does nothing and never raises.
    sha = vault_history.record(["a.md"], "write: a.md")
    assert sha is None
