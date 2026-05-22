"""Data models for workflow definitions, triggers, steps, and run state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import yaml

WORKFLOW_PLUGIN_KEY = "workflow-plugin"


class TriggerType(str, Enum):
    webhook = "webhook"
    fs_watch = "fs_watch"
    schedule = "schedule"
    manual = "manual"
    event = "event"


class StepType(str, Enum):
    tool_call = "tool_call"
    agent_session = "agent_session"
    mcp_call = "mcp_call"
    http_request = "http_request"
    condition = "condition"
    transform = "transform"
    delay = "delay"


class AuthType(str, Enum):
    none = "none"
    basic = "basic"
    apikey = "apikey"
    oauth = "oauth"


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class StepRunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


@dataclass
class TriggerConfig:
    id: str
    type: TriggerType
    token: str | None = None
    secret: str | None = None
    allowed_methods: list[str] = field(default_factory=lambda: ["POST"])
    path: str | None = None
    pattern: str = "*"
    events: list[str] = field(default_factory=lambda: ["created"])
    debounce_ms: int = 1000
    cron: str | None = None
    event: str | None = None
    filter: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "type": self.type.value}
        if self.token is not None:
            out["token"] = self.token
        if self.secret is not None:
            out["secret"] = self.secret
        if self.allowed_methods != ["POST"]:
            out["allowed_methods"] = list(self.allowed_methods)
        if self.path is not None:
            out["path"] = self.path
        if self.pattern != "*":
            out["pattern"] = self.pattern
        if self.events != ["created"]:
            out["events"] = list(self.events)
        if self.debounce_ms != 1000:
            out["debounce_ms"] = self.debounce_ms
        if self.cron is not None:
            out["cron"] = self.cron
        if self.event is not None:
            out["event"] = self.event
        if self.filter is not None:
            out["filter"] = self.filter
        return out


@dataclass
class StepConfig:
    id: str
    name: str
    type: StepType
    slug: str | None = None
    tool: str | None = None
    input: dict[str, Any] | None = None
    prompt: str | None = None
    model: str | None = None
    background: bool = False
    max_turns: int = 8
    mcp_server: str | None = None
    mcp_tool: str | None = None
    url: str | None = None
    method: str = "GET"
    headers: dict[str, str] | None = None
    body: Any = None
    auth_type: str = "none"
    auth_credential: str | None = None
    auth_username: str | None = None
    auth_password_credential: str | None = None
    auth_header_name: str | None = None
    auth_prefix: str = "Bearer"
    auth_query_name: str | None = None
    auth_location: str = "header"
    custom_headers: dict[str, str] | None = None
    expression: str | None = None
    then_step: str | None = None
    else_step: str | None = None
    template: str | None = None
    output_format: str = "text"
    duration_seconds: int = 0
    condition: str | None = None
    on_error: str = "stop"
    retry_count: int = 0
    retry_delay_seconds: int = 5

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "name": self.name, "type": self.type.value}
        if self.slug is not None:
            out["slug"] = self.slug
        if self.tool is not None:
            out["tool"] = self.tool
        if self.input is not None:
            out["input"] = self.input
        if self.prompt is not None:
            out["prompt"] = self.prompt
        if self.model is not None:
            out["model"] = self.model
        if self.background:
            out["background"] = True
        if self.max_turns != 8:
            out["max_turns"] = self.max_turns
        if self.mcp_server is not None:
            out["mcp_server"] = self.mcp_server
        if self.mcp_tool is not None:
            out["mcp_tool"] = self.mcp_tool
        if self.url is not None:
            out["url"] = self.url
        if self.method != "GET":
            out["method"] = self.method
        if self.headers is not None:
            out["headers"] = self.headers
        if self.body is not None:
            out["body"] = self.body
        if self.auth_type != "none":
            out["auth_type"] = self.auth_type
        if self.auth_credential is not None:
            out["auth_credential"] = self.auth_credential
        if self.auth_username is not None:
            out["auth_username"] = self.auth_username
        if self.auth_password_credential is not None:
            out["auth_password_credential"] = self.auth_password_credential
        if self.auth_header_name is not None:
            out["auth_header_name"] = self.auth_header_name
        if self.auth_prefix != "Bearer":
            out["auth_prefix"] = self.auth_prefix
        if self.auth_query_name is not None:
            out["auth_query_name"] = self.auth_query_name
        if self.auth_location != "header":
            out["auth_location"] = self.auth_location
        if self.custom_headers is not None:
            out["custom_headers"] = self.custom_headers
        if self.expression is not None:
            out["expression"] = self.expression
        if self.then_step is not None:
            out["then_step"] = self.then_step
        if self.else_step is not None:
            out["else_step"] = self.else_step
        if self.template is not None:
            out["template"] = self.template
        if self.output_format != "text":
            out["output_format"] = self.output_format
        if self.duration_seconds != 0:
            out["duration_seconds"] = self.duration_seconds
        if self.condition is not None:
            out["condition"] = self.condition
        if self.on_error != "stop":
            out["on_error"] = self.on_error
        if self.retry_count != 0:
            out["retry_count"] = self.retry_count
        if self.retry_delay_seconds != 5:
            out["retry_delay_seconds"] = self.retry_delay_seconds
        return out


@dataclass
class WorkflowDef:
    title: str = "Untitled Workflow"
    enabled: bool = True
    triggers: list[TriggerConfig] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    steps: list[StepConfig] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "enabled": self.enabled,
            "triggers": [t.to_dict() for t in self.triggers],
            "variables": dict(self.variables),
            "steps": [s.to_dict() for s in self.steps],
            "description": self.description,
        }


@dataclass
class WorkflowRun:
    id: str
    workflow_path: str
    trigger_id: str
    trigger_type: TriggerType
    trigger_payload: dict[str, Any]
    status: RunStatus
    started_at: str
    finished_at: str | None = None
    current_step: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "workflow_path": self.workflow_path,
            "trigger_id": self.trigger_id,
            "trigger_type": self.trigger_type.value,
            "trigger_payload": self.trigger_payload,
            "status": self.status.value,
            "started_at": self.started_at,
        }
        if self.finished_at is not None:
            out["finished_at"] = self.finished_at
        if self.current_step is not None:
            out["current_step"] = self.current_step
        if self.error is not None:
            out["error"] = self.error
        return out


@dataclass
class StepRun:
    run_id: str
    step_id: str
    status: StepRunStatus
    input_resolved: dict[str, Any] | None = None
    output: Any = None
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "step_id": self.step_id,
            "status": self.status.value,
        }
        if self.input_resolved is not None:
            out["input_resolved"] = self.input_resolved
        if self.output is not None:
            out["output"] = self.output
        if self.error is not None:
            out["error"] = self.error
        if self.started_at is not None:
            out["started_at"] = self.started_at
        if self.finished_at is not None:
            out["finished_at"] = self.finished_at
        return out


def is_workflow_file(content: str) -> bool:
    if not content.startswith("---"):
        return False
    end = content.find("\n---", 3)
    if end == -1:
        return False
    try:
        fm = yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError:
        return False
    return isinstance(fm, dict) and WORKFLOW_PLUGIN_KEY in fm
