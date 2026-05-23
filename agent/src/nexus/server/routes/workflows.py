"""API routes for workflow CRUD, manual trigger, run history, and webhook receiver."""

from __future__ import annotations

import json
import logging
import secrets
import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...workflows import parser
from ...workflows.engine import WorkflowEngine
from ...workflows.models import (
    StepConfig,
    StepType,
    TriggerConfig,
    TriggerType,
    WorkflowDef,
)
from ...workflows.cache import WorkflowListCache
from ...workflows.store import WorkflowStore

log = logging.getLogger(__name__)

router = APIRouter()

_STORE: WorkflowStore | None = None
_ENGINE: WorkflowEngine | None = None
_CACHE = WorkflowListCache()


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
    return {"run": run.to_dict(), "steps": [sr.to_dict() for sr in step_runs]}


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
    hooks = []
    for t in wf.triggers:
        if t.type == TriggerType.webhook:
            url = f"{base_url}/workflow/trigger/{t.token}" if t.token else None
            hooks.append({"trigger_id": t.id, "token": t.token, "url": url})

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
        wf.triggers = [
            TriggerConfig(
                id=t.get("id", ""),
                type=TriggerType(t.get("type", "manual")),
                token=t.get("token"),
                cron=t.get("cron"),
                path=t.get("path"),
                pattern=t.get("pattern", "*"),
                events=t.get("events", ["created"]),
                debounce_ms=t.get("debounce_ms", 1000),
                event=t.get("event"),
                filter=t.get("filter"),
            )
            for t in body.triggers
        ]
    if body.variables is not None:
        wf.variables = body.variables
    if body.steps is not None:
        wf.steps = [
            StepConfig(
                id=s.get("id", ""),
                name=s.get("name", ""),
                type=StepType(s.get("type", "tool_call")),
                tool=s.get("tool"),
                input=s.get("input"),
                prompt=s.get("prompt"),
                model=s.get("model"),
                background=s.get("background", False),
                max_turns=s.get("max_turns", 8),
                condition=s.get("condition"),
                on_error=s.get("on_error", "stop"),
                retry_count=s.get("retry_count", 0),
                retry_delay_seconds=s.get("retry_delay_seconds", 5),
                url=s.get("url"),
                method=s.get("method", "GET"),
                headers=s.get("headers"),
                body=s.get("body"),
                expression=s.get("expression"),
                then_step=s.get("then_step"),
                else_step=s.get("else_step"),
                template=s.get("template"),
                output_format=s.get("output_format", "text"),
                duration_seconds=s.get("duration_seconds", 0),
                mcp_server=s.get("mcp_server"),
                mcp_tool=s.get("mcp_tool"),
                action=s.get("action"),
                board_path=s.get("board_path"),
                lane_id=s.get("lane_id"),
                card_id=s.get("card_id"),
                table_path=s.get("table_path"),
                row_data=s.get("row_data"),
                row_id=s.get("row_id"),
                where=s.get("where"),
                query_sql=s.get("query_sql"),
                llm_instructions=s.get("llm_instructions"),
                output_sample=s.get("output_sample"),
                response_template=s.get("response_template"),
                auth_type=s.get("auth_type", "none"),
                auth_credential=s.get("auth_credential"),
                auth_username=s.get("auth_username"),
                auth_password_credential=s.get("auth_password_credential"),
                auth_header_name=s.get("auth_header_name"),
                auth_prefix=s.get("auth_prefix", "Bearer"),
                auth_query_name=s.get("auth_query_name"),
                auth_location=s.get("auth_location", "header"),
                custom_headers=s.get("custom_headers"),
                next_step=s.get("next_step"),
            )
            for s in body.steps
        ]

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
    return sr.to_dict()


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


class TestStepBody(BaseModel):
    step_id: str
    trigger_payload: dict = {}
    step_outputs: dict = {}


class InteractiveStartBody(BaseModel):
    payload: dict = {}
    mode: str = "trigger"  # "trigger" (execute trigger only) or "all" (execute all steps)
    seed_from_samples: bool = False


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
    run = await engine.start_interactive(
        workflow_path=path,
        trigger_payload=body.payload,
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


@router.get("/workflows/{path:path}/interactive-run/{run_id}")
async def get_interactive_state(path: str, run_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    state = engine.interactive_get_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="interactive run not found")
    return state


@router.post("/workflows/{path:path}/interactive-run/{run_id}/execute-step/{step_id}")
async def interactive_execute_step(path: str, run_id: str, step_id: str, request: Request) -> dict:
    engine = _get_engine(request)
    sr = await engine.interactive_execute_step(run_id, step_id)
    if sr is None:
        raise HTTPException(status_code=404, detail="step not found or not reachable")
    return sr.to_dict()


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


@router.post("/workflows/{path:path}/generate-script")
async def generate_script(path: str, body: GenerateScriptBody, request: Request) -> dict:
    import json as _json
    from ... import vault as _vault
    from ...agent.llm import ChatMessage as LLMMsg, Role

    try:
        content = _vault.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    engine = _get_engine(request)
    agent = getattr(engine, "_agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="agent not available")

    model_id = getattr(agent, "_chosen_model", None)
    resolved_provider, upstream_model = agent._resolve_provider(model_id)
    chat_model = upstream_model or model_id

    input_desc = ""
    if body.input_schema:
        input_desc = f"\nAvailable step output data (in `data` dict, keyed by step slug):\n```json\n{_json.dumps(body.input_schema, indent=2, default=str)[:3000]}\n```\n"
    if body.trigger_keys:
        input_desc += f"\nTrigger payload keys: {', '.join(body.trigger_keys)} (accessible via data dict)\n"

    system_msg = LLMMsg(
        role=Role.SYSTEM,
        content=(
            "You are a Python code generator. Generate a short Python script that transforms workflow data.\n"
            "Rules:\n"
            "- The script runs inside exec() with a sandboxed namespace.\n"
            "- Available variables: `data` (dict with all step outputs, keyed by slug), `json` module.\n"
            "- You MUST set the `result` variable to the output value.\n"
            "- No imports, no file I/O, no network access. Only pure Python + json module.\n"
            "- Output ONLY the Python code. No markdown fences, no explanation.\n"
            "- Keep it concise. Prefer one-liners when possible.\n"
        ),
    )
    user_msg = LLMMsg(
        role=Role.USER,
        content=f"{body.description}{input_desc}\n\nGenerate the Python script:",
    )

    try:
        resp = await resolved_provider.chat([system_msg, user_msg], model=chat_model, max_tokens=1024)
        code = resp.content or ""
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
