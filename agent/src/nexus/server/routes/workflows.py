"""API routes for workflow CRUD, manual trigger, run history, and webhook receiver."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...workflows import parser
from ...workflows.engine import WorkflowEngine
from ...workflows.models import (
    StepConfig,
    StepRun,
    StepRunStatus,
    TriggerConfig,
    TriggerType,
    WorkflowDef,
)
from ...workflows.cache import WorkflowListCache
from ...workflows.store import WorkflowStore

EVENT_TYPE_REGISTRY = [
    {"pattern": "vault.indexed", "description": "Vault file indexed (FTS/metadata)", "category": "vault"},
    {"pattern": "vault.created", "description": "Vault file created", "category": "vault"},
    {"pattern": "vault.removed", "description": "Vault file removed", "category": "vault"},
    {"pattern": "vault.*", "description": "Any vault event", "category": "vault"},
    {"pattern": "graphrag.indexed", "description": "GraphRAG indexing completed", "category": "knowledge"},
    {"pattern": "graphrag.index_failed", "description": "GraphRAG indexing failed", "category": "knowledge"},
    {"pattern": "graphrag.removed", "description": "GraphRAG data removed", "category": "knowledge"},
    {"pattern": "graphrag.*", "description": "Any GraphRAG event", "category": "knowledge"},
    {"pattern": "dream.*", "description": "Any dream engine event", "category": "dream"},
    {"pattern": "workflow.run_completed", "description": "Workflow run completed", "category": "workflow"},
    {"pattern": "workflow.*", "description": "Any workflow event", "category": "workflow"},
    {"pattern": "local_llm.*", "description": "Local LLM download/progress event", "category": "system"},
]

log = logging.getLogger(__name__)

router = APIRouter()

_STORE: WorkflowStore | None = None
_ENGINE: WorkflowEngine | None = None
_CACHE = WorkflowListCache()


def _enrich_step_runs(step_runs: list, workflow_path: str) -> list[dict]:
    from ... import vault as _vault
    step_lookup: dict[str, dict] = {}
    try:
        content = _vault.read_file(workflow_path)
        raw = content.get("content", "") if isinstance(content, dict) else str(content)
        wf_def = parser.parse(raw)
        for s in wf_def.steps:
            step_lookup[s.id] = {"step_name": s.name, "step_slug": s.slug or "", "step_type": s.type.value}
    except Exception:
        pass
    enriched: list[dict] = []
    for sr in step_runs:
        d = sr.to_dict()
        cfg = step_lookup.get(sr.step_id, {})
        d["step_name"] = cfg.get("step_name", "")
        d["step_slug"] = cfg.get("step_slug", "")
        d["step_type"] = cfg.get("step_type", "")
        enriched.append(d)
    return enriched


def _enrich_single_step(sr, workflow_path: str) -> dict:
    return _enrich_step_runs([sr], workflow_path)[0]


def init(store: WorkflowStore, engine: WorkflowEngine) -> None:
    global _STORE, _ENGINE
    _STORE = store
    _ENGINE = engine


def _get_store(request: Request) -> WorkflowStore:
    s = _STORE or getattr(request.app.state, "workflow_store", None)
    if s is None:
        raise HTTPException(status_code=500, detail="workflow store not initialised")
    return s


def _get_engine(request: Request) -> WorkflowEngine:
    e = _ENGINE or getattr(request.app.state, "workflow_engine", None)
    if e is None:
        raise HTTPException(status_code=500, detail="workflow engine not initialised")
    return e


def _scan_workflows(request: Request) -> list[dict]:
    from ... import vault as _vault

    entries = _vault.list_tree()
    workflows: list[dict] = []
    for entry in entries:
        if entry.type != "file" or not entry.path.endswith(".md"):
            continue
        try:
            content = _vault.read_file(entry.path)
            body = content.get("content", "") if isinstance(content, dict) else str(content)
            if body.startswith("---") and "workflow-plugin" in body[:500]:
                wf = parser.parse(body)
                workflows.append({
                    "path": entry.path,
                    "title": wf.title,
                    "enabled": wf.enabled,
                    "step_count": len(wf.steps),
                    "trigger_count": len(wf.triggers),
                })
        except Exception:
            continue
    return workflows


class WorkflowCreateBody(BaseModel):
    path: str
    title: str = "Untitled Workflow"
    description: str = ""
    enabled: bool = True


class WorkflowUpdateBody(BaseModel):
    title: str | None = None
    enabled: bool | None = None
    triggers: list[dict] | None = None
    variables: dict[str, str] | None = None
    steps: list[dict] | None = None


class ManualRunBody(BaseModel):
    payload: dict | None = None


@router.get("/workflows/tools")
async def list_workflow_tools(request: Request) -> dict:
    tools = _collect_tool_specs()
    return {"tools": tools}


def _collect_tool_specs() -> list[dict]:
    from nexus.agent.llm import ToolSpec
    from nexus.agent.loop import SKILL_MANAGE_TOOL

    spec_groups: list[tuple[ToolSpec, ...]] = []

    def _add(*specs: ToolSpec) -> None:
        spec_groups.append(specs)

    _add(SKILL_MANAGE_TOOL)

    from nexus.tools.state_tool import STATE_TOOLS
    _add(*STATE_TOOLS)

    from nexus.tools.ontology_tool import ONTOLOGY_MANAGE_TOOL
    _add(ONTOLOGY_MANAGE_TOOL)

    from nexus.tools.http_call import HTTP_CALL_TOOL
    _add(HTTP_CALL_TOOL)

    from nexus.tools.acp_call import ACP_CALL_TOOL
    if _acp_configured():
        _add(ACP_CALL_TOOL)

    from nexus.tools.vault_tool import VAULT_TOOLS, VAULT_SEMANTIC_SEARCH_TOOL
    _add(*VAULT_TOOLS)
    _add(VAULT_SEMANTIC_SEARCH_TOOL)

    from nexus.tools.kanban_tool import KANBAN_MANAGE_TOOL
    _add(KANBAN_MANAGE_TOOL)

    from nexus.tools.kanban_query_tool import KANBAN_QUERY_TOOL
    _add(KANBAN_QUERY_TOOL)

    from nexus.tools.calendar_tool import CALENDAR_MANAGE_TOOL
    _add(CALENDAR_MANAGE_TOOL)

    from nexus.tools.dispatch_card_tool import DISPATCH_CARD_TOOL
    _add(DISPATCH_CARD_TOOL)

    from nexus.tools.heartbeat_tool import HEARTBEAT_MANAGE_TOOL
    _add(HEARTBEAT_MANAGE_TOOL)

    from nexus.tools.datatable_tool import DATATABLE_MANAGE_TOOL
    _add(DATATABLE_MANAGE_TOOL)

    from nexus.tools.dashboard_tool import DASHBOARD_MANAGE_TOOL
    _add(DASHBOARD_MANAGE_TOOL)

    from nexus.tools.show_kanban_tool import SHOW_KANBAN_TOOL
    _add(SHOW_KANBAN_TOOL)

    from nexus.tools.show_dashboard_widget_tool import SHOW_DASHBOARD_WIDGET_TOOL
    _add(SHOW_DASHBOARD_WIDGET_TOOL)

    from nexus.tools.show_data_table_tool import SHOW_DATA_TABLE_TOOL
    _add(SHOW_DATA_TABLE_TOOL)

    from nexus.tools.csv_tool import CSV_TOOL
    _add(CSV_TOOL)

    from nexus.tools.visualize_tool import VISUALIZE_TABLE_TOOL
    _add(VISUALIZE_TABLE_TOOL)

    from nexus.tools.ocr_tool import OCR_IMAGE_TOOL
    _add(OCR_IMAGE_TOOL)

    from nexus.tools.memory_tool import MEMORY_READ_TOOL, MEMORY_WRITE_TOOL
    _add(MEMORY_READ_TOOL)
    _add(MEMORY_WRITE_TOOL)

    from nexus.tools.nexus_kb import NEXUS_KB_TOOL
    _add(NEXUS_KB_TOOL)

    from nexus.tools.context_tool import CONTEXT_STATUS_TOOL, FORK_SESSION_TOOL
    _add(CONTEXT_STATUS_TOOL)
    _add(FORK_SESSION_TOOL)

    from nexus.agent.ask_user_tool import ASK_USER_TOOL
    _add(ASK_USER_TOOL)

    from loom.tools.terminal import TERMINAL_TOOL_SPEC
    _add(TERMINAL_TOOL_SPEC)

    from nexus.agent.notify_user_tool import NOTIFY_USER_TOOL
    _add(NOTIFY_USER_TOOL)

    from loom.tools.subagent import SPAWN_SUBAGENTS_TOOL_SPEC
    _add(SPAWN_SUBAGENTS_TOOL_SPEC)

    seen: set[str] = set()
    result: list[dict] = []
    for group in spec_groups:
        for spec in group:
            if spec.name in seen:
                continue
            seen.add(spec.name)
            result.append({
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            })

    result.sort(key=lambda t: t["name"])
    return result


def _acp_configured() -> bool:
    try:
        from nexus.tools.acp_call import acp_is_configured
        return acp_is_configured()
    except Exception:
        return False


@router.get("/workflows/event-types")
async def list_event_types() -> dict:
    return {"event_types": EVENT_TYPE_REGISTRY}


@router.get("/workflows")
async def list_workflows(request: Request) -> dict:
    if _CACHE.is_warm:
        return {"workflows": _CACHE.get_all()}
    workflows = _scan_workflows(request)
    for w in workflows:
        _CACHE.update(w["path"], w)
    return {"workflows": workflows}


@router.get("/workflows/{path:path}/schema")
async def get_workflow_schema(path: str) -> dict:
    from ...workflows.schema import load_schema
    return load_schema(path)


@router.get("/workflows/{path:path}/runs")
async def list_runs(path: str, request: Request, limit: int = 50, offset: int = 0) -> dict:
    store = _get_store(request)
    runs = store.list_runs(path, limit=limit, offset=offset)
    return {"runs": [r.to_dict() for r in runs]}


@router.get("/workflows/{path:path}/runs/{run_id}")
async def get_run(path: str, run_id: str, request: Request) -> dict:
    store = _get_store(request)
    run = store.get_run(run_id)
    if run is None or run.workflow_path != path:
        raise HTTPException(status_code=404, detail="run not found")
    step_runs = store.list_step_runs(run_id)
    return {"run": run.to_dict(), "steps": _enrich_step_runs(step_runs, path)}


@router.get("/workflows/{path:path}/webhook-url")
async def get_webhook_url(path: str, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    base_url = str(request.base_url).rstrip("/")
    from ...config_file import load as load_config
    broker_base = load_config().broker.url.rstrip("/")

    hooks = []
    for t in wf.triggers:
        if t.type == TriggerType.webhook:
            local_url = f"{base_url}/workflow/trigger/{t.token}" if t.token else None
            broker_url = f"{broker_base}/wh/{t.broker_slug}" if t.broker_slug else None
            hooks.append({
                "trigger_id": t.id,
                "token": t.token,
                "url": broker_url or local_url,
                "localUrl": local_url,
                "brokerUrl": broker_url,
            })

    return {"webhooks": hooks}


@router.get("/workflows/{path:path}/debug/{run_id}/events")
async def debug_events(path: str, run_id: str) -> StreamingResponse:
    import asyncio
    from ...server.event_bus import subscribe, unsubscribe

    q = subscribe()

    async def stream():
        try:
            yield b": subscribed\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if event.get("run_id") != run_id:
                    continue
                evt_type = event.get("type", "debug")
                payload = {k: v for k, v in event.items() if k != "type"}
                data = json.dumps(payload, default=str)
                yield f"event: {evt_type}\ndata: {data}\n\n".encode()
                if evt_type in ("workflow.debug.run_completed", "workflow.debug.step_failed"):
                    if event.get("status") in ("completed", "failed", "cancelled"):
                        break
        finally:
            unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@router.get("/workflows/{path:path}/samples")
async def get_workflow_samples(path: str) -> dict:
    from ...workflows.schema import load_step_samples, load_trigger_sample

    return {
        "trigger_payload": load_trigger_sample(path),
        "steps": load_step_samples(path),
    }


@router.get("/workflows/{path:path}/interactive-run/{run_id}")
async def get_interactive_state(path: str, run_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    state = engine.interactive_get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="interactive run not found")
    from ...workflows.models import StepRun
    step_runs = [StepRun(
        run_id=s["run_id"], step_id=s["step_id"], status=s["status"],
        input_resolved=s.get("input_resolved"), output=s.get("output"),
        error=s.get("error"), started_at=s.get("started_at"), finished_at=s.get("finished_at"),
    ) for s in state.get("steps", [])]
    state["steps"] = _enrich_step_runs(step_runs, path)
    return state


@router.get("/workflows/{path:path}")
async def get_workflow(path: str, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    body = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(body)
    store = _get_store(request)
    runs = store.list_runs(path, limit=10)
    return {
        "path": path,
        "definition": wf.to_dict(),
        "runs": [r.to_dict() for r in runs],
    }


@router.post("/workflows")
async def create_workflow(body: WorkflowCreateBody, request: Request) -> dict:
    from ... import vault as _vault

    if not body.path.endswith(".md"):
        body.path += ".md"
    wf = WorkflowDef(title=body.title, enabled=body.enabled, description=body.description)
    md = parser.serialize(wf)
    _vault.write_file(body.path, md)
    _CACHE.update(body.path, {
        "path": body.path,
        "title": wf.title,
        "enabled": wf.enabled,
        "step_count": len(wf.steps),
        "trigger_count": len(wf.triggers),
    })
    return {"ok": True, "path": body.path}


@router.put("/workflows/{path:path}")
async def update_workflow(path: str, body: WorkflowUpdateBody, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    if body.title is not None:
        wf.title = body.title
    if body.enabled is not None:
        wf.enabled = body.enabled
    if body.triggers is not None:
        wf.triggers = [TriggerConfig.from_dict(t) for t in body.triggers]
    if body.variables is not None:
        wf.variables = body.variables
    if body.steps is not None:
        wf.steps = [StepConfig.from_dict(s) for s in body.steps]

    md = parser.serialize(wf, original_content=raw)
    _vault.write_file(path, md)

    store = _get_store(request)

    for t in wf.triggers:
        if t.type == TriggerType.webhook and not t.token:
            t.token = secrets.token_hex(16)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            store.register_webhook_token(t.token, path, t.id, now)
            md = parser.serialize(wf, original_content=raw)
            _vault.write_file(path, md)

    from ...broker.client import BrokerClient
    from ...broker.provision import ensure_broker_endpoint as _ensure_broker
    broker_client = BrokerClient()
    if broker_client.available:
        broker_changed = False
        for t in wf.triggers:
            if t.type == TriggerType.webhook and t.token:
                try:
                    bwh = await _ensure_broker(
                        client=broker_client,
                        endpoint_type="workflow",
                        endpoint_key=f"{path}:{t.id}",
                        name=f"Workflow: {wf.title}",
                        existing_broker_id=t.broker_id,
                        existing_broker_slug=t.broker_slug,
                    )
                    if bwh:
                        if t.broker_id != bwh.id or t.broker_slug != bwh.slug:
                            t.broker_id = bwh.id
                            t.broker_slug = bwh.slug
                            broker_changed = True
                except Exception:
                    log.exception("broker: failed to provision for workflow %s trigger %s", path, t.id)
        if broker_changed:
            md = parser.serialize(wf, original_content=raw)
            _vault.write_file(path, md)

    _register_triggers(path, wf, request)

    _CACHE.update(path, {
        "path": path,
        "title": wf.title,
        "enabled": wf.enabled,
        "step_count": len(wf.steps),
        "trigger_count": len(wf.triggers),
    })

    return {"ok": True}


def _register_triggers(path: str, wf: WorkflowDef, request: Request) -> None:
    try:
        fsw = getattr(request.app.state, "workflow_fsw_driver", None)
        if fsw:
            import asyncio
            asyncio.create_task(fsw.start(path, wf))
    except Exception:
        pass
    try:
        evt = getattr(request.app.state, "workflow_event_listener", None)
        if evt:
            for t in wf.triggers:
                if t.type == TriggerType.event and t.event:
                    evt.register(path, t.id, t.event, t.filter)
    except Exception:
        pass


def _unregister_triggers(path: str, wf: WorkflowDef | None, request: Request) -> None:
    try:
        fsw = getattr(request.app.state, "workflow_fsw_driver", None)
        if fsw and wf:
            import asyncio
            for t in wf.triggers:
                if t.type == TriggerType.fs_watch:
                    asyncio.create_task(fsw.stop(path, t.id))
    except Exception:
        pass
    try:
        evt = getattr(request.app.state, "workflow_event_listener", None)
        if evt and wf:
            for t in wf.triggers:
                if t.type == TriggerType.event:
                    evt.unregister(path, t.id)
    except Exception:
        pass


@router.delete("/workflows/{path:path}")
async def delete_workflow(path: str, request: Request) -> dict:
    from ... import vault as _vault

    wf = None
    try:
        content = _vault.read_file(path)
        raw = content.get("content", "") if isinstance(content, dict) else str(content)
        wf = parser.parse(raw)
    except Exception:
        pass

    try:
        _vault.delete(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    store = _get_store(request)
    store.remove_webhook_tokens(path)
    _unregister_triggers(path, wf, request)
    _CACHE.invalidate(path)
    return {"ok": True}


@router.post("/workflows/{path:path}/run")
async def manual_run(path: str, body: ManualRunBody, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    if not wf.enabled:
        raise HTTPException(status_code=400, detail="workflow is disabled")

    engine = _get_engine(request)
    run = await engine.run_workflow(
        workflow_path=path,
        trigger_id="manual",
        trigger_type=TriggerType.manual,
        trigger_payload=body.payload or {},
        wf_def=wf,
    )
    return run.to_dict()


@router.api_route("/workflow/trigger/{token}", methods=["GET", "POST"])
async def webhook_trigger(token: str, request: Request) -> Response:
    if token.startswith("test_"):
        from ...workflows.triggers.test_listener import _TEST_LISTENERS
        test_info = None
        for _tid, info in list(_TEST_LISTENERS.items()):
            if info.get("test_token") == token:
                test_info = info
                break
        if test_info is not None:
            content_type = request.headers.get("content-type", "")
            try:
                if "application/json" in content_type:
                    payload = await request.json()
                elif "application/x-www-form-urlencoded" in content_type:
                    form = await request.form()
                    payload = dict(form)
                else:
                    raw_bytes = await request.body()
                    payload = {"raw": raw_bytes.decode("utf-8", errors="replace")}
            except Exception:
                payload = {}
            try:
                test_info["queue"].put_nowait({
                    "method": request.method,
                    "headers": dict(request.headers),
                    "body": payload,
                    "query": dict(request.query_params),
                })
            except Exception:
                pass
            return Response(
                content=json.dumps({"captured": True}),
                status_code=status.HTTP_200_OK,
                media_type="application/json",
            )

    store = _get_store(request)
    result = store.lookup_webhook_token(token)
    if result is None:
        raise HTTPException(status_code=404, detail="unknown webhook token")

    workflow_path, trigger_id = result

    content_type = request.headers.get("content-type", "")
    try:
        if "application/json" in content_type:
            payload = await request.json()
        elif "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            payload = dict(form)
        else:
            raw_bytes = await request.body()
            raw_str = raw_bytes.decode("utf-8", errors="replace")
            payload = {"raw": raw_str}
    except Exception:
        payload = {}

    from ... import vault as _vault

    try:
        content = _vault.read_file(workflow_path)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="workflow file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    if not wf.enabled:
        raise HTTPException(status_code=400, detail="workflow is disabled")

    engine = _get_engine(request)

    has_return = any(
        s.get("type") == "return_step" for s in (wf.to_dict().get("steps") or [])
    )

    if has_return:
        run = await engine.run_workflow(
            workflow_path=workflow_path,
            trigger_id=trigger_id,
            trigger_type=TriggerType.webhook,
            trigger_payload=payload,
            wf_def=wf,
        )
        if run.status.value == "failed":
            return Response(
                content=json.dumps({"error": run.error or "workflow run failed"}, default=str),
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                media_type="application/json",
            )
        return_val = run.trigger_payload.get("_return_value", {})
        resp = return_val.get("response", return_val) if isinstance(return_val, dict) else return_val
        return Response(
            content=json.dumps(resp, default=str),
            status_code=status.HTTP_200_OK,
            media_type="application/json",
        )

    import asyncio
    asyncio.create_task(engine.run_workflow(
        workflow_path=workflow_path,
        trigger_id=trigger_id,
        trigger_type=TriggerType.webhook,
        trigger_payload=payload,
        wf_def=wf,
    ))

    return Response(
        content=json.dumps({"ok": True}),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


class DebugStartBody(BaseModel):
    payload: dict | None = None


@router.post("/workflows/{path:path}/debug")
async def start_debug(path: str, body: DebugStartBody, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    engine = _get_engine(request)

    run = await engine.run_workflow_debug(
        workflow_path=path,
        trigger_payload=body.payload or {},
        wf_def=wf,
    )
    return run.to_dict()


@router.post("/workflows/{path:path}/debug/{run_id}/continue")
async def debug_continue(path: str, run_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    step_id = None
    body_data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    step_id = body_data.get("step_id")
    ok = engine.debug_continue(run_id, step_id)
    if not ok:
        raise HTTPException(status_code=404, detail="debug session not found or already completed")
    return {"ok": True}


@router.post("/workflows/{path:path}/debug/{run_id}/step/{step_id}/rerun")
async def debug_rerun_step(path: str, run_id: str, step_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    sr = await engine.debug_rerun_step(run_id, step_id)
    if sr is None:
        raise HTTPException(status_code=404, detail="debug session or step not found")
    return _enrich_single_step(sr, path)


@router.post("/workflows/{path:path}/debug/{run_id}/cancel")
async def debug_cancel(run_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    ok = engine.cancel_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True}


class TestTriggerBody(BaseModel):
    trigger_id: str


@router.post("/workflows/{path:path}/test-trigger")
async def test_trigger(path: str, body: TestTriggerBody, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    engine = _get_engine(request)
    try:
        result = await engine.test_trigger(path, body.trigger_id, wf_def=wf)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result


class TestTriggerListenBody(BaseModel):
    trigger_id: str


@router.post("/workflows/{path:path}/test-trigger/listen")
async def test_trigger_listen(
    path: str, body: TestTriggerListenBody, request: Request,
) -> StreamingResponse:
    import asyncio as _asyncio
    from ... import vault as _vault
    from ...workflows.triggers.test_listener import TestTriggerListener

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    trigger = next((t for t in wf.triggers if t.id == body.trigger_id), None)
    if trigger is None:
        raise HTTPException(status_code=404, detail="trigger not found")

    store = _get_store(request)
    engine = _get_engine(request)
    test_id = str(uuid.uuid4())
    base_url = str(request.base_url).rstrip("/")

    trigger_config = {
        "workflow_path": path,
        "trigger_id": trigger.id,
        "path": trigger.path,
        "event": trigger.event,
        "pattern": trigger.pattern,
        "events": trigger.events,
        "base_url": base_url,
    }

    listener = TestTriggerListener(
        test_id=test_id,
        trigger_type=trigger.type.value,
        trigger_config=trigger_config,
        store=store,
        engine=engine,
    )

    async def stream():
        try:
            async for event_bytes in listener.start():
                yield event_bytes
        finally:
            await listener.cleanup()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/workflows/{path:path}/test-trigger/{test_id}")
async def cancel_test_listener(path: str, test_id: str) -> dict:
    from ...workflows.triggers.test_listener import remove_test_listener
    info = remove_test_listener(test_id)
    if info is None:
        raise HTTPException(status_code=404, detail="test listener not found")
    for task_or_observer in info.get("cleanup_tasks", []):
        try:
            if isinstance(task_or_observer, asyncio.Task):
                task_or_observer.cancel()
            else:
                task_or_observer.stop()
        except Exception:
            pass
    return {"ok": True}


class TestStepBody(BaseModel):
    step_id: str
    trigger_payload: dict = {}
    step_outputs: dict = {}


class InteractiveStartBody(BaseModel):
    payload: dict = {}
    mode: str = "trigger"  # "trigger" (execute trigger only) or "all" (execute all steps)
    seed_from_samples: bool = False
    payload_format: str = "json"  # "json" | "plain" | "xml"
    payload_raw: str = ""  # raw payload text when format is not json


class InteractiveExecuteStepBody(BaseModel):
    pass


@router.post("/workflows/{path:path}/interactive-run")
async def start_interactive_run(path: str, body: InteractiveStartBody, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    engine = _get_engine(request)
    from ...workflows.engine import _convert_trigger_payload

    if body.payload_raw and body.payload_format != "json":
        trigger_payload = _convert_trigger_payload(body.payload_raw, body.payload_format)
    else:
        trigger_payload = body.payload

    run = await engine.start_interactive(
        workflow_path=path,
        trigger_payload=trigger_payload,
        wf_def=wf,
        seed_from_samples=body.seed_from_samples,
    )

    if body.mode == "all":
        completed_run = await engine.interactive_execute_all(run.id)
        if completed_run:
            run = completed_run

    return {
        "run": run.to_dict(),
        "mode": body.mode,
    }


class SeedFromRunBody(BaseModel):
    pass


@router.post("/workflows/{path:path}/seed-from-run/{run_id}")
async def seed_from_run(path: str, run_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    run = await engine.seed_from_run(path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="source run not found")
    step_runs = engine.store.list_step_runs(run.id)
    return {
        "run": run.to_dict(),
        "steps": _enrich_step_runs(step_runs, path),
        "condition_branches": {},
    }


class ExecuteStepBody(BaseModel):
    step_config: dict | None = None


@router.post("/workflows/{path:path}/interactive-run/{run_id}/execute-step/{step_id}")
async def interactive_execute_step(
    path: str, run_id: str, step_id: str, body: ExecuteStepBody, request: Request,
) -> dict:
    step_override = None
    if body.step_config:
        try:
            step_override = StepConfig.from_dict(body.step_config)
            step_override.id = step_id
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("step_override parse failed: %s", exc)
            pass
    engine = _get_engine(request)
    sr, cond_branches = await engine.interactive_execute_step(run_id, step_id, step_override=step_override)
    if sr is None:
        raise HTTPException(status_code=404, detail="step not found or not reachable")
    result = _enrich_single_step(sr, path)
    if cond_branches:
        result["condition_branches"] = cond_branches
    return result


@router.post("/workflows/{path:path}/interactive-run/{run_id}/execute-all")
async def interactive_execute_all(path: str, run_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    run = await engine.interactive_execute_all(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="interactive run not found")
    return run.to_dict()


@router.post("/workflows/{path:path}/interactive-run/{run_id}/cancel")
async def interactive_cancel(run_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    ok = engine.interactive_cancel(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="interactive run not found")
    return {"ok": True}


@router.get("/workflows/{path:path}/interactive-run/{run_id}/events")
async def interactive_events(path: str, run_id: str) -> StreamingResponse:
    import asyncio
    from ...server.event_bus import subscribe, unsubscribe

    q = subscribe()

    async def stream():
        try:
            yield b": subscribed\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield b": ping\n\n"
                    continue
                if event.get("run_id") != run_id:
                    continue
                evt_type = event.get("type", "interactive")
                payload = {k: v for k, v in event.items() if k != "type"}
                data = json.dumps(payload, default=str)
                yield f"event: {evt_type}\ndata: {data}\n\n".encode()
                if evt_type in ("workflow.interactive.run_completed", "workflow.interactive.run_failed", "workflow.interactive.run_cancelled"):
                    break
        finally:
            unsubscribe(q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workflows/{path:path}/test-step")
async def test_step(path: str, body: TestStepBody, request: Request) -> dict:
    from ... import vault as _vault

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    engine = _get_engine(request)
    try:
        result = await engine.test_step(
            path, body.step_id, body.trigger_payload, body.step_outputs, wf_def=wf
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return result


class GenerateScriptBody(BaseModel):
    description: str
    input_schema: dict = {}
    trigger_keys: list[str] = []


def _schema_only(value: Any, max_depth: int = 3) -> Any:
    if isinstance(value, dict):
        if max_depth <= 0:
            return "{...}"
        return {k: _schema_only(v, max_depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "[]"
        if max_depth <= 0:
            return "[...]"
        return [_schema_only(value[0], max_depth - 1)]
    return json.dumps(value, default=str) if not isinstance(value, (int, float, bool)) and len(str(value)) > 120 else type(value).__name__


@router.post("/workflows/{path:path}/generate-script")
async def generate_script(path: str, body: GenerateScriptBody, request: Request) -> dict:
    engine = _get_engine(request)

    input_desc = ""
    if body.input_schema:
        schema_view = {slug: _schema_only(val) for slug, val in body.input_schema.items()}
        input_desc = (
            f"\nAvailable step output data (in `data` dict, keyed by step slug). "
            f"Types/shapes shown, not actual values:\n```json\n"
            f"{json.dumps(schema_view, indent=2, default=str)[:2000]}\n```\n"
        )
    if body.trigger_keys:
        input_desc += f"\nTrigger payload keys: {', '.join(body.trigger_keys)} (accessible via data dict)\n"

    system_prompt = (
        "You are a Python code generator. Your ONLY job is to write Python code.\n"
        "You MUST NOT perform the transformation yourself. Write Python code that performs it.\n\n"
        "Rules:\n"
        "- The script runs inside exec() with a sandboxed namespace.\n"
        "- Available variables: `data` (dict with all step outputs, keyed by slug), `json` module.\n"
        "- You MUST set the `result` variable to the output value.\n"
        "- No imports, no file I/O, no network access. Only pure Python + json module.\n"
        "- Output ONLY the Python code. No markdown fences, no explanation, no output data.\n"
        "- Keep it concise. Prefer one-liners when possible.\n\n"
        "Example: user says 'convert to uppercase' → output: result = str(data['myStep']['text']).upper()\n"
        "Example: user says 'extract names' → output: result = [item['name'] for item in data['myStep']['items']]\n"
    )
    user_prompt = (
        f"Write Python code that does the following:\n{body.description}\n"
        f"{input_desc}\n"
        f"Output ONLY the Python script (set `result = ...`):"
    )

    try:
        code = await engine.single_shot_llm(
            user_prompt, system_prompt=system_prompt,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    cleaned = code.strip()
    if cleaned.startswith("```python"):
        cleaned = cleaned[len("```python"):]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    return {"code": cleaned}
