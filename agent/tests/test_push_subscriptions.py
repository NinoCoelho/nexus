"""Push subscription CRUD + dead-endpoint cleanup."""

from __future__ import annotations

import pytest

from nexus.server.session_store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(db_path=tmp_path / "sessions.sqlite")


def test_upsert_then_list_then_delete(store):
    store.upsert_push_subscription(
        endpoint="https://fcm.example/abc",
        p256dh="pk",
        auth="auth",
        user_agent="Mozilla/5.0",
    )
    subs = store.list_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["endpoint"] == "https://fcm.example/abc"
    assert subs[0]["p256dh"] == "pk"
    assert subs[0]["auth"] == "auth"
    assert subs[0]["user_agent"] == "Mozilla/5.0"

    assert store.delete_push_subscription("https://fcm.example/abc") is True
    assert store.list_push_subscriptions() == []
    # Idempotent: second delete returns False but doesn't raise.
    assert store.delete_push_subscription("https://fcm.example/abc") is False


def test_upsert_replaces_keys_on_conflict(store):
    store.upsert_push_subscription(
        endpoint="https://x", p256dh="pk1", auth="auth1",
    )
    store.upsert_push_subscription(
        endpoint="https://x", p256dh="pk2", auth="auth2", user_agent="UA2",
    )
    subs = store.list_push_subscriptions()
    assert len(subs) == 1
    assert subs[0]["p256dh"] == "pk2"
    assert subs[0]["auth"] == "auth2"
    assert subs[0]["user_agent"] == "UA2"


def test_vapid_keys_round_trip(tmp_path, monkeypatch):
    """VAPID keys persist across loads — second call returns the same pair."""
    monkeypatch.setattr(
        "nexus.push.keys._KEYS_PATH", tmp_path / "push.json",
    )
    from nexus.push.keys import load_or_create
    a = load_or_create()
    b = load_or_create()
    assert a.public_key == b.public_key
    assert a.private_key == b.private_key
    # P-256 raw uncompressed point: 65 bytes → 87 chars urlsafe-b64 nopad
    assert len(a.public_key) == 87
