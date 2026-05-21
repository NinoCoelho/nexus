"""Tests for workflow template expression resolution."""

from __future__ import annotations

from nexus.workflows.expressions import build_context, evaluate_condition, resolve_templates


def test_resolve_trigger_field():
    ctx = build_context(
        trigger_payload={"file_path": "/tmp/test.pdf", "event_type": "created"},
        step_outputs={},
        variables={},
    )
    result = resolve_templates("{{trigger.file_path}}", ctx)
    assert result == "/tmp/test.pdf"


def test_resolve_step_output():
    ctx = build_context(
        trigger_payload={},
        step_outputs={"extract": {"output": "OCR text here"}},
        variables={},
    )
    result = resolve_templates("{{steps.extract.output}}", ctx)
    assert result == "OCR text here"


def test_resolve_variable():
    ctx = build_context(
        trigger_payload={},
        step_outputs={},
        variables={"output_dir": "/tmp/out"},
    )
    result = resolve_templates("{{vars.output_dir}}", ctx)
    assert result == "/tmp/out"


def test_resolve_nested_path():
    ctx = build_context(
        trigger_payload={"body": {"user": {"name": "Alice"}}},
        step_outputs={},
        variables={},
    )
    result = resolve_templates("{{trigger.body.user.name}}", ctx)
    assert result == "Alice"


def test_resolve_missing_returns_original():
    ctx = build_context(trigger_payload={}, step_outputs={}, variables={})
    result = resolve_templates("{{trigger.missing}}", ctx)
    assert result == "{{trigger.missing}}"


def test_resolve_dict():
    ctx = build_context(
        trigger_payload={"name": "test.pdf"},
        step_outputs={},
        variables={"dir": "/out"},
    )
    result = resolve_templates(
        {"path": "{{vars.dir}}/{{trigger.name}}", "size": 100},
        ctx,
    )
    assert result == {"path": "/out/test.pdf", "size": 100}


def test_resolve_list():
    ctx = build_context(
        trigger_payload={"items": ["a", "b"]},
        step_outputs={},
        variables={},
    )
    result = resolve_templates(["{{trigger.items.0}}", "{{trigger.items.1}}"], ctx)
    assert result == ["a", "b"]


def test_resolve_non_string_passthrough():
    ctx = build_context(trigger_payload={}, step_outputs={}, variables={})
    assert resolve_templates(42, ctx) == 42
    assert resolve_templates(None, ctx) is None
    assert resolve_templates(True, ctx) is True


def test_evaluate_condition_true():
    ctx = build_context(
        trigger_payload={"amount": 100},
        step_outputs={},
        variables={},
    )
    assert evaluate_condition("trigger.amount > 0", ctx) is True


def test_evaluate_condition_false():
    ctx = build_context(
        trigger_payload={"amount": 0},
        step_outputs={},
        variables={},
    )
    assert evaluate_condition("trigger.amount > 0", ctx) is False


def test_evaluate_condition_template_true():
    ctx = build_context(
        trigger_payload={"status": "ok"},
        step_outputs={},
        variables={},
    )
    assert evaluate_condition("trigger.status", ctx) is True


def test_evaluate_condition_template_false():
    ctx = build_context(
        trigger_payload={"status": ""},
        step_outputs={},
        variables={},
    )
    assert evaluate_condition("trigger.status", ctx) is False


def test_mixed_expression():
    ctx = build_context(
        trigger_payload={"file_path": "/data/invoice.pdf"},
        step_outputs={"extract": {"output": {"text": "Hello", "pages": 3}}},
        variables={"prefix": "processed"},
    )
    result = resolve_templates("{{vars.prefix}}/{{trigger.file_path}} -> {{steps.extract.output.pages}} pages", ctx)
    assert result == "processed//data/invoice.pdf -> 3 pages"
