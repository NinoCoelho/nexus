"""Tests for workflow trigger drivers (webhook, fs_watch, schedule, event)."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from nexus.workflows.models import (
    StepConfig,
    StepType,
    TriggerConfig,
    TriggerType,
    WorkflowDef,
)
from nexus.workflows.store import WorkflowStore
from nexus.workflows.triggers.webhook import WebhookTriggerDriver
from nexus.workflows.triggers.event import EventTriggerListener


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    s = WorkflowStore(path)
    yield s
    s.close()


async def test_webhook_driver_registers_token(store):
    driver = WebhookTriggerDriver(store)
    wf = WorkflowDef(
        title="Test",
        triggers=[
            TriggerConfig(id="wh1", type=TriggerType.webhook),
        ],
    )
    await driver.start("test.md", wf)
    assert wf.triggers[0].token is not None
    result = store.lookup_webhook_token(wf.triggers[0].token)
    assert result == ("test.md", "wh1")


async def test_webhook_driver_skips_non_webhook(store):
    driver = WebhookTriggerDriver(store)
    wf = WorkflowDef(
        title="Test",
        triggers=[
            TriggerConfig(id="s1", type=TriggerType.schedule, cron="* * * * *"),
        ],
    )
    await driver.start("test.md", wf)
    assert len(list(store._conn.execute("SELECT * FROM webhook_tokens").fetchall())) == 0


async def test_webhook_driver_stop_removes_tokens(store):
    driver = WebhookTriggerDriver(store)
    wf = WorkflowDef(
        title="Test",
        triggers=[
            TriggerConfig(id="wh1", type=TriggerType.webhook, token="tok123"),
        ],
    )
    import datetime
    store.register_webhook_token("tok123", "test.md", "wh1", datetime.datetime.now(datetime.timezone.utc).isoformat())
    await driver.stop("test.md", "wh1")
    assert store.lookup_webhook_token("tok123") is None


async def test_event_listener_register_and_unregister(store):
    listener = EventTriggerListener(store)
    listener.register("w.md", "evt1", "vault.*", {"path": "invoices/**"})
    assert len(listener._registrations) == 1

    listener.unregister("w.md", "evt1")
    assert len(listener._registrations) == 0


async def test_event_listener_unregister_specific(store):
    listener = EventTriggerListener(store)
    listener.register("w.md", "evt1", "vault.*")
    listener.register("w.md", "evt2", "session.*")
    listener.unregister("w.md", "evt1")
    assert len(listener._registrations) == 1
    assert listener._registrations[0]["trigger_id"] == "evt2"


async def test_event_listener_filter_matching(store):
    listener = EventTriggerListener(store)
    assert listener._matches_filters(
        {"type": "vault.indexed", "path": "invoices/test.md"},
        {"path": "invoices/*"},
    )
    assert not listener._matches_filters(
        {"type": "vault.indexed", "path": "other/test.md"},
        {"path": "invoices/*"},
    )
    assert listener._matches_filters(
        {"type": "vault.indexed", "path": "invoices/test.md"},
        {},
    )


def test_trigger_config_to_dict():
    t = TriggerConfig(
        id="wh1",
        type=TriggerType.webhook,
        token="abc",
        path="~/Downloads",
        pattern="*.pdf",
        events=["created", "modified"],
        cron="0 9 * * 1-5",
        event="vault.updated",
        filter={"path": "invoices/**"},
    )
    d = t.to_dict()
    assert d["id"] == "wh1"
    assert d["type"] == "webhook"
    assert d["token"] == "abc"
    assert "path" not in d
    assert "pattern" not in d
    assert "events" not in d
    assert "cron" not in d
    assert "event" not in d
    assert "filter" not in d

    fsw = TriggerConfig(
        id="fs1",
        type=TriggerType.fs_watch,
        path="~/Downloads",
        pattern="*.pdf",
        events=["created", "modified"],
    )
    fd = fsw.to_dict()
    assert fd["path"] == "~/Downloads"
    assert fd["pattern"] == "*.pdf"
    assert fd["events"] == ["created", "modified"]
    assert "cron" not in fd
    assert "token" not in fd


def test_trigger_config_defaults():
    t = TriggerConfig(id="t1", type=TriggerType.manual)
    d = t.to_dict()
    assert d == {"id": "t1", "type": "manual"}


async def test_event_listener_start_stop(store):
    listener = EventTriggerListener(store)
    await listener.start()
    assert listener._task is not None
    assert not listener._task.done()
    await listener.stop()
    assert listener._task.done()
