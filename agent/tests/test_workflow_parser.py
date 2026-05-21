"""Tests for workflow vault file parsing and serialization."""

from __future__ import annotations

from nexus.workflows import parser
from nexus.workflows.models import (
    WORKFLOW_PLUGIN_KEY,
    StepType,
    TriggerType,
    WorkflowDef,
    is_workflow_file,
)


_SAMPLE_WORKFLOW = """\
---
workflow-plugin: basic
enabled: true
triggers:
  - id: wh1
    type: webhook
  - id: sched1
    type: schedule
    cron: "0 9 * * 1-5"
variables:
  output_dir: ~/processed
steps:
  - id: extract
    name: Extract Text
    type: tool_call
    tool: ocr_image
    input:
      path: "{{trigger.file_path}}"
  - id: analyze
    name: Analyze Invoice
    type: agent_session
    prompt: "Analyze: {{steps.extract.output}}"
  - id: save
    name: Save to Vault
    type: tool_call
    tool: vault_write
    input:
      path: "{{vars.output_dir}}/invoice.md"
      content: "{{steps.analyze.output}}"
    condition: "{{steps.analyze.output.amount}} > 0"
---

# Invoice Processing Pipeline

Processes incoming PDF invoices automatically.

<!-- nx:runs=47 -->
"""


def test_parse_basic():
    wf = parser.parse(_SAMPLE_WORKFLOW)
    assert wf.title == "Invoice Processing Pipeline"
    assert wf.enabled is True
    assert len(wf.triggers) == 2
    assert len(wf.steps) == 3
    assert wf.variables == {"output_dir": "~/processed"}


def test_parse_triggers():
    wf = parser.parse(_SAMPLE_WORKFLOW)
    t1 = wf.triggers[0]
    assert t1.id == "wh1"
    assert t1.type == TriggerType.webhook

    t2 = wf.triggers[1]
    assert t2.id == "sched1"
    assert t2.type == TriggerType.schedule
    assert t2.cron == "0 9 * * 1-5"


def test_parse_steps():
    wf = parser.parse(_SAMPLE_WORKFLOW)
    s1 = wf.steps[0]
    assert s1.id == "extract"
    assert s1.type == StepType.tool_call
    assert s1.tool == "ocr_image"
    assert s1.input == {"path": "{{trigger.file_path}}"}

    s2 = wf.steps[1]
    assert s2.id == "analyze"
    assert s2.type == StepType.agent_session
    assert s2.prompt == "Analyze: {{steps.extract.output}}"

    s3 = wf.steps[2]
    assert s3.id == "save"
    assert s3.condition == "{{steps.analyze.output.amount}} > 0"


def test_roundtrip():
    wf = parser.parse(_SAMPLE_WORKFLOW)
    md = parser.serialize(wf, original_content=_SAMPLE_WORKFLOW)
    wf2 = parser.parse(md)
    assert wf2.title == wf.title
    assert wf2.enabled == wf.enabled
    assert len(wf2.triggers) == len(wf.triggers)
    assert len(wf2.steps) == len(wf.steps)
    assert wf2.variables == wf.variables


def test_roundtrip_preserves_body():
    wf = parser.parse(_SAMPLE_WORKFLOW)
    md = parser.serialize(wf, original_content=_SAMPLE_WORKFLOW)
    assert "Invoice Processing Pipeline" in md
    assert "processes incoming PDF invoices" in md.lower() or "Processes incoming" in md


def test_serialize_new_workflow():
    wf = WorkflowDef(title="Test Workflow", enabled=True)
    md = parser.serialize(wf)
    assert "workflow-plugin: basic" in md
    assert "# Test Workflow" in md
    wf2 = parser.parse(md)
    assert wf2.title == "Test Workflow"


def test_parse_empty_workflow():
    md = "---\nworkflow-plugin: basic\n---\n\n# Empty\n"
    wf = parser.parse(md)
    assert wf.title == "Empty"
    assert wf.enabled is True
    assert len(wf.triggers) == 0
    assert len(wf.steps) == 0


def test_parse_disabled():
    md = "---\nworkflow-plugin: basic\nenabled: false\n---\n\n# Disabled\n"
    wf = parser.parse(md)
    assert wf.enabled is False


def test_is_workflow_file():
    assert is_workflow_file(_SAMPLE_WORKFLOW) is True
    assert is_workflow_file("---\nkanban-plugin: basic\n---\n") is False
    assert is_workflow_file("just regular markdown") is False
    assert is_workflow_file("---\nno plugin\n---\n") is False


def test_parse_fs_watch_trigger():
    md = """\
---
workflow-plugin: basic
triggers:
  - id: fs1
    type: fs_watch
    path: ~/Downloads
    pattern: "*.pdf"
    events: [created, modified]
    debounce_ms: 2000
---
"""
    wf = parser.parse(md)
    t = wf.triggers[0]
    assert t.type == TriggerType.fs_watch
    assert t.path == "~/Downloads"
    assert t.pattern == "*.pdf"
    assert t.events == ["created", "modified"]
    assert t.debounce_ms == 2000


def test_parse_event_trigger():
    md = """\
---
workflow-plugin: basic
triggers:
  - id: evt1
    type: event
    event: vault.updated
    filter:
      path: "invoices/**"
---
"""
    wf = parser.parse(md)
    t = wf.triggers[0]
    assert t.type == TriggerType.event
    assert t.event == "vault.updated"
    assert t.filter == {"path": "invoices/**"}


def test_to_dict():
    wf = parser.parse(_SAMPLE_WORKFLOW)
    d = wf.to_dict()
    assert d["title"] == "Invoice Processing Pipeline"
    assert len(d["triggers"]) == 2
    assert len(d["steps"]) == 3
    assert d["enabled"] is True
    assert d["variables"]["output_dir"] == "~/processed"
