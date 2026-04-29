"""Stale-detection tests: mtime walk vs manifest."""

from __future__ import annotations

import os
from pathlib import Path

from nexus.agent.folder_graph import _storage, stale_files


def _touch(p: Path, content: str = "") -> None:
    p.write_text(content, encoding="utf-8")


def _bump_mtime(p: Path, delta: float = 5.0) -> None:
    s = p.stat()
    os.utime(p, (s.st_atime, s.st_mtime + delta))


def test_no_index_returns_all_as_added(tmp_path: Path) -> None:
    _touch(tmp_path / "a.md", "x")
    _touch(tmp_path / "b.md", "y")
    result = stale_files(tmp_path)
    assert sorted(result["added"]) == ["a.md", "b.md"]
    assert result["changed"] == []
    assert result["removed"] == []


def test_unchanged_files_show_as_clean(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    _touch(a, "hello")
    conn = _storage.open_manifest(tmp_path)
    s = a.stat()
    _storage.upsert_file(conn, "a.md", mtime=s.st_mtime, size=s.st_size,
                         hash_=_storage.content_hash("hello"))
    conn.close()

    result = stale_files(tmp_path)
    assert result["added"] == []
    assert result["changed"] == []
    assert result["removed"] == []


def test_modified_file_appears_as_changed(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    _touch(a, "hello")
    s = a.stat()
    conn = _storage.open_manifest(tmp_path)
    _storage.upsert_file(conn, "a.md", mtime=s.st_mtime, size=s.st_size,
                         hash_=_storage.content_hash("hello"))
    conn.close()

    _touch(a, "hello world! this is a longer body so size differs")
    _bump_mtime(a)

    result = stale_files(tmp_path)
    assert result["changed"] == ["a.md"]


def test_new_file_appears_as_added(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    _touch(a, "hi")
    s = a.stat()
    conn = _storage.open_manifest(tmp_path)
    _storage.upsert_file(conn, "a.md", mtime=s.st_mtime, size=s.st_size,
                         hash_=_storage.content_hash("hi"))
    conn.close()

    _touch(tmp_path / "b.md", "new")
    result = stale_files(tmp_path)
    assert result["added"] == ["b.md"]
    assert result["changed"] == []


def test_deleted_file_appears_as_removed(tmp_path: Path) -> None:
    a = tmp_path / "a.md"
    _touch(a, "x")
    s = a.stat()
    conn = _storage.open_manifest(tmp_path)
    _storage.upsert_file(conn, "a.md", mtime=s.st_mtime, size=s.st_size,
                         hash_=_storage.content_hash("x"))
    _storage.upsert_file(conn, "missing.md", mtime=0, size=0, hash_="zz")
    conn.close()

    result = stale_files(tmp_path)
    assert "missing.md" in result["removed"]
    assert "a.md" not in result["removed"]


def test_hidden_dir_excluded_from_walk(tmp_path: Path) -> None:
    _touch(tmp_path / "real.md", "x")
    nested_hidden = tmp_path / ".nexus-graph" / "junk.md"
    nested_hidden.parent.mkdir(parents=True)
    _touch(nested_hidden, "should not be picked up")

    result = stale_files(tmp_path)
    assert result["added"] == ["real.md"]


def test_only_text_extensions_included(tmp_path: Path) -> None:
    _touch(tmp_path / "doc.md", "m")
    _touch(tmp_path / "notes.txt", "t")
    _touch(tmp_path / "photo.jpg", "binary")  # treated as text by Path.write_text but extension excluded
    _touch(tmp_path / "data.json", "binary")

    result = stale_files(tmp_path)
    assert sorted(result["added"]) == ["doc.md", "notes.txt"]
