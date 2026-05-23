"""Workflow execution engine.

Runs a workflow's steps sequentially, resolving template expressions,
handling errors, and persisting run state. Supports interactive
step-by-step execution with re-run and downstream data wipe.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid
from typing import Any

from . import parser
from .expressions import build_context, evaluate_condition, resolve_templates
from .models import (
    RunStatus,
    StepConfig,
    StepRun,
    StepRunStatus,
    StepType,
    TriggerType,
    WorkflowDef,
    WorkflowRun,
)
from .store import WorkflowStore
from .schema import infer_schema, truncate_sample, save_schema, load_schema, save_step_sample, load_step_samples, save_trigger_sample, load_trigger_sample

log = logging.getLogger(__name__)

_DEBUG_SESSIONS: dict[str, dict[str, asyncio.Event]] = {}

_INTERACTIVE_SESSIONS: dict[str, dict[str, Any]] = {}


def _publish_debug(run_id: str, kind: str, payload: dict[str, Any]) -> None:
    try:
        from ..server.event_bus import publish
        publish({"type": f"workflow.debug.{kind}", "run_id": run_id, **payload})
    except Exception:
        pass


class WorkflowEngine:
    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        self._running: dict[str, asyncio.Task] = {}

    @property
    def store(self) -> WorkflowStore:
        return self._store

    async def run_workflow(
        self,
        workflow_path: str,
        trigger_id: str,
        trigger_type: TriggerType,
        trigger_payload: dict[str, Any],
        wf_def: WorkflowDef | None = None,
    ) -> WorkflowRun:
        if wf_def is None:
            wf_def = self._load_workflow(workflow_path)

        now = datetime.datetime.utcnow().isoformat()
        run = WorkflowRun(
            id=str(uuid.uuid4()),
            workflow_path=workflow_path,
            trigger_id=trigger_id,
            trigger_type=trigger_type,
            trigger_payload=trigger_payload,
            status=RunStatus.running,
            started_at=now,
        )
        self._store.create_run(run)

        task = asyncio.current_task()
        if task:
            self._running[run.id] = task

        try:
            await self._execute_steps(run, wf_def)
        except Exception as exc:
            run.status = RunStatus.failed
            run.error = str(exc)
            run.finished_at = datetime.datetime.utcnow().isoformat()
            self._store.update_run(run)
            log.exception("workflow %s run %s failed", workflow_path, run.id)
        finally:
            self._running.pop(run.id, None)
            self._publish_run_event(run)

        return run

    def _publish_run_event(self, run: WorkflowRun) -> None:
        try:
            from ..server.event_bus import publish
            publish({
                "type": "workflow.run_completed",
                "run_id": run.id,
                "workflow_path": run.workflow_path,
                "status": run.status.value,
                "trigger_type": run.trigger_type.value,
                "error": run.error,
            })
        except Exception:
            pass

    def cancel_run(self, run_id: str) -> bool:
        task = self._running.get(run_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def _execute_steps(self, run: WorkflowRun, wf: WorkflowDef) -> None:
        step_outputs: dict[str, Any] = {}
        steps_by_id = {s.id: s for s in wf.steps}
        step_order = [s.id for s in wf.steps]
        idx = 0

        while idx < len(step_order):
            step_id = step_order[idx]
            step = steps_by_id.get(step_id)
            if step is None:
                idx += 1
                continue

            ctx = build_context(run.trigger_payload, step_outputs, wf.variables)

            if step.condition is not None:
                if not evaluate_condition(step.condition, ctx):
                    now = datetime.datetime.utcnow().isoformat()
                    sr = StepRun(
                        run_id=run.id,
                        step_id=step.id,
                        status=StepRunStatus.skipped,
                        started_at=now,
                        finished_at=now,
                    )
                    self._store.create_step_run(sr)
                    step_outputs[step.id] = {"_skipped": True}
                    idx += 1
                    continue

            run.current_step = step.id
            self._store.update_run(run)

            step_run = await self._execute_step(step, ctx, run.id)
            self._store.create_step_run(step_run)

            if step_run.status == StepRunStatus.failed:
                error_action = step.on_error
                if error_action == "continue":
                    step_outputs[step.id] = {"_error": step_run.error}
                    idx += 1
                    continue
                elif error_action.startswith("goto:"):
                    target = error_action[5:]
                    if target in steps_by_id:
                        idx = step_order.index(target)
                        continue
                run.status = RunStatus.failed
                run.error = f"Step '{step.name}' failed: {step_run.error}"
                run.finished_at = datetime.datetime.utcnow().isoformat()
                self._store.update_run(run)
                return

            if step_run.output is not None:
                step_outputs[step.id] = step_run.output

            if step.type == StepType.return_step:
                run.status = RunStatus.completed
                run.finished_at = datetime.datetime.utcnow().isoformat()
                run.trigger_payload["_return_value"] = step_run.output
                self._store.update_run(run)
                return

            if step.type == StepType.condition and step.expression:
                result = evaluate_condition(step.expression, ctx)
                target = step.then_step if result else step.else_step
                if target and target in steps_by_id:
                    idx = step_order.index(target)
                    continue

            idx += 1

        run.status = RunStatus.completed
        run.finished_at = datetime.datetime.utcnow().isoformat()
        self._store.update_run(run)

    async def _execute_step(
        self,
        step: StepConfig,
        ctx: dict[str, Any],
        run_id: str,
    ) -> StepRun:
        now = datetime.datetime.utcnow().isoformat()
        sr = StepRun(
            run_id=run_id,
            step_id=step.id,
            status=StepRunStatus.running,
            started_at=now,
        )

        resolved_input = resolve_templates(step.input, ctx) if step.input else None
        if step.type == StepType.agent_session:
            sr.input_resolved = {"prompt": resolve_templates(step.prompt or "", ctx)}
        else:
            sr.input_resolved = resolved_input

        last_exc: Exception | None = None
        attempts = 1 + step.retry_count

        for attempt in range(attempts):
            try:
                output = await self._dispatch_step(step, ctx, resolved_input)
                sr.status = StepRunStatus.completed
                sr.output = output
                sr.finished_at = datetime.datetime.utcnow().isoformat()
                return sr
            except asyncio.CancelledError:
                sr.status = StepRunStatus.skipped
                sr.finished_at = datetime.datetime.utcnow().isoformat()
                return sr
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(step.retry_delay_seconds)

        sr.status = StepRunStatus.failed
        sr.error = str(last_exc)
        sr.finished_at = datetime.datetime.utcnow().isoformat()
        return sr

    async def _dispatch_step(
        self,
        step: StepConfig,
        ctx: dict[str, Any],
        resolved_input: dict[str, Any] | None,
    ) -> Any:
        if step.type == StepType.tool_call:
            return await self._execute_tool_call(step, resolved_input)
        elif step.type == StepType.agent_session:
            return await self._execute_agent_session(step, ctx)
        elif step.type == StepType.condition:
            return {"result": evaluate_condition(step.expression or "", ctx)}
        elif step.type == StepType.delay:
            await asyncio.sleep(step.duration_seconds)
            return {"waited_seconds": step.duration_seconds}
        elif step.type == StepType.transform:
            if step.output_format == "llm":
                return await self._execute_llm_transform(step, ctx)
            elif step.output_format == "script":
                return await self._execute_script_transform(step, ctx)
            else:
                template = resolve_templates(step.template or "", ctx)
                if step.output_format == "json":
                    try:
                        return json.loads(template)
                    except json.JSONDecodeError:
                        return {"raw": template}
                return {"result": template}
        elif step.type == StepType.http_request:
            return await self._execute_http_request(step, ctx)
        elif step.type == StepType.mcp_call:
            return await self._execute_mcp_call(step, ctx)
        elif step.type == StepType.kanban_action:
            return await self._execute_kanban_action(step, ctx)
        elif step.type == StepType.table_action:
            return await self._execute_table_action(step, ctx)
        elif step.type == StepType.return_step:
            return await self._execute_return_step(step, ctx)
        else:
            raise ValueError(f"unsupported step type: {step.type}")

    async def _execute_llm_transform(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        from ..agent.loop.agent import Agent
        from ..server.session_store.store import SessionStore

        system_prompt = step.tool or "Transform the following input as instructed. Output only the result."
        user_input = resolve_templates(step.template or "", ctx)
        if not user_input:
            raise ValueError(f"step '{step.name}' missing input for LLM transform")

        agent: Agent | None = getattr(self, "_agent", None)
        store: SessionStore | None = getattr(self, "_sessions", None)
        if agent is None or store is None:
            return {"result": user_input, "_simulated": True}

        session = store.create()
        prompt = f"{system_prompt}\n\n{user_input}"
        final_text = ""
        async for event in agent.run_turn_stream(
            prompt,
            history=[],
            session_id=session.id,
            model_id=step.model or None,
        ):
            etype = event.get("type")
            if etype == "delta":
                final_text += event.get("text", "")
            elif etype == "error":
                raise RuntimeError(event.get("message", "LLM transform error"))
        return {"result": final_text.strip()}

    async def _execute_script_transform(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        template = resolve_templates(step.template or "", ctx)
        if not template:
            raise ValueError(f"step '{step.name}' missing script")

        data = ctx.get("steps", {})
        local_vars: dict[str, Any] = {"data": data, "json": json, "result": None}
        try:
            exec(template, {"__builtins__": {}}, local_vars)
            return {"result": local_vars.get("result")}
        except Exception as exc:
            raise ValueError(f"script error in step '{step.name}': {exc}") from exc

    async def _execute_tool_call(self, step: StepConfig, resolved_input: dict[str, Any] | None) -> Any:
        from ..agent._loom_bridge.registry import build_tool_registry

        tool_name = step.tool
        if not tool_name:
            raise ValueError(f"step '{step.name}' missing tool name")

        registry = build_tool_registry()
        handler = registry.get(tool_name)
        if handler is None:
            raise ValueError(f"unknown tool: {tool_name}")

        args = resolved_input or {}
        if asyncio.iscoroutinefunction(handler):
            result = await handler(**args)
        else:
            result = handler(**args)
        if isinstance(result, dict):
            return result
        return {"result": result}

    async def _execute_agent_session(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        from ..agent.loop.agent import Agent
        from ..server.session_store.store import SessionStore
        from ..agent.llm import ChatMessage as NxChatMessage

        prompt = resolve_templates(step.prompt or "", ctx)
        if not prompt:
            raise ValueError(f"step '{step.name}' missing prompt for agent session")

        agent: Agent | None = getattr(self, "_agent", None)
        store: SessionStore | None = getattr(self, "_sessions", None)

        if agent is None or store is None:
            return {"result": prompt, "_simulated": True}

        session = store.create()
        session_id = session.id

        final_text = ""
        final_messages: list[NxChatMessage] = []
        async for event in agent.run_turn_stream(
            prompt,
            history=[],
            session_id=session_id,
            model_id=step.model or None,
        ):
            etype = event.get("type")
            if etype == "delta":
                final_text += event.get("text", "")
            elif etype == "error":
                raise RuntimeError(event.get("message", "agent error"))
            elif etype == "done":
                raw_msgs = event.get("messages") or []
                final_messages = [
                    m if isinstance(m, NxChatMessage) else NxChatMessage(**m)
                    for m in raw_msgs
                ]

        if final_messages:
            store.replace_history(session_id, final_messages)

        return {"session_id": session_id, "result": final_text}

    async def _execute_http_request(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        import aiohttp
        from ..secrets import resolve as resolve_secret

        url = resolve_templates(step.url or "", ctx)
        method = step.method.upper()
        headers = resolve_templates(step.headers or {}, ctx)
        body = resolve_templates(step.body, ctx) if step.body else None

        if step.custom_headers:
            resolved_custom = resolve_templates(step.custom_headers, ctx)
            headers.update(resolved_custom)

        if step.auth_type == "basic":
            user = resolve_templates(step.auth_username or "", ctx)
            pwd_name = step.auth_password_credential
            pwd = resolve_secret(pwd_name) if pwd_name else ""
            import base64
            token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"

        elif step.auth_type == "apikey":
            key_val = resolve_secret(step.auth_credential) if step.auth_credential else ""
            prefix = step.auth_prefix or "Bearer"
            if step.auth_location == "query":
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}{step.auth_query_name or 'api_key'}={key_val}"
            elif step.auth_location == "header" and step.auth_header_name:
                headers[step.auth_header_name] = f"{prefix} {key_val}" if prefix else key_val
            else:
                headers["Authorization"] = f"{prefix} {key_val}"

        elif step.auth_type == "oauth":
            token_val = resolve_secret(step.auth_credential) if step.auth_credential else ""
            headers["Authorization"] = f"Bearer {token_val}"

        async with aiohttp.ClientSession() as session:
            kwargs: dict[str, Any] = {"headers": headers}
            if body is not None and method in ("POST", "PUT", "PATCH"):
                if isinstance(body, (dict, list)):
                    kwargs["json"] = body
                else:
                    kwargs["data"] = str(body)
            async with session.request(method, url, **kwargs) as resp:
                text = await resp.text()
                return {
                    "status": resp.status,
                    "body": text[:10000],
                    "headers": dict(resp.headers),
                }

    async def _execute_mcp_call(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        server_name = step.mcp_server
        tool_name = step.mcp_tool
        if not server_name or not tool_name:
            raise ValueError(f"step '{step.name}' missing mcp_server or mcp_tool")

        mgr = getattr(self, "_mcp_manager", None)
        if mgr is None:
            try:
                from ..server.app import app
                mgr = getattr(app.state, "mcp_manager", None)
            except Exception:
                pass
        if mgr is None:
            raise RuntimeError("MCP manager not available")

        client = mgr._clients.get(server_name)
        if client is None:
            raise ValueError(f"MCP server '{server_name}' not connected")

        resolved_input = resolve_templates(step.input or {}, ctx)
        result = await client.call_tool(tool_name, resolved_input)
        return result

    async def _execute_kanban_action(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        from ..vault_kanban import add_card, move_card, update_card

        action = step.action
        if not action:
            raise ValueError(f"step '{step.name}' missing action")

        board_path = resolve_templates(step.board_path or "", ctx)
        if not board_path:
            raise ValueError(f"step '{step.name}' missing board_path")

        if action == "add_card":
            lane_id = resolve_templates(step.lane_id or "", ctx)
            title = resolve_templates(step.template or "", ctx)
            body = resolve_templates(step.input or {}, ctx) if step.input else None
            card = add_card(board_path, lane_id, title, body)
            return {"card_id": card.id, "title": card.title}
        elif action == "move_card":
            card_id = resolve_templates(step.card_id or "", ctx)
            lane_id = resolve_templates(step.lane_id or "", ctx)
            move_card(board_path, card_id, lane_id)
            return {"moved": True, "card_id": card_id, "lane_id": lane_id}
        elif action == "update_card":
            card_id = resolve_templates(step.card_id or "", ctx)
            updates = resolve_templates(step.row_data or {}, ctx) if step.row_data else {}
            update_card(board_path, card_id, updates)
            return {"updated": True, "card_id": card_id}
        else:
            raise ValueError(f"unknown kanban action: {action}")

    async def _execute_table_action(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        from ..vault_datatable import add_row, update_row, find_rows

        action = step.action
        if not action:
            raise ValueError(f"step '{step.name}' missing action")

        table_path = resolve_templates(step.table_path or "", ctx)
        if not table_path:
            raise ValueError(f"step '{step.name}' missing table_path")

        if action == "add_row":
            row = resolve_templates(step.row_data or {}, ctx) if step.row_data else {}
            result = add_row(table_path, row)
            return {"added": True, "_id": result.get("_id")}
        elif action == "update_row":
            row_id = resolve_templates(step.row_id or "", ctx)
            updates = resolve_templates(step.row_data or {}, ctx) if step.row_data else {}
            update_row(table_path, row_id, updates)
            return {"updated": True, "row_id": row_id}
        elif action == "find_rows":
            where = resolve_templates(step.where or {}, ctx) if step.where else None
            rows = find_rows(table_path, where=where)
            return {"rows": rows}
        else:
            raise ValueError(f"unknown table action: {action}")

    async def _execute_return_step(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        template = step.response_template or ""
        resolved = resolve_templates(template, ctx)
        return {"_return": True, "response": resolved}

    def _load_workflow(self, path: str) -> WorkflowDef:
        from .. import vault as _vault

        content = _vault.read_file(path)
        body = content.get("content", "") if isinstance(content, dict) else str(content)
        return parser.parse(body)

    # ── Debug mode ──────────────────────────────────────────────────────

    async def run_workflow_debug(
        self,
        workflow_path: str,
        trigger_payload: dict[str, Any],
        wf_def: WorkflowDef | None = None,
    ) -> WorkflowRun:
        if wf_def is None:
            wf_def = self._load_workflow(workflow_path)

        now = datetime.datetime.utcnow().isoformat()
        run = WorkflowRun(
            id=str(uuid.uuid4()),
            workflow_path=workflow_path,
            trigger_id="debug",
            trigger_type=TriggerType.manual,
            trigger_payload=trigger_payload,
            status=RunStatus.running,
            started_at=now,
        )
        self._store.create_run(run)

        task = asyncio.current_task()
        if task:
            self._running[run.id] = task

        _DEBUG_SESSIONS[run.id] = {}

        try:
            await self._execute_steps_debug(run, wf_def)
        except asyncio.CancelledError:
            run.status = RunStatus.cancelled
            run.finished_at = datetime.datetime.utcnow().isoformat()
            self._store.update_run(run)
        except Exception as exc:
            run.status = RunStatus.failed
            run.error = str(exc)
            run.finished_at = datetime.datetime.utcnow().isoformat()
            self._store.update_run(run)
        finally:
            self._running.pop(run.id, None)
            _DEBUG_SESSIONS.pop(run.id, None)

        return run

    async def _execute_steps_debug(self, run: WorkflowRun, wf: WorkflowDef) -> None:
        step_outputs: dict[str, Any] = {}
        step_schemas: dict[str, dict] = {}
        steps_by_id = {s.id: s for s in wf.steps}
        step_order = [s.id for s in wf.steps]
        idx = 0

        while idx < len(step_order):
            step_id = step_order[idx]
            step = steps_by_id.get(step_id)
            if step is None:
                idx += 1
                continue

            ctx = build_context(run.trigger_payload, step_outputs, wf.variables)

            _publish_debug(run.id, "step_starting", {
                "step_id": step.id,
                "step_name": step.name,
                "step_type": step.type.value,
            })

            pause_evt = asyncio.Event()
            _DEBUG_SESSIONS[run.id][step.id] = pause_evt

            run.current_step = step.id
            self._store.update_run(run)

            step_run = await self._execute_step(step, ctx, run.id)
            self._store.create_step_run(step_run)

            if step_run.status == StepRunStatus.failed:
                error_action = step.on_error
                if error_action == "continue":
                    step_outputs[step.id] = {"_error": step_run.error}
                else:
                    run.status = RunStatus.failed
                    run.error = f"Step '{step.name}' failed: {step_run.error}"
                    run.finished_at = datetime.datetime.utcnow().isoformat()
                    self._store.update_run(run)
                    _publish_debug(run.id, "step_failed", {
                        "step_id": step.id,
                        "error": step_run.error,
                    })
                    return

            if step_run.output is not None:
                step_outputs[step.id] = step_run.output
                schema = infer_schema(step_run.output)
                if schema:
                    step_schemas[step.id] = {
                        "slug": step.slug or "",
                        "output_schema": schema,
                        "sample_output": truncate_sample(step_run.output),
                    }

            _publish_debug(run.id, "step_completed", {
                "step_id": step.id,
                "step_name": step.name,
                "status": step_run.status.value,
                "input_resolved": step_run.input_resolved,
                "output": truncate_sample(step_run.output, 2000),
                "error": step_run.error,
                "started_at": step_run.started_at,
                "finished_at": step_run.finished_at,
            })

            await pause_evt.wait()

            if step.type == StepType.condition and step.expression:
                result = evaluate_condition(step.expression, ctx)
                target = step.then_step if result else step.else_step
                if target and target in steps_by_id:
                    idx = step_order.index(target)
                    continue

            idx += 1

        run.status = RunStatus.completed
        run.finished_at = datetime.datetime.utcnow().isoformat()
        self._store.update_run(run)

        if step_schemas:
            save_schema(run.workflow_path, step_schemas)

        _publish_debug(run.id, "run_completed", {"status": run.status.value})

    def debug_continue(self, run_id: str, step_id: str | None = None) -> bool:
        sessions = _DEBUG_SESSIONS.get(run_id)
        if not sessions:
            return False
        if step_id:
            evt = sessions.get(step_id)
            if evt and not evt.is_set():
                evt.set()
                return True
        else:
            for evt in sessions.values():
                if not evt.is_set():
                    evt.set()
                    return True
        return False

    async def debug_rerun_step(self, run_id: str, step_id: str) -> StepRun | None:
        run = self._store.get_run(run_id)
        if not run:
            return None

        wf = self._load_workflow(run.workflow_path)
        step = next((s for s in wf.steps if s.id == step_id), None)
        if not step:
            return None

        step_runs = self._store.list_step_runs(run_id)
        outputs: dict[str, Any] = {}
        for sr in step_runs:
            if sr.output is not None:
                outputs[sr.step_id] = sr.output

        ctx = build_context(run.trigger_payload, outputs, wf.variables)
        step_run = await self._execute_step(step, ctx, run_id)
        self._store.create_step_run(step_run)

        _publish_debug(run_id, "step_rerun", {
            "step_id": step.id,
            "status": step_run.status.value,
            "output": truncate_sample(step_run.output, 2000),
            "error": step_run.error,
        })

        return step_run

    async def test_trigger(
        self,
        workflow_path: str,
        trigger_id: str,
        wf_def: WorkflowDef | None = None,
    ) -> dict[str, Any]:
        if wf_def is None:
            wf_def = self._load_workflow(workflow_path)

        trigger = next((t for t in wf_def.triggers if t.id == trigger_id), None)
        if trigger is None:
            raise ValueError(f"trigger {trigger_id} not found")

        payload: dict[str, Any] = {}

        if trigger.type == TriggerType.webhook:
            payload = {
                "method": (trigger.allowed_methods or ["POST"])[0],
                "headers": {"content-type": "application/json"},
                "body": {"message": "test webhook payload", "data": {"key": "value"}},
                "query": {},
            }
        elif trigger.type == TriggerType.fs_watch:
            payload = {
                "event_type": "created",
                "src_path": trigger.path or "/tmp/test.txt",
                "is_directory": False,
            }
        elif trigger.type == TriggerType.schedule:
            payload = {
                "fired_at": datetime.datetime.utcnow().isoformat(),
                "cron": trigger.cron or "* * * * *",
            }
        elif trigger.type == TriggerType.event:
            payload = {
                "event": trigger.event or "test.event",
                "data": {"source": "test", "payload": "sample"},
            }
        elif trigger.type == TriggerType.manual:
            payload = {"manual": True, "triggered_at": datetime.datetime.utcnow().isoformat()}

        schema = infer_schema(payload)
        sample = truncate_sample(payload)

        return {
            "trigger_payload": payload,
            "schema": schema,
            "sample": sample,
        }

    async def test_step(
        self,
        workflow_path: str,
        step_id: str,
        trigger_payload: dict[str, Any],
        step_outputs: dict[str, Any],
        wf_def: WorkflowDef | None = None,
    ) -> dict[str, Any]:
        if wf_def is None:
            wf_def = self._load_workflow(workflow_path)

        step = next((s for s in wf_def.steps if s.id == step_id), None)
        if step is None:
            raise ValueError(f"step {step_id} not found")

        ctx = build_context(trigger_payload, step_outputs, wf_def.variables)
        step_run = await self._execute_step(step, ctx, "test")

        result: dict[str, Any] = {
            "step_id": step.id,
            "step_name": step.name,
            "status": step_run.status.value,
            "input_resolved": step_run.input_resolved,
            "output": step_run.output,
            "error": step_run.error,
        }

        if step_run.output is not None:
            schema = infer_schema(step_run.output)
            if schema:
                result["schema"] = schema
                result["sample"] = truncate_sample(step_run.output)

        if step_run.output is not None:
            step_outputs[step.id] = step_run.output

        return result

    # ── Interactive step-by-step execution ─────────────────────────────────

    async def start_interactive(
        self,
        workflow_path: str,
        trigger_payload: dict[str, Any],
        wf_def: WorkflowDef | None = None,
        seed_from_samples: bool = False,
    ) -> WorkflowRun:
        if wf_def is None:
            wf_def = self._load_workflow(workflow_path)

        now = datetime.datetime.utcnow().isoformat()
        run = WorkflowRun(
            id=str(uuid.uuid4()),
            workflow_path=workflow_path,
            trigger_id="interactive",
            trigger_type=TriggerType.manual,
            trigger_payload=trigger_payload,
            status=RunStatus.running,
            started_at=now,
        )
        self._store.create_run(run)

        _INTERACTIVE_SESSIONS[run.id] = {
            "workflow_path": workflow_path,
            "trigger_payload": trigger_payload,
            "variables": dict(wf_def.variables),
            "steps": [s.to_dict() for s in wf_def.steps],
            "condition_branches": {},
        }

        if seed_from_samples:
            samples = load_step_samples(workflow_path)
            for step_id, sample in samples.items():
                output = sample.get("output")
                if output is not None:
                    sr = StepRun(
                        run_id=run.id,
                        step_id=step_id,
                        status=StepRunStatus.completed,
                        input_resolved=sample.get("input_resolved"),
                        output=output,
                        started_at=now,
                        finished_at=now,
                    )
                    self._store.create_step_run(sr)

        _publish_interactive(run.id, "run_started", {
            "trigger_payload": trigger_payload,
        })

        save_trigger_sample(workflow_path, trigger_payload)

        return run

    async def interactive_execute_step(
        self,
        run_id: str,
        step_id: str,
    ) -> StepRun | None:
        run = self._store.get_run(run_id)
        if not run:
            return None

        session = _INTERACTIVE_SESSIONS.get(run_id)
        if not session:
            wf = self._load_workflow(run.workflow_path)
            steps_by_id = {s.id: s for s in wf.steps}
            step = steps_by_id.get(step_id)
            if not step:
                return None

            step_runs = self._store.list_step_runs(run_id)
            outputs: dict[str, Any] = {}
            for sr in step_runs:
                if sr.output is not None and sr.status == StepRunStatus.completed:
                    outputs[sr.step_id] = sr.output

            ctx = build_context(run.trigger_payload, outputs, wf.variables)
            step_run = await self._execute_step(step, ctx, run_id)
            self._store.create_step_run(step_run)

            _publish_interactive(run_id, "step_executed", {
                "step_id": step.id,
                "step_name": step.name,
                "step_type": step.type.value,
                "status": step_run.status.value,
                "input_resolved": step_run.input_resolved,
                "output": truncate_sample(step_run.output, 4000),
                "error": step_run.error,
                "started_at": step_run.started_at,
                "finished_at": step_run.finished_at,
            })

            if step_run.status == StepRunStatus.completed and step_run.output is not None:
                self._wipe_downstream(run_id, step_id, wf)
                save_step_sample(
                    run.workflow_path, step.id,
                    step_name=step.name, step_slug=step.slug,
                    input_resolved=step_run.input_resolved,
                    output=step_run.output,
                )

            return step_run

        steps_data = session.get("steps", [])
        steps = [self._dict_to_step(s) for s in steps_data]
        steps_by_id = {s.id: s for s in steps}
        step = steps_by_id.get(step_id)
        if not step:
            return None

        step_runs = self._store.list_step_runs(run_id)
        outputs: dict[str, Any] = {}
        for sr in step_runs:
            if sr.output is not None and sr.status == StepRunStatus.completed:
                outputs[sr.step_id] = sr.output

        cond_branches = session.get("condition_branches", {})
        if not self._is_step_reachable(step_id, steps, cond_branches, outputs):
            return None

        ctx = build_context(
            session["trigger_payload"],
            outputs,
            session.get("variables", {}),
        )

        _publish_interactive(run_id, "step_starting", {
            "step_id": step.id,
            "step_name": step.name,
            "step_type": step.type.value,
        })

        step_run = await self._execute_step(step, ctx, run_id)
        self._store.create_step_run(step_run)

        result_payload: dict[str, Any] = {
            "step_id": step.id,
            "step_name": step.name,
            "step_type": step.type.value,
            "status": step_run.status.value,
            "input_resolved": step_run.input_resolved,
            "output": truncate_sample(step_run.output, 4000),
            "error": step_run.error,
            "started_at": step_run.started_at,
            "finished_at": step_run.finished_at,
        }

        if step_run.status == StepRunStatus.completed and step.type == StepType.condition and step.expression:
            result = evaluate_condition(step.expression, ctx)
            cond_branches[step.id] = "then" if result else "else"
            result_payload["condition_result"] = result
            result_payload["condition_branch"] = cond_branches[step.id]
            session["condition_branches"] = cond_branches

        _publish_interactive(run_id, "step_executed", result_payload)

        if step_run.status == StepRunStatus.completed and step_run.output is not None:
            wf = self._load_workflow(run.workflow_path)
            self._wipe_downstream(run_id, step_id, wf)
            save_step_sample(
                run.workflow_path, step.id,
                step_name=step.name, step_slug=step.slug,
                input_resolved=step_run.input_resolved,
                output=step_run.output,
            )

        return step_run

    async def interactive_execute_all(
        self,
        run_id: str,
    ) -> WorkflowRun | None:
        run = self._store.get_run(run_id)
        if not run:
            return None

        session = _INTERACTIVE_SESSIONS.get(run_id)
        wf = self._load_workflow(run.workflow_path)
        steps_by_id = {s.id: s for s in wf.steps}

        step_runs = self._store.list_step_runs(run_id)
        outputs: dict[str, Any] = {}
        for sr in step_runs:
            if sr.output is not None and sr.status == StepRunStatus.completed:
                outputs[sr.step_id] = sr.output

        cond_branches: dict[str, str] = {}
        if session:
            cond_branches = session.get("condition_branches", {})

        step_order = self._compute_execution_order(wf, outputs, cond_branches)

        for step_id in step_order:
            step = steps_by_id.get(step_id)
            if not step:
                continue
            if step_id in outputs:
                continue

            ctx = build_context(
                session["trigger_payload"] if session else run.trigger_payload,
                outputs,
                session.get("variables", {}) if session else wf.variables,
            )

            if step.condition is not None:
                if not evaluate_condition(step.condition, ctx):
                    now = datetime.datetime.utcnow().isoformat()
                    sr = StepRun(
                        run_id=run.id, step_id=step.id,
                        status=StepRunStatus.skipped,
                        started_at=now, finished_at=now,
                    )
                    self._store.create_step_run(sr)
                    outputs[step.id] = {"_skipped": True}
                    continue

            _publish_interactive(run_id, "step_starting", {
                "step_id": step.id,
                "step_name": step.name,
                "step_type": step.type.value,
            })

            run.current_step = step.id
            self._store.update_run(run)

            step_run = await self._execute_step(step, ctx, run.id)
            self._store.create_step_run(step_run)

            result_payload: dict[str, Any] = {
                "step_id": step.id,
                "step_name": step.name,
                "step_type": step.type.value,
                "status": step_run.status.value,
                "input_resolved": step_run.input_resolved,
                "output": truncate_sample(step_run.output, 4000),
                "error": step_run.error,
                "started_at": step_run.started_at,
                "finished_at": step_run.finished_at,
            }

            if step_run.status == StepRunStatus.failed:
                error_action = step.on_error
                if error_action != "continue":
                    run.status = RunStatus.failed
                    run.error = f"Step '{step.name}' failed: {step_run.error}"
                    run.finished_at = datetime.datetime.utcnow().isoformat()
                    self._store.update_run(run)
                    _publish_interactive(run_id, "step_executed", result_payload)
                    _publish_interactive(run_id, "run_failed", {"error": run.error})
                    return run
                outputs[step.id] = {"_error": step_run.error}
            elif step_run.output is not None:
                outputs[step.id] = step_run.output
                save_step_sample(
                    run.workflow_path, step.id,
                    step_name=step.name, step_slug=step.slug,
                    input_resolved=step_run.input_resolved,
                    output=step_run.output,
                )

            if step.type == StepType.condition and step.expression and step_run.status == StepRunStatus.completed:
                result = evaluate_condition(step.expression, ctx)
                cond_branches[step.id] = "then" if result else "else"
                result_payload["condition_result"] = result
                result_payload["condition_branch"] = cond_branches[step.id]
                if session:
                    session["condition_branches"] = cond_branches

            _publish_interactive(run_id, "step_executed", result_payload)

            if step.type == StepType.return_step:
                run.status = RunStatus.completed
                run.finished_at = datetime.datetime.utcnow().isoformat()
                run.trigger_payload["_return_value"] = step_run.output
                self._store.update_run(run)
                _publish_interactive(run_id, "run_completed", {"status": "completed"})
                return run

        run.status = RunStatus.completed
        run.finished_at = datetime.datetime.utcnow().isoformat()
        self._store.update_run(run)
        _publish_interactive(run_id, "run_completed", {"status": "completed"})
        return run

    def interactive_get_state(self, run_id: str) -> dict[str, Any] | None:
        run = self._store.get_run(run_id)
        if not run:
            return None

        step_runs = self._store.list_step_runs(run_id)
        session = _INTERACTIVE_SESSIONS.get(run_id, {})

        return {
            "run": run.to_dict(),
            "steps": [sr.to_dict() for sr in step_runs],
            "condition_branches": session.get("condition_branches", {}),
        }

    def interactive_cancel(self, run_id: str) -> bool:
        session = _INTERACTIVE_SESSIONS.pop(run_id, None)
        if session is None:
            return False

        run = self._store.get_run(run_id)
        if run and run.status == RunStatus.running:
            run.status = RunStatus.cancelled
            run.finished_at = datetime.datetime.utcnow().isoformat()
            self._store.update_run(run)
            _publish_interactive(run_id, "run_cancelled", {})

        return True

    def _dict_to_step(self, d: dict[str, Any]) -> StepConfig:
        return StepConfig(
            id=d.get("id", ""),
            name=d.get("name", ""),
            type=StepType(d.get("type", "tool_call")),
            slug=d.get("slug"),
            tool=d.get("tool"),
            input=d.get("input"),
            prompt=d.get("prompt"),
            model=d.get("model"),
            url=d.get("url"),
            method=d.get("method", "GET"),
            headers=d.get("headers"),
            body=d.get("body"),
            auth_type=d.get("auth_type", "none"),
            auth_credential=d.get("auth_credential"),
            auth_username=d.get("auth_username"),
            auth_password_credential=d.get("auth_password_credential"),
            auth_header_name=d.get("auth_header_name"),
            auth_prefix=d.get("auth_prefix", "Bearer"),
            auth_query_name=d.get("auth_query_name"),
            auth_location=d.get("auth_location", "header"),
            custom_headers=d.get("custom_headers"),
            expression=d.get("expression"),
            then_step=d.get("then_step"),
            else_step=d.get("else_step"),
            template=d.get("template"),
            output_format=d.get("output_format", "text"),
            duration_seconds=d.get("duration_seconds", 0),
            condition=d.get("condition"),
            on_error=d.get("on_error", "stop"),
            retry_count=d.get("retry_count", 0),
            retry_delay_seconds=d.get("retry_delay_seconds", 5),
            action=d.get("action"),
            board_path=d.get("board_path"),
            lane_id=d.get("lane_id"),
            card_id=d.get("card_id"),
            table_path=d.get("table_path"),
            row_data=d.get("row_data"),
            row_id=d.get("row_id"),
            where=d.get("where"),
            mcp_server=d.get("mcp_server"),
            mcp_tool=d.get("mcp_tool"),
            llm_instructions=d.get("llm_instructions"),
            output_sample=d.get("output_sample"),
            response_template=d.get("response_template"),
            next_step=d.get("next_step"),
        )

    def _wipe_downstream(self, run_id: str, step_id: str, wf: WorkflowDef) -> None:
        downstream = self._get_downstream_steps(step_id, wf)

        if not downstream:
            return

        self._store.delete_step_runs(run_id, downstream)

        session = _INTERACTIVE_SESSIONS.get(run_id)
        if session:
            for ds_id in downstream:
                session.pop(f"output_{ds_id}", None)

    def _get_downstream_steps(self, step_id: str, wf: WorkflowDef) -> list[str]:
        steps_by_id = {s.id: s for s in wf.steps}
        visited: set[str] = set()
        result: list[str] = []
        queue: list[str] = []

        step = steps_by_id.get(step_id)
        if not step:
            return []

        if step.next_step:
            queue.append(step.next_step)
        if step.then_step:
            queue.append(step.then_step)
        if step.else_step:
            queue.append(step.else_step)

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            result.append(current_id)

            current = steps_by_id.get(current_id)
            if current:
                if current.next_step:
                    queue.append(current.next_step)
                if current.then_step:
                    queue.append(current.then_step)
                if current.else_step:
                    queue.append(current.else_step)

        return result

    def _compute_execution_order(
        self,
        wf: WorkflowDef,
        existing_outputs: dict[str, Any],
        cond_branches: dict[str, str],
    ) -> list[str]:
        steps_by_id = {s.id: s for s in wf.steps}
        order: list[str] = []
        visited: set[str] = set()

        if not wf.steps:
            return order

        first_step = wf.steps[0]
        self._walk_chain(first_step.id, steps_by_id, existing_outputs, cond_branches, visited, order)

        for s in wf.steps:
            if s.id not in visited:
                order.append(s.id)

        return order

    def _walk_chain(
        self,
        step_id: str,
        steps_by_id: dict[str, StepConfig],
        existing_outputs: dict[str, Any],
        cond_branches: dict[str, str],
        visited: set[str],
        order: list[str],
    ) -> None:
        if step_id in visited:
            return
        visited.add(step_id)
        order.append(step_id)

        step = steps_by_id.get(step_id)
        if not step:
            return

        if step.type == StepType.condition:
            branch = cond_branches.get(step.id)
            if branch == "then" and step.then_step:
                self._walk_chain(step.then_step, steps_by_id, existing_outputs, cond_branches, visited, order)
            elif branch == "else" and step.else_step:
                self._walk_chain(step.else_step, steps_by_id, existing_outputs, cond_branches, visited, order)
            else:
                if step.then_step:
                    self._walk_chain(step.then_step, steps_by_id, existing_outputs, cond_branches, visited, order)
                if step.else_step:
                    self._walk_chain(step.else_step, steps_by_id, existing_outputs, cond_branches, visited, order)

            cond_step = steps_by_id.get(step_id)
            if cond_step:
                for s in steps_by_id.values():
                    if s.id not in visited:
                        has_pred = False
                        for other in steps_by_id.values():
                            if other.next_step == s.id or other.then_step == s.id or other.else_step == s.id:
                                if other.id in visited:
                                    pass
                                else:
                                    has_pred = True
                        if not has_pred:
                            pass
        elif step.next_step:
            self._walk_chain(step.next_step, steps_by_id, existing_outputs, cond_branches, visited, order)

    def _is_step_reachable(
        self,
        step_id: str,
        steps: list[StepConfig],
        cond_branches: dict[str, str],
        outputs: dict[str, Any],
    ) -> bool:
        if not steps:
            return False

        steps_by_id = {s.id: s for s in steps}
        step = steps_by_id.get(step_id)
        if not step:
            return False

        if step_id == steps[0].id:
            return True

        for s in steps:
            if s.next_step == step_id or s.then_step == step_id or s.else_step == step_id:
                if s.type == StepType.condition:
                    branch = cond_branches.get(s.id)
                    if branch == "then" and s.then_step == step_id:
                        return s.id in outputs
                    elif branch == "else" and s.else_step == step_id:
                        return s.id in outputs
                    elif not branch:
                        return False
                    continue
                if s.id in outputs:
                    return True

        return False


def _publish_interactive(run_id: str, kind: str, payload: dict[str, Any]) -> None:
    try:
        from ..server.event_bus import publish
        publish({"type": f"workflow.interactive.{kind}", "run_id": run_id, **payload})
    except Exception:
        pass
