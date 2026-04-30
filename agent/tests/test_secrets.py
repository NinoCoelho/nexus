"""Tests for the generalized secret store.

Covers:
- ``set`` / ``get`` round-trip
- ``resolve`` env-first precedence
- ``exists`` returns true for env-only values
- ``list_all`` masks values and never returns raw secrets
- Old-format files (``[keys]`` only, no ``[meta]``) still load
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def secrets_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Import ``nexus.secrets`` with SECRETS_PATH redirected to a temp file."""
    from nexus import secrets as _s

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")
    return _s


def test_set_then_get_round_trip(secrets_module) -> None:
    secrets_module.set("MY_KEY", "value-1", kind="generic")
    assert secrets_module.get("MY_KEY") == "value-1"


def test_resolve_env_first(secrets_module, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets_module.set("OVERRIDE_ME", "from-store")
    monkeypatch.setenv("OVERRIDE_ME", "from-env")
    assert secrets_module.resolve("OVERRIDE_ME") == "from-env"
    # get() stays file-only
    assert secrets_module.get("OVERRIDE_ME") == "from-store"


def test_resolve_falls_back_to_store(secrets_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORE_ONLY", raising=False)
    secrets_module.set("STORE_ONLY", "from-store")
    assert secrets_module.resolve("STORE_ONLY") == "from-store"


def test_resolve_missing(secrets_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    assert secrets_module.resolve("NOPE") is None


def test_exists_env_only(secrets_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_ONLY", "x")
    assert secrets_module.exists("ENV_ONLY") is True


def test_exists_store_only(secrets_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORE_ONLY_2", raising=False)
    secrets_module.set("STORE_ONLY_2", "abc")
    assert secrets_module.exists("STORE_ONLY_2") is True


def test_list_all_masks_values_and_includes_meta(secrets_module) -> None:
    secrets_module.set("LONG_KEY_NAME", "sk-abcdefghijklmn", kind="provider")
    secrets_module.set("SHORTY", "abc", kind="skill", skill="github")

    items = {e["name"]: e for e in secrets_module.list_all()}
    assert items["LONG_KEY_NAME"]["kind"] == "provider"
    assert items["LONG_KEY_NAME"]["masked"] == "sk-…klmn"
    assert items["LONG_KEY_NAME"]["source"] == "store"
    assert "sk-abcdefghijklmn" not in str(items)

    assert items["SHORTY"]["kind"] == "skill"
    assert items["SHORTY"]["skill"] == "github"
    assert items["SHORTY"]["masked"] == "••••"


def test_delete_removes_meta(secrets_module) -> None:
    secrets_module.set("BYE", "v", kind="generic")
    assert secrets_module.exists("BYE")
    secrets_module.delete("BYE")
    assert secrets_module.get("BYE") is None
    assert all(e["name"] != "BYE" for e in secrets_module.list_all())


def test_legacy_format_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files written by the old code (``[keys]`` only, no ``[meta]``) keep working."""
    from nexus import secrets as _s

    legacy = tmp_path / "secrets.toml"
    legacy.write_text('[keys]\nFOO = "bar"\n')
    monkeypatch.setattr(_s, "SECRETS_PATH", legacy)

    assert _s.get("FOO") == "bar"
    items = _s.list_all()
    assert any(e["name"] == "FOO" for e in items)
    # No meta written → defaults to "generic"
    foo = next(e for e in items if e["name"] == "FOO")
    assert foo["kind"] == "generic"
