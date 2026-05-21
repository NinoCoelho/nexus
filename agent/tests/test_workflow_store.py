"""Tests for workflow run store (SQLite)."""

from __future__ import annotations

import tempfile
import os

from nexus.workflows.models import (
    RunStatus,
    StepRun,
    StepRunStatus,
    TriggerType,
    WorkflowRun,
)
from nexus.workflows.store import WorkflowStore


def _store() -> WorkflowStore:
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    store = WorkflowStore(path)
    return store


def test_create_and_get_run():
    store = _store()
    try:
        run = WorkflowRun(
            id="run-1",
            workflow_path="workflows/test.md",
            trigger_id="manual",
            trigger_type=TriggerType.manual,
            trigger_payload={"test": True},
            status=RunStatus.running,
            started_at="2026-01-01T00:00:00",
        )
        store.create_run(run)
        got = store.get_run("run-1")
        assert got is not None
        assert got.id == "run-1"
        assert got.workflow_path == "workflows/test.md"
        assert got.status == RunStatus.running
        assert got.trigger_payload == {"test": True}
    finally:
        store.close()


def test_update_run():
    store = _store()
    try:
        run = WorkflowRun(
            id="run-2",
            workflow_path="w.md",
            trigger_id="wh1",
            trigger_type=TriggerType.webhook,
            trigger_payload={},
            status=RunStatus.running,
            started_at="2026-01-01T00:00:00",
        )
        store.create_run(run)
        run.status = RunStatus.completed
        run.finished_at = "2026-01-01T00:01:00"
        store.update_run(run)
        got = store.get_run("run-2")
        assert got is not None
        assert got.status == RunStatus.completed
        assert got.finished_at == "2026-01-01T00:01:00"
    finally:
        store.close()


def test_list_runs():
    store = _store()
    try:
        for i in range(5):
            run = WorkflowRun(
                id=f"run-{i}",
                workflow_path="w.md",
                trigger_id="manual",
                trigger_type=TriggerType.manual,
                trigger_payload={},
                status=RunStatus.completed,
                started_at=f"2026-01-0{i+1}T00:00:00",
                finished_at=f"2026-01-0{i+1}T00:01:00",
            )
            store.create_run(run)
        runs = store.list_runs("w.md", limit=3)
        assert len(runs) == 3
        runs2 = store.list_runs("w.md", limit=3, offset=3)
        assert len(runs2) == 2
    finally:
        store.close()


def test_step_runs():
    store = _store()
    try:
        run = WorkflowRun(
            id="run-sr",
            workflow_path="w.md",
            trigger_id="manual",
            trigger_type=TriggerType.manual,
            trigger_payload={},
            status=RunStatus.running,
            started_at="2026-01-01T00:00:00",
        )
        store.create_run(run)

        sr1 = StepRun(
            run_id="run-sr",
            step_id="step-1",
            status=StepRunStatus.completed,
            input_resolved={"path": "/tmp/test.pdf"},
            output={"text": "OCR result"},
            started_at="2026-01-01T00:00:01",
            finished_at="2026-01-01T00:00:05",
        )
        store.create_step_run(sr1)

        sr2 = StepRun(
            run_id="run-sr",
            step_id="step-2",
            status=StepRunStatus.running,
            started_at="2026-01-01T00:00:05",
        )
        store.create_step_run(sr2)

        steps = store.list_step_runs("run-sr")
        assert len(steps) == 2
        assert steps[0].step_id == "step-1"
        assert steps[0].status == StepRunStatus.completed
        assert steps[0].output == {"text": "OCR result"}
        assert steps[1].step_id == "step-2"
        assert steps[1].status == StepRunStatus.running
    finally:
        store.close()


def test_webhook_tokens():
    store = _store()
    try:
        store.register_webhook_token("tok123", "w.md", "wh1", "2026-01-01T00:00:00")
        result = store.lookup_webhook_token("tok123")
        assert result == ("w.md", "wh1")
        assert store.lookup_webhook_token("nonexistent") is None

        store.remove_webhook_tokens("w.md")
        assert store.lookup_webhook_token("tok123") is None
    finally:
        store.close()


def test_to_dict():
    run = WorkflowRun(
        id="run-d",
        workflow_path="w.md",
        trigger_id="manual",
        trigger_type=TriggerType.manual,
        trigger_payload={"key": "val"},
        status=RunStatus.completed,
        started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:01:00",
    )
    d = run.to_dict()
    assert d["id"] == "run-d"
    assert d["status"] == "completed"
    assert d["trigger_payload"] == {"key": "val"}

    sr = StepRun(
        run_id="run-d",
        step_id="s1",
        status=StepRunStatus.completed,
        output={"result": "ok"},
    )
    sd = sr.to_dict()
    assert sd["step_id"] == "s1"
    assert sd["output"] == {"result": "ok"}
