"""Integration tests for workflow API routes.

Tests the full stack: vault file → parser → store → engine → API endpoints.
Uses a real FastAPI TestClient with mocked vault and in-memory SQLite.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workflow_env(tmp_path, monkeypatch):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setattr("nexus.vault._VAULT_ROOT", None)
    monkeypatch.setattr("nexus.home._ROOT", tmp_path)
    monkeypatch.setattr("nexus.home._USER_HOME_DIR", __import__("contextvars").ContextVar("x", default=None))

    from nexus.workflows.store import WorkflowStore
    from nexus.workflows.engine import WorkflowEngine
    from nexus.workflows.models import TriggerType

    db_path = str(tmp_path / "workflow_runs.sqlite")
    store = WorkflowStore(db_path)
    engine = WorkflowEngine(store)

    from nexus.server.routes import workflows as wf_routes
    wf_routes._STORE = store
    wf_routes._ENGINE = engine

    yield {"store": store, "engine": engine, "vault_dir": vault_dir, "tmp": tmp_path}

    store.close()


def _write_workflow(vault_dir, path, content):
    full = vault_dir / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


_SAMPLE_WF = """\
---
workflow-plugin: basic
enabled: true
triggers:
  - id: manual1
    type: manual
steps:
  - id: step1
    name: Greeting
    type: transform
    template: "Hello World"
---

# Test Workflow

A test workflow.
"""


def test_list_workflows_empty(workflow_env):
    from nexus.server.routes.workflows import _scan_workflows

    class FakeReq:
        class State:
            pass
        app = type("App", (), {"state": State()})()

    result = _scan_workflows(FakeReq())
    assert result == []


def test_list_workflows_with_file(workflow_env):
    _write_workflow(workflow_env["vault_dir"], "workflows/test.md", _SAMPLE_WF)

    from nexus.server.routes.workflows import _scan_workflows

    class FakeReq:
        class State:
            pass
        app = type("App", (), {"state": State()})()

    result = _scan_workflows(FakeReq())
    assert len(result) == 1
    assert result[0]["title"] == "Test Workflow"
    assert result[0]["step_count"] == 1
    assert result[0]["trigger_count"] == 1


async def test_engine_run_with_transform(workflow_env):
    engine = workflow_env["engine"]
    from nexus.workflows.models import StepConfig, StepType, TriggerType, WorkflowDef

    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(id="t1", name="Greet", type=StepType.transform, template="Hello {{trigger.name}}"),
        ],
    )
    run = await engine.run_workflow(
        "test.md", "manual", TriggerType.manual, {"name": "World"}, wf_def=wf,
    )
    assert run.status.value == "completed"
    steps = engine.store.list_step_runs(run.id)
    assert steps[0].output["result"] == "Hello World"


async def test_engine_run_condition_branch(workflow_env):
    engine = workflow_env["engine"]
    from nexus.workflows.models import RunStatus, StepConfig, StepType, TriggerType, WorkflowDef

    wf = WorkflowDef(
        title="Test",
        variables={"threshold": "10"},
        steps=[
            StepConfig(
                id="check",
                name="Check",
                type=StepType.condition,
                expression="trigger.value > 5",
                then_step="high",
                else_step="low",
            ),
            StepConfig(id="high", name="High", type=StepType.transform, template="high"),
            StepConfig(id="low", name="Low", type=StepType.transform, template="low"),
        ],
    )
    run = await engine.run_workflow(
        "test.md", "manual", TriggerType.manual, {"value": 15}, wf_def=wf,
    )
    assert run.status == RunStatus.completed
    steps = engine.store.list_step_runs(run.id)
    step_ids = [s.step_id for s in steps if s.status.value == "completed"]
    assert "high" in step_ids


async def test_engine_run_with_delay(workflow_env):
    engine = workflow_env["engine"]
    from nexus.workflows.models import StepConfig, StepType, TriggerType, WorkflowDef

    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(id="d1", name="Wait", type=StepType.delay, duration_seconds=0),
            StepConfig(id="d2", name="After", type=StepType.transform, template="done"),
        ],
    )
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status.value == "completed"
    steps = engine.store.list_step_runs(run.id)
    assert len(steps) == 2


def test_store_cleanup_old_runs(workflow_env):
    store = workflow_env["store"]
    from nexus.workflows.models import RunStatus, TriggerType, WorkflowRun

    run = WorkflowRun(
        id="old-run",
        workflow_path="test.md",
        trigger_id="manual",
        trigger_type=TriggerType.manual,
        trigger_payload={},
        status=RunStatus.completed,
        started_at="2020-01-01T00:00:00",
        finished_at="2020-01-01T00:01:00",
    )
    store.create_run(run)
    deleted = store.cleanup_old_runs(days=1)
    assert deleted == 1
    assert store.get_run("old-run") is None


def test_webhook_token_lookup(workflow_env):
    store = workflow_env["store"]
    store.register_webhook_token("abc123", "w.md", "wh1", "2026-01-01T00:00:00")
    result = store.lookup_webhook_token("abc123")
    assert result == ("w.md", "wh1")
    assert store.lookup_webhook_token("missing") is None


def test_run_to_dict_roundtrip(workflow_env):
    from nexus.workflows.models import RunStatus, TriggerType, WorkflowRun

    run = WorkflowRun(
        id="r1",
        workflow_path="w.md",
        trigger_id="wh1",
        trigger_type=TriggerType.webhook,
        trigger_payload={"body": {"test": True}},
        status=RunStatus.running,
        started_at="2026-01-01T00:00:00",
    )
    d = run.to_dict()
    assert d["trigger_type"] == "webhook"
    assert d["trigger_payload"] == {"body": {"test": True}}

    store = workflow_env["store"]
    store.create_run(run)
    loaded = store.get_run("r1")
    assert loaded is not None
    assert loaded.trigger_payload == {"body": {"test": True}}
