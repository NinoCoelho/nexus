"""Tests for the workflow execution engine."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from nexus.workflows.engine import WorkflowEngine
from nexus.workflows.models import (
    RunStatus,
    StepConfig,
    StepRunStatus,
    StepType,
    TriggerType,
    WorkflowDef,
)
from nexus.workflows.store import WorkflowStore


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    s = WorkflowStore(path)
    yield s
    s.close()


@pytest.fixture
def engine(store):
    return WorkflowEngine(store)


def _simple_workflow() -> WorkflowDef:
    return WorkflowDef(
        title="Test",
        enabled=True,
        steps=[
            StepConfig(
                id="delay1",
                name="Wait",
                type=StepType.delay,
                duration_seconds=0,
            ),
        ],
    )


async def test_run_simple_workflow(engine):
    wf = _simple_workflow()
    run = await engine.run_workflow(
        workflow_path="test.md",
        trigger_id="manual",
        trigger_type=TriggerType.manual,
        trigger_payload={},
        wf_def=wf,
    )
    assert run.status == RunStatus.completed
    assert run.finished_at is not None

    step_runs = engine.store.list_step_runs(run.id)
    assert len(step_runs) == 1
    assert step_runs[0].status == StepRunStatus.completed


async def test_condition_skip(engine):
    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(
                id="s1",
                name="Step 1",
                type=StepType.delay,
                duration_seconds=0,
                condition="false",
            ),
            StepConfig(
                id="s2",
                name="Step 2",
                type=StepType.delay,
                duration_seconds=0,
            ),
        ],
    )
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status == RunStatus.completed
    steps = engine.store.list_step_runs(run.id)
    assert steps[0].status == StepRunStatus.skipped
    assert steps[1].status == StepRunStatus.completed


async def test_error_stop(engine):
    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(
                id="bad",
                name="Bad Step",
                type=StepType.tool_call,
                tool="nonexistent_tool_xyz",
                on_error="stop",
            ),
            StepConfig(
                id="after",
                name="After",
                type=StepType.delay,
                duration_seconds=0,
            ),
        ],
    )
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status == RunStatus.failed
    steps = engine.store.list_step_runs(run.id)
    assert steps[0].status == StepRunStatus.failed
    assert len(steps) == 1


async def test_error_continue(engine):
    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(
                id="bad",
                name="Bad Step",
                type=StepType.tool_call,
                tool="nonexistent_tool_xyz",
                on_error="continue",
            ),
            StepConfig(
                id="after",
                name="After",
                type=StepType.delay,
                duration_seconds=0,
            ),
        ],
    )
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status == RunStatus.completed
    steps = engine.store.list_step_runs(run.id)
    assert steps[0].status == StepRunStatus.failed
    assert steps[1].status == StepRunStatus.completed


async def test_transform_step(engine):
    wf = WorkflowDef(
        title="Test",
        variables={"name": "World"},
        steps=[
            StepConfig(
                id="t1",
                name="Greeting",
                type=StepType.transform,
                template="Hello {{vars.name}}!",
            ),
        ],
    )
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status == RunStatus.completed
    steps = engine.store.list_step_runs(run.id)
    assert steps[0].output == {"result": "Hello World!"}


async def test_delay_step(engine):
    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(
                id="d1",
                name="Wait",
                type=StepType.delay,
                duration_seconds=0,
            ),
        ],
    )
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status == RunStatus.completed
    steps = engine.store.list_step_runs(run.id)
    assert steps[0].output == {"waited_seconds": 0}


async def test_disabled_workflow_not_blocked(engine):
    wf = WorkflowDef(title="Disabled", enabled=False, steps=[
        StepConfig(id="s1", name="S1", type=StepType.delay, duration_seconds=0),
    ])
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status == RunStatus.completed


async def test_run_history_stored(engine, store):
    wf = _simple_workflow()
    await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    runs = store.list_runs("test.md")
    assert len(runs) == 2


async def test_trigger_payload_available(engine):
    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(
                id="t1",
                name="Transform",
                type=StepType.transform,
                template="File: {{trigger.file_path}}",
            ),
        ],
    )
    run = await engine.run_workflow(
        "test.md", "wh1", TriggerType.webhook,
        {"file_path": "/tmp/doc.pdf"}, wf_def=wf,
    )
    assert run.status == RunStatus.completed
    steps = engine.store.list_step_runs(run.id)
    assert steps[0].output == {"result": "File: /tmp/doc.pdf"}


async def test_step_output_chaining(engine):
    wf = WorkflowDef(
        title="Test",
        steps=[
            StepConfig(
                id="s1",
                name="Step 1",
                type=StepType.transform,
                template="result-data",
            ),
            StepConfig(
                id="s2",
                name="Step 2",
                type=StepType.transform,
                template="Got: {{steps.s1.result}}",
            ),
        ],
    )
    run = await engine.run_workflow("test.md", "manual", TriggerType.manual, {}, wf_def=wf)
    assert run.status == RunStatus.completed
    steps = engine.store.list_step_runs(run.id)
    assert steps[1].output == {"result": "Got: result-data"}
