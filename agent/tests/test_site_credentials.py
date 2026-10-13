"""Tests for the encrypted site credential store (nexus.site_credentials)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nexus import site_credentials as sc


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "SITE_CREDS_PATH", tmp_path / "site_credentials.db")
    monkeypatch.setattr(sc, "SITE_CREDS_KEY_PATH", tmp_path / "keys" / "site_credentials.key")


def test_normalize_site_matrix() -> None:
    assert sc.normalize_site("github.com") == "github.com"
    assert sc.normalize_site("https://github.com/login") == "github.com"
    assert sc.normalize_site("https://www.GitHub.com/login?ref=x#frag") == "github.com"
    assert sc.normalize_site("http://user:pass@sub.example.com:8443/path") == "sub.example.com"
    assert sc.normalize_site("  WWW.Example.COM/  ") == "example.com"
    assert sc.normalize_site("app.example.com") == "app.example.com"
    assert sc.normalize_site("") == ""
    assert sc.normalize_site("not a url ://") == ""  # garbage degrades to empty, never raises


def test_save_get_roundtrip() -> None:
    saved = sc.save("https://www.example.com/login", "alice", "hunter2")
    assert saved.site == "example.com"
    cred = sc.get("example.com")
    assert cred is not None
    assert cred.username == "alice"
    assert cred.password == "hunter2"
    # Same entry from any spelling of the site.
    assert sc.exists("https://example.com/")
    assert sc.exists("www.example.com")


def test_encrypted_at_rest_and_modes() -> None:
    sc.save("example.com", "alice", "hunter2")
    raw = sc.SITE_CREDS_PATH.read_bytes()
    assert b"hunter2" not in raw
    assert b"alice" not in raw
    # Key + store are both 0600.
    assert oct(os.stat(sc.SITE_CREDS_PATH).st_mode)[-3:] == "600"
    assert oct(os.stat(sc.SITE_CREDS_KEY_PATH).st_mode)[-3:] == "600"


def test_list_sites_never_returns_password() -> None:
    sc.save("example.com", "alice", "hunter2")
    entries = sc.list_sites()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["site"] == "example.com"
    assert entry["username"] == "alice"
    assert "password" not in entry
    assert "hunter2" not in json.dumps(entries)


def test_overwrite_preserves_created_and_updates() -> None:
    sc.save("example.com", "alice", "one")
    first = sc.get("example.com")
    assert first is not None
    sc.save("example.com", "alice", "two")
    second = sc.get("example.com")
    assert second is not None
    assert second.username == "alice"
    assert second.password == "two"
    assert second.created_at == first.created_at
    assert second.updated_at != first.updated_at or first.updated_at is None


# ── multiple credentials per site ──────────────────────────────────────────


def test_second_username_adds_account_not_replaces() -> None:
    sc.save("example.com", "alice", "one")
    sc.save("example.com", "bob", "two")
    assert sc.usernames("example.com") == ["bob", "alice"]  # newest updated first
    alice = sc.get("example.com", "alice")
    bob = sc.get("example.com", "bob")
    assert alice is not None and alice.password == "one"
    assert bob is not None and bob.password == "two"
    # Default (no username) = most recently updated.
    default = sc.get("example.com")
    assert default is not None and default.username == "bob"


def test_list_sites_one_row_per_credential() -> None:
    sc.save("example.com", "alice", "one")
    sc.save("example.com", "bob", "two")
    sc.save("other.com", "carol", "three")
    rows = sc.list_sites()
    assert len(rows) == 3
    by_pair = {(r["site"], r["username"]) for r in rows}
    assert by_pair == {("example.com", "alice"), ("example.com", "bob"), ("other.com", "carol")}
    assert "password" not in json.dumps(rows)


def test_delete_one_username_keeps_the_other() -> None:
    sc.save("example.com", "alice", "one")
    sc.save("example.com", "bob", "two")
    assert sc.delete("example.com", "alice") is True
    assert sc.usernames("example.com") == ["bob"]
    assert sc.delete("example.com", "alice") is False  # already gone
    # Without a username: wipes the whole site.
    assert sc.delete("example.com") is True
    assert sc.exists("example.com") is False


def test_get_unknown_username_lists_nothing() -> None:
    sc.save("example.com", "alice", "one")
    assert sc.get("example.com", "bob") is None
    assert sc.get("example.com", "alice") is not None


def test_mark_used_targets_username() -> None:
    sc.save("example.com", "alice", "one")
    sc.save("example.com", "bob", "two")
    sc.mark_used("example.com", "alice")
    alice = sc.get("example.com", "alice")
    bob = sc.get("example.com", "bob")
    assert alice is not None and alice.last_used_at is not None
    assert bob is not None and bob.last_used_at is None


def test_v1_single_entry_file_migrates_on_load() -> None:
    """Old store shape {site: {entry}} becomes [entry] transparently."""
    sc.save("example.com", "alice", "one")  # creates key + v2 file
    # Hand-write a v1 file with the same key.
    from cryptography.fernet import Fernet

    key = Fernet(sc.SITE_CREDS_KEY_PATH.read_bytes().strip())
    v1 = {
        "legacy.com": {
            "username": "old-user",
            "password": "old-pass",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
            "last_used_at": None,
        }
    }
    sc.SITE_CREDS_PATH.write_bytes(key.encrypt(json.dumps(v1).encode()))
    cred = sc.get("legacy.com")
    assert cred is not None
    assert cred.username == "old-user"
    assert cred.password == "old-pass"
    # A new save for the same site persists the migrated list shape.
    sc.save("legacy.com", "new-user", "new-pass")
    assert sc.usernames("legacy.com") == ["new-user", "old-user"]


def test_delete() -> None:
    sc.save("example.com", "alice", "hunter2")
    assert sc.delete("https://example.com/x") is True
    assert sc.exists("example.com") is False
    assert sc.delete("example.com") is False


def test_save_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        sc.save("", "alice", "pw")
    with pytest.raises(ValueError):
        sc.save("example.com", "", "pw")
    with pytest.raises(ValueError):
        sc.save("example.com", "alice", "")


def test_corrupt_store_treated_as_empty() -> None:
    sc.SITE_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    sc.SITE_CREDS_PATH.write_bytes(b"not-fernet")
    assert sc.list_sites() == []
    assert sc.get("example.com") is None


def test_mark_used_updates_timestamp() -> None:
    sc.save("example.com", "alice", "hunter2")
    assert sc.mark_used("https://example.com") is None
    cred = sc.get("example.com")
    assert cred is not None
    assert cred.last_used_at is not None
