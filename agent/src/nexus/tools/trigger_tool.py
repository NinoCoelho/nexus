"""manage_triggers agent tool.

Lets the agent create, update, pause, resume, run-now, and delete
workflow triggers (schedules and webhooks) on vault-stored WorkflowDefs
(``workflow-plugin: basic`` markdown files). This is the agent-facing
surface of the trigger subsystem: a bot can set up "every weekday at 9,
send me an inbox summary" entirely inside a turn, without touching the UI.

Webhook tokens/secrets are never returned to the agent: ``trigger_info``
and ``trigger`` results carry only ids, types, and schedules — the person
copies any webhook URL from the UI card or the Triggers page.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from collections.abc import Callable
from typing import Any

from .. import vault as _vault
from ..agent.llm import ToolSpec
from ..workflows import parser as wf_parser
from ..workflows.models import (
    TriggerConfig,
    TriggerType,
    WorkflowDef,
)

log = logging.getLogger(__name__)


TRIGGER_INFO_TOOL = ToolSpec(
    name="trigger_info",
    description=(
        "List every workflow trigger (schedule, webhook, app event, fs_watch, "
        "event, rss) across the vault, with id, type, schedule/cron, enabled "
        "state, and owning workflow path. Webhook tokens and secrets are "
        "never included — they are shown only in the UI Triggers page."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)

TRIGGER_TOOL = ToolSpec(
    name="trigger",
    description=(
        "Create, update, pause, resume, or immediately run a workflow "
        "trigger. Actions: create | update | pause | resume | run_now. "
        "For create/update you must supply ``workflow_path`` (vault .md "
        "path) and ``trigger`` with at least ``type`` (schedule|webhook|"
        "app_event|fs_watch|event|rss) plus type-specific fields "
        "(cron for schedule, path/events for fs_watch, event for event, "
        "url for rss). A new schedule trigger fires on its cron; a new "
        "webhook trigger gets a token shown only to the person via the "
        "UI card — not returned here. run_now fires a workflow once "
        "immediately on a hidden session (origin=trigger, never appears "
        "in chat lists). Pause/resume toggles the whole workflow's "
        "enabled flag. Run limit: max_active_minutes stops stuck "
        "workflows; max_runs caps total executions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "pause", "resume", "run_now"],
                "description": "Operation to perform.",
            },
            "workflow_path": {
                "type": "string",
                "description": (
                    "Vault path of the workflow .md file (e.g. "
                    "workflows/inbox-summary.md). Required for "
                    "create/update/pause/resume/delete_trigger."
                ),
            },
            "trigger": {
                "type": "object",
                "description": (
                    "Trigger configuration. For create: {type, ...type-specific}. "
                    "For update/pause/resume: {id} identifies the trigger; "
                    "include fields to change (e.g. cron, enabled)."
                ),
                "properties": {
                    "id": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "schedule", "webhook", "app_event", "fs_watch",
                            "event", "rss", "manual",
                        ],
                    },
                    "cron": {"type": "string"},
                    "path": {"type": "string"},
                    "events": {"type": "array", "items": {"type": "string"}},
                    "event": {"type": "string"},
                    "url": {"type": "string"},
                    "filter": {"type": "object"},
                    "max_active_minutes": {
                        "type": "integer",
                        "description": (
                            "Abort the workflow if a single run exceeds this "
                            "many minutes (stuck-work safeguard)."
                        ),
                    },
                    "max_runs": {
                        "type": "integer",
                        "description": (
                            "Stop scheduling after this many total runs "
                            "(0 = unlimited)."
                        ),
                    },
                },
            },
        },
        "required": ["action"],
    },
)

DELETE_TRIGGER_TOOL = ToolSpec(
    name="delete_trigger",
    description=(
        "Delete a trigger from a workflow. Supply ``workflow_path`` and "
        "``trigger.id``. Deleting a webhook trigger also removes its "
        "broker endpoint and token — the webhook URL stops working."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_path": {"type": "string"},
            "trigger": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                },
                "required": ["id"],
            },
        },
        "required": ["workflow_path", "trigger"],
    },
)


# ── helpers ────────────────────────────────────────────────────────────

def _read_wf(path: str) -> WorkflowDef | None:
    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        return None
    except Exception:
        log.exception("trigger tool: failed to read %s", path)
        return None
    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    try:
        return wf_parser.parse(raw)
    except Exception:
        log.exception("trigger tool: failed to parse %s", path)
        return None


def _write_wf(path: str, wf: WorkflowDef, original_raw: str | None = None) -> None:
    md = wf_parser.serialize(wf, original_content=original_raw)
    _vault.write_file(path, md)


def _trigger_public(t: TriggerConfig) -> dict[str, Any]:
    """Public view of a trigger — never includes token/secret/broker ids."""
    out: dict[str, Any] = {
        "id": t.id,
        "type": t.type.value,
        "workflow_level_enabled": True,
    }
    if t.type == TriggerType.schedule:
        if t.cron:
            out["cron"] = t.cron
    elif t.type == TriggerType.webhook:
        out["has_token"] = bool(t.token)
        out["has_secret"] = bool(t.secret)
        out["allowed_methods"] = t.allowed_methods
        out["payload_format"] = t.payload_format
    elif t.type == TriggerType.fs_watch:
        out["path"] = t.path
        out["pattern"] = t.pattern
        out["events"] = t.events
        out["debounce_ms"] = t.debounce_ms
    elif t.type == TriggerType.event:
        out["event"] = t.event
        out["filter"] = t.filter
    elif t.type == TriggerType.rss:
        out["rss_url"] = t.rss_url
        out["rss_poll_minutes"] = t.rss_poll_minutes
    return out


async def handle_trigger_info(
    args: dict[str, Any],
    engine_getter: Callable[[], Any] | None,
) -> str:
    """List all triggers across vault workflows."""
    try:
        entries = _vault.list_tree()
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"vault list failed: {exc}"})

    out: list[dict[str, Any]] = []
    for entry in entries:
        if entry.type != "file" or not entry.path.endswith(".md"):
            continue
        try:
            content = _vault.read_file(entry.path)
        except Exception:
            continue
        raw = content.get("content", "") if isinstance(content, dict) else str(content)
        if not raw.startswith("---") or "workflow-plugin" not in raw[:500]:
            continue
        try:
            wf = wf_parser.parse(raw)
        except Exception:
            continue
        for t in wf.triggers:
            out.append({
                "workflow_path": entry.path,
                "workflow_title": wf.title,
                "workflow_enabled": wf.enabled,
                "workflow_bot": wf.bot or None,
                **_trigger_public(t),
            })

    return json.dumps({"ok": True, "triggers": out})


async def handle_trigger(
    args: dict[str, Any],
    engine_getter: Callable[[], Any] | None,
) -> str:
    action = args.get("action")
    if action not in ("create", "update", "pause", "resume", "run_now"):
        return json.dumps({
            "ok": False,
            "error": "invalid action (create|update|pause|resume|run_now)",
        })

    path = args.get("workflow_path", "")
    if not path and action != "run_now":
        return json.dumps({"ok": False, "error": "workflow_path required"})

    if action == "run_now":
        return await _run_now(args, engine_getter)

    wf = _read_wf(path)
    if wf is None:
        return json.dumps({"ok": False, "error": f"workflow not found: {path}"})

    try:
        content = _vault.read_file(path)
        original_raw = content.get("content", "") if isinstance(content, dict) else str(content)
    except Exception:
        original_raw = None

    trigger_cfg = args.get("trigger") or {}
    t_id = trigger_cfg.get("id") or str(uuid.uuid4().hex[:12])

    if action == "create":
        t_type = trigger_cfg.get("type")
        if not t_type:
            return json.dumps({"ok": False, "error": "trigger.type required for create"})
        try:
            tt = TriggerType(t_type)
        except ValueError:
            return json.dumps({"ok": False, "error": f"unknown trigger type: {t_type}"})

        new_t = TriggerConfig(id=t_id, type=tt)

        if tt == TriggerType.schedule:
            cron = trigger_cfg.get("cron", "")
            if not cron:
                return json.dumps({"ok": False, "error": "schedule triggers need cron"})
            new_t.cron = cron
        elif tt == TriggerType.webhook:
            new_t.token = secrets.token_hex(16)  # UI card only, never to agent
        elif tt == TriggerType.fs_watch:
            new_t.path = trigger_cfg.get("path", "*")
            new_t.events = trigger_cfg.get("events") or ["created"]
            new_t.pattern = trigger_cfg.get("pattern", "*")
            new_t.debounce_ms = int(trigger_cfg.get("debounce_ms", 1000))
        elif tt == TriggerType.event:
            new_t.event = trigger_cfg.get("event", "")
            new_t.filter = trigger_cfg.get("filter")
        elif tt == TriggerType.rss:
            new_t.rss_url = trigger_cfg.get("url", "")
            new_t.rss_poll_minutes = int(trigger_cfg.get("rss_poll_minutes", 15))

        # Persist run-limit metadata in workflow variables (agent-visible)
        vars_ = dict(wf.variables)
        if trigger_cfg.get("max_active_minutes"):
            vars_[f"nx:trigger:{t_id}:max_active_minutes"] = str(
                int(trigger_cfg["max_active_minutes"])
            )
        if trigger_cfg.get("max_runs") is not None:
            vars_[f"nx:trigger:{t_id}:max_runs"] = str(int(trigger_cfg["max_runs"]))
            vars_[f"nx:trigger:{t_id}:runs"] = "0"
        wf.variables = vars_

        wf.triggers.append(new_t)
        wf.enabled = True
        _write_wf(path, wf, original_raw)
        return json.dumps({
            "ok": True,
            "action": "create",
            "trigger_id": t_id,
            "type": t_type,
            "workflow_path": path,
            "note": (
                "webhook token/secret not returned — copy the URL from the "
                "UI Triggers card"
            ) if tt == TriggerType.webhook else None,
        })

    if action in ("update", "pause", "resume"):
        found = None
        for t in wf.triggers:
            if t.id == t_id:
                found = t
                break
        if found is None:
            return json.dumps({"ok": False, "error": f"trigger not found: {t_id}"})

        if action == "pause":
            wf.enabled = False
        elif action == "resume":
            wf.enabled = True
        else:  # update
            if "cron" in trigger_cfg and found.type == TriggerType.schedule:
                found.cron = str(trigger_cfg["cron"])
            if "path" in trigger_cfg and found.type == TriggerType.fs_watch:
                found.path = str(trigger_cfg["path"])
            if "events" in trigger_cfg and found.type == TriggerType.fs_watch:
                found.events = list(trigger_cfg["events"])
            if "event" in trigger_cfg and found.type == TriggerType.event:
                found.event = str(trigger_cfg["event"])
            if "filter" in trigger_cfg and found.type == TriggerType.event:
                found.filter = trigger_cfg["filter"]
            if "url" in trigger_cfg and found.type == TriggerType.rss:
                found.rss_url = str(trigger_cfg["url"])
            # Update run-limit vars
            vars_ = dict(wf.variables)
            if trigger_cfg.get("max_active_minutes"):
                vars_[f"nx:trigger:{t_id}:max_active_minutes"] = str(
                    int(trigger_cfg["max_active_minutes"])
                )
            if trigger_cfg.get("max_runs") is not None:
                vars_[f"nx:trigger:{t_id}:max_runs"] = str(int(trigger_cfg["max_runs"]))
                vars_[f"nx:trigger:{t_id}:runs"] = "0"
            wf.variables = vars_

        _write_wf(path, wf, original_raw)
        return json.dumps({
            "ok": True,
            "action": action,
            "trigger_id": t_id,
            "workflow_path": path,
            "workflow_enabled": wf.enabled,
        })

    return json.dumps({"ok": False, "error": f"unhandled action: {action}"})


async def _run_now(args: dict[str, Any], engine_getter: Callable[[], Any] | None) -> str:
    path = args.get("workflow_path", "")
    trigger_cfg = args.get("trigger") or {}
    t_id = trigger_cfg.get("id") or "manual"

    if not path:
        return json.dumps({"ok": False, "error": "workflow_path required for run_now"})

    engine = engine_getter() if engine_getter else None
    if engine is None:
        return json.dumps({"ok": False, "error": "workflow engine not initialised"})

    try:
        run = await engine.run_workflow(
            workflow_path=path,
            trigger_id=t_id,
            trigger_type=TriggerType.manual,
            trigger_payload={"source": "agent_trigger_tool", "action": "run_now"},
        )
        return json.dumps({
            "ok": True,
            "action": "run_now",
            "run_id": run.id,
            "workflow_path": path,
            "status": run.status.value,
        })
    except Exception as exc:
        log.exception("trigger tool: run_now failed for %s", path)
        return json.dumps({"ok": False, "error": str(exc)})


async def handle_delete_trigger(
    args: dict[str, Any],
    engine_getter: Callable[[], Any] | None,
) -> str:
    path = args.get("workflow_path", "")
    t_id = (args.get("trigger") or {}).get("id", "")
    if not path or not t_id:
        return json.dumps({"ok": False, "error": "workflow_path and trigger.id required"})

    wf = _read_wf(path)
    if wf is None:
        return json.dumps({"ok": False, "error": f"workflow not found: {path}"})

    try:
        content = _vault.read_file(path)
        original_raw = content.get("content", "") if isinstance(content, dict) else str(content)
    except Exception:
        original_raw = None

    before = len(wf.triggers)
    wf.triggers = [t for t in wf.triggers if t.id != t_id]
    if len(wf.triggers) == before:
        return json.dumps({"ok": False, "error": f"trigger not found: {t_id}"})

    # Cleanup webhook broker endpoint + token best-effort
    try:
        from ..broker.client import BrokerClient
        from ..broker.sync import delete_broker_endpoint
        broker_client = BrokerClient()
        if broker_client.available:
            try:
                await delete_broker_endpoint(
                    broker_client, endpoint_key=f"{path}:{t_id}"
                )
            except Exception:
                pass
    except Exception:
        pass

    _write_wf(path, wf, original_raw)
    return json.dumps({
        "ok": True,
        "action": "delete",
        "trigger_id": t_id,
        "workflow_path": path,
    })


# ── tool handler wrappers ─────────────────────────────────────────────

async def _trigger_info(args: dict[str, Any]) -> str:
    return await handle_trigger_info(args, _engine_getter)


async def _trigger(args: dict[str, Any]) -> str:
    return await handle_trigger(args, _engine_getter)


async def _delete_trigger(args: dict[str, Any]) -> str:
    return await handle_delete_trigger(args, _engine_getter)


_engine_getter: Callable[[], Any] | None = None


def set_engine_getter(getter: Callable[[], Any]) -> None:
    global _engine_getter
    _engine_getter = getter


def get_engine_getter() -> Callable[[], Any] | None:
    return _engine_getter
