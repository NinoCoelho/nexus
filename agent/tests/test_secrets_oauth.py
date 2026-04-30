"""OAuth bundle helpers in the secrets store.

Exercises the JSON-encoded-bundle storage path, the listing's
oauth-aware mask, and the refresh-on-near-expiry behavior.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nexus import secrets
from nexus.secrets import OAuthBundle


@pytest.fixture(autouse=True)
def _isolated_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect SECRETS_PATH at the user's tmp dir so tests don't
    touch ~/.nexus/secrets.toml. Mirrors the pattern in conftest +
    test_credentials_routes.py."""
    monkeypatch.setattr(secrets, "SECRETS_PATH", tmp_path / "secrets.toml")


def test_set_and_get_oauth_round_trip() -> None:
    secrets.set_oauth(
        "ANTHROPIC_OAUTH",
        refresh="r-token",
        access="a-token",
        expires_at=1_900_000_000,
        account_id="acc_123",
    )
    bundle = secrets.get_oauth("ANTHROPIC_OAUTH")
    assert bundle is not None
    assert bundle.refresh == "r-token"
    assert bundle.access == "a-token"
    assert bundle.expires_at == 1_900_000_000
    assert bundle.account_id == "acc_123"


def test_get_oauth_returns_none_when_absent() -> None:
    assert secrets.get_oauth("NEVER_STORED") is None


def test_get_oauth_returns_none_for_non_oauth_value() -> None:
    """A plain API key stored under a name should not deserialize as
    an OAuthBundle — get_oauth must tolerate that and return None."""
    secrets.set("PLAIN_KEY", "sk-abc123", kind="provider")
    assert secrets.get_oauth("PLAIN_KEY") is None


def test_list_all_masks_oauth_with_account_label() -> None:
    secrets.set_oauth(
        "GH_COPILOT",
        refresh="r",
        access="a",
        expires_at=1_900_000_000,
        account_id="user@example.com",
    )
    rows = {row["name"]: row for row in secrets.list_all()}
    assert "GH_COPILOT" in rows
    assert rows["GH_COPILOT"]["kind"] == "oauth"
    assert "OAuth" in rows["GH_COPILOT"]["masked"]
    assert "user@example.com" in rows["GH_COPILOT"]["masked"]
    # Critically — the JSON token bodies must not appear in the listing.
    assert '"refresh"' not in rows["GH_COPILOT"]["masked"]


def test_list_all_masks_oauth_without_account_id() -> None:
    secrets.set_oauth(
        "ANON_OAUTH", refresh="r", access="a", expires_at=1_900_000_000
    )
    rows = {row["name"]: row for row in secrets.list_all()}
    assert rows["ANON_OAUTH"]["masked"].startswith("OAuth")


async def test_refresh_oauth_skips_when_not_stale() -> None:
    far_future = int(time.time()) + 3600
    secrets.set_oauth(
        "FRESH",
        refresh="r-old",
        access="a-old",
        expires_at=far_future,
    )
    calls = 0

    async def _refresher(_b: OAuthBundle) -> OAuthBundle:
        nonlocal calls
        calls += 1
        return OAuthBundle(refresh="r-new", access="a-new", expires_at=far_future)

    out = await secrets.refresh_oauth_if_needed("FRESH", _refresher)
    assert out is not None
    assert out.access == "a-old"      # refresher NOT invoked
    assert calls == 0


async def test_refresh_oauth_runs_when_stale() -> None:
    near_past = int(time.time()) - 10
    secrets.set_oauth(
        "STALE", refresh="r-old", access="a-old", expires_at=near_past
    )

    async def _refresher(b: OAuthBundle) -> OAuthBundle:
        assert b.refresh == "r-old"
        return OAuthBundle(
            refresh="r-new", access="a-new", expires_at=near_past + 7200
        )

    out = await secrets.refresh_oauth_if_needed("STALE", _refresher)
    assert out is not None
    assert out.access == "a-new"
    # Stored bundle was rotated.
    persisted = secrets.get_oauth("STALE")
    assert persisted is not None
    assert persisted.access == "a-new"
    assert persisted.refresh == "r-new"


async def test_refresh_oauth_returns_none_when_unset() -> None:
    async def _never(b: OAuthBundle) -> OAuthBundle:  # pragma: no cover
        raise AssertionError("refresher should not be called for missing bundle")

    out = await secrets.refresh_oauth_if_needed("MISSING", _never)
    assert out is None
