"""HITL event log — write-on-publish, status transitions, retention.

The log is the durable side of HITL: it backs the bell/notification
center so prompts that fired (and possibly timed out) while no UI was
attached are still visible after the in-memory pending future is gone.
"""

from __future__ import annotations

import asyncio

import pytest

from nexus.server.events import SessionEvent
from nexus.server.session_store import SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(db_path=tmp_path / "sessions.sqlite")


def test_publish_user_request_creates_pending_row(store):
    store.publish(
        "sess-a",
        SessionEvent(
            kind="user_request",
            data={
                "request_id": "r1",
                "prompt": "Approve?",
                "kind": "confirm",
                "timeout_seconds": 60,
            },
        ),
    )
    rows = store.list_hitl_events()
    assert len(rows) == 1
    assert rows[0]["request_id"] == "r1"
    assert rows[0]["status"] == "pending"
    assert rows[0]["session_id"] == "sess-a"
    assert rows[0]["prompt"] == "Approve?"
    assert rows[0]["kind"] == "confirm"
    assert rows[0]["resolved_at"] is None


async def test_resolve_pending_marks_answered(store):
    """End-to-end: broker.ask → resolve → row flips to 'answered'."""
    async def driver():
        return await store.broker.ask(
            "sess-b", "Pick one?", kind="confirm", timeout_seconds=10,
        )

    task = asyncio.create_task(driver())
    await asyncio.sleep(0.05)
    rid = next(rid for (sid, rid) in store.broker._requests if sid == "sess-b")

    assert store.resolve_pending("sess-b", rid, "yes") is True
    assert await task == "yes"

    rows = store.list_hitl_events()
    assert rows[0]["status"] == "answered"
    assert rows[0]["answer"] == "yes"
    assert rows[0]["resolved_at"] is not None


def test_user_request_cancelled_with_timeout_reason_marks_timed_out(store):
    store.publish(
        "sess-c",
        SessionEvent(
            kind="user_request",
            data={"request_id": "r2", "prompt": "?", "kind": "confirm",
                  "timeout_seconds": 30},
        ),
    )
    store.publish(
        "sess-c",
        SessionEvent(
            kind="user_request_cancelled",
            data={"request_id": "r2", "reason": "timeout"},
        ),
    )
    row = store.list_hitl_events()[0]
    assert row["status"] == "timed_out"
    assert row["reason"] == "timeout"
    assert row["resolved_at"] is not None


def test_user_request_cancelled_without_timeout_marks_cancelled(store):
    store.publish(
        "sess-c",
        SessionEvent(
            kind="user_request",
            data={"request_id": "r3", "prompt": "?", "kind": "confirm",
                  "timeout_seconds": 30},
        ),
    )
    store.publish(
        "sess-c",
        SessionEvent(
            kind="user_request_cancelled",
            data={"request_id": "r3", "reason": "session_reset"},
        ),
    )
    row = store.list_hitl_events()[0]
    assert row["status"] == "cancelled"
    assert row["reason"] == "session_reset"


def test_user_request_auto_records_auto_answered_row(store):
    store.publish(
        "sess-d",
        SessionEvent(
            kind="user_request_auto",
            data={
                "request_id": "auto-1", "prompt": "go?", "kind": "confirm",
                "answer": "yes", "reason": "yolo",
            },
        ),
    )
    row = store.list_hitl_events()[0]
    assert row["status"] == "auto_answered"
    assert row["answer"] == "yes"
    assert row["reason"] == "yolo"


def test_history_is_ordered_newest_first(store):
    for i in range(3):
        store.publish(
            "sess-e",
            SessionEvent(
                kind="user_request",
                data={"request_id": f"r-{i}", "prompt": f"q{i}",
                      "kind": "confirm", "timeout_seconds": 30},
            ),
        )
    rows = store.list_hitl_events()
    assert [r["request_id"] for r in rows] == ["r-2", "r-1", "r-0"]


def test_form_payload_round_trips(store):
    store.publish(
        "sess-f",
        SessionEvent(
            kind="user_request",
            data={
                "request_id": "f1",
                "prompt": "Fill in",
                "kind": "form",
                "fields": [{"name": "a", "type": "text", "label": "A"}],
                "form_title": "T",
                "form_description": "D",
                "timeout_seconds": 60,
            },
        ),
    )
    row = store.list_hitl_events()[0]
    assert row["fields"] == [{"name": "a", "type": "text", "label": "A"}]
    assert row["form_title"] == "T"
    assert row["form_description"] == "D"
