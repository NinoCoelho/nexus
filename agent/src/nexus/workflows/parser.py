"""Parse and serialize workflow vault files.

Workflow files are markdown with ``workflow-plugin: basic`` in frontmatter.
The frontmatter holds triggers, variables, and step definitions in YAML.
The body holds free-form documentation and run stats in HTML comments.
"""

from __future__ import annotations

from typing import Any

import yaml

from .models import (
    WORKFLOW_PLUGIN_KEY,
    StepConfig,
    StepType,
    TriggerConfig,
    TriggerType,
    WorkflowDef,
)


def _build_trigger(raw: dict[str, Any]) -> TriggerConfig:
    return TriggerConfig(
        id=str(raw.get("id", "")),
        type=TriggerType(raw.get("type", "manual")),
        token=raw.get("token"),
        secret=raw.get("secret"),
        allowed_methods=raw.get("allowed_methods", ["POST"]),
        path=raw.get("path"),
        pattern=raw.get("pattern", "*"),
        events=raw.get("events", ["created"]),
        debounce_ms=raw.get("debounce_ms", 1000),
        cron=raw.get("cron"),
        event=raw.get("event"),
        filter=raw.get("filter"),
    )


def _build_step(raw: dict[str, Any]) -> StepConfig:
    return StepConfig(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        type=StepType(raw.get("type", "tool_call")),
        tool=raw.get("tool"),
        input=raw.get("input"),
        prompt=raw.get("prompt"),
        model=raw.get("model"),
        background=bool(raw.get("background", False)),
        max_turns=int(raw.get("max_turns", 8)),
        mcp_server=raw.get("mcp_server"),
        mcp_tool=raw.get("mcp_tool"),
        url=raw.get("url"),
        method=str(raw.get("method", "GET")),
        headers=raw.get("headers"),
        body=raw.get("body"),
        expression=raw.get("expression"),
        then_step=raw.get("then_step"),
        else_step=raw.get("else_step"),
        template=raw.get("template"),
        output_format=str(raw.get("output_format", "text")),
        duration_seconds=int(raw.get("duration_seconds", 0)),
        condition=raw.get("condition"),
        on_error=str(raw.get("on_error", "stop")),
        retry_count=int(raw.get("retry_count", 0)),
        retry_delay_seconds=int(raw.get("retry_delay_seconds", 5)),
        next_step=raw.get("next_step"),
    )


def parse(content: str) -> WorkflowDef:
    frontmatter: dict[str, Any] = {}
    body = ""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            try:
                parsed = yaml.safe_load(content[3:end]) or {}
                if isinstance(parsed, dict):
                    frontmatter = parsed
            except yaml.YAMLError:
                pass
            body = content[end + 4:].lstrip("\n")

    triggers = [_build_trigger(t) for t in frontmatter.get("triggers", []) if isinstance(t, dict)]
    steps = [_build_step(s) for s in frontmatter.get("steps", []) if isinstance(s, dict)]
    variables = frontmatter.get("variables") or {}
    if not isinstance(variables, dict):
        variables = {}

    title = ""
    description_lines: list[str] = []
    in_description = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            in_description = True
            continue
        if stripped.startswith("<!-- nx:"):
            continue
        if in_description and stripped:
            description_lines.append(stripped)
        elif in_description and not stripped and description_lines:
            description_lines.append("")

    wf = WorkflowDef(
        title=title or "Untitled Workflow",
        enabled=bool(frontmatter.get("enabled", True)),
        triggers=triggers,
        variables={str(k): str(v) for k, v in variables.items()},
        steps=steps,
        description="\n".join(description_lines).strip(),
    )
    return wf


def serialize(wf: WorkflowDef, original_content: str | None = None) -> str:
    fm: dict[str, Any] = {WORKFLOW_PLUGIN_KEY: "basic"}
    fm["enabled"] = wf.enabled
    if wf.triggers:
        fm["triggers"] = [t.to_dict() for t in wf.triggers]
    if wf.variables:
        fm["variables"] = dict(wf.variables)
    if wf.steps:
        fm["steps"] = [s.to_dict() for s in wf.steps]

    body_lines: list[str] = []
    if original_content:
        body_lines.append("")
        if original_content.startswith("---"):
            end = original_content.find("\n---", 3)
            if end != -1:
                existing_body = original_content[end + 4:].lstrip("\n")
                body_lines = [existing_body]
        else:
            body_lines = [original_content]
    else:
        body_lines = ["", f"# {wf.title}", ""]
        if wf.description:
            body_lines.append(wf.description)
            body_lines.append("")

    fm_text = yaml.dump(fm, default_flow_style=False, sort_keys=False).rstrip()
    parts = [f"---\n{fm_text}\n---"]
    if body_lines:
        body_str = "\n".join(body_lines)
        if body_str.strip():
            parts.append(body_str.rstrip())
    return "\n".join(parts).rstrip() + "\n"
