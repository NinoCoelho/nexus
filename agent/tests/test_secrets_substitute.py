"""``$NAME`` substitution at the tool boundary."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexus import secrets as _s

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")


def test_substitutes_in_string(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import secrets, secrets_substitute

    secrets.set("MY_TOKEN", "abc123", kind="generic")
    monkeypatch.delenv("MY_TOKEN", raising=False)
    out = secrets_substitute.resolve("Bearer $MY_TOKEN")
    assert out == "Bearer abc123"


def test_env_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import secrets, secrets_substitute

    secrets.set("OVERRIDE", "from-store")
    monkeypatch.setenv("OVERRIDE", "from-env")
    assert secrets_substitute.resolve("Bearer $OVERRIDE") == "Bearer from-env"


def test_unknown_placeholder_is_left_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import secrets_substitute

    monkeypatch.delenv("NEVER_SET", raising=False)
    assert secrets_substitute.resolve("Bearer $NEVER_SET") == "Bearer $NEVER_SET"


def test_lowercase_is_not_substituted(monkeypatch: pytest.MonkeyPatch) -> None:
    """``$lower`` and ``$Mixed`` must not be touched (avoid path/var collisions)."""
    from nexus import secrets, secrets_substitute

    secrets.set("LOWER", "should-not-show")
    monkeypatch.delenv("LOWER", raising=False)
    assert secrets_substitute.resolve("/path/$lower") == "/path/$lower"
    assert secrets_substitute.resolve("$Mixed") == "$Mixed"


def test_walks_dicts_and_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus import secrets, secrets_substitute

    secrets.set("AUTH", "tok-x")
    monkeypatch.delenv("AUTH", raising=False)
    args = {
        "headers": {"Authorization": "Bearer $AUTH"},
        "body": {"items": ["before", "$AUTH", {"nested": "$AUTH"}]},
        "url": "https://api.example.com/v1?token=$AUTH",
    }
    out = secrets_substitute.resolve(args)
    assert out["headers"]["Authorization"] == "Bearer tok-x"
    assert out["body"]["items"] == ["before", "tok-x", {"nested": "tok-x"}]
    assert out["url"] == "https://api.example.com/v1?token=tok-x"


def test_non_strings_pass_through() -> None:
    from nexus import secrets_substitute

    assert secrets_substitute.resolve(42) == 42
    assert secrets_substitute.resolve(None) is None
    assert secrets_substitute.resolve(True) is True
