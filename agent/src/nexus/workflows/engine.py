"""Workflow execution engine.

Runs a workflow's steps sequentially, resolving template expressions,
handling errors, and persisting run state.
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

log = logging.getLogger(__name__)


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

        return run

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
        else:
            raise ValueError(f"unsupported step type: {step.type}")

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

        return {"session_id": session_id, "result": final_text}

    async def _execute_http_request(self, step: StepConfig, ctx: dict[str, Any]) -> Any:
        import aiohttp

        url = resolve_templates(step.url or "", ctx)
        method = step.method.upper()
        headers = resolve_templates(step.headers or {}, ctx)
        body = resolve_templates(step.body, ctx) if step.body else None

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
        return {"_not_implemented": True, "mcp_server": step.mcp_server, "mcp_tool": step.mcp_tool}

    def _load_workflow(self, path: str) -> WorkflowDef:
        from .. import vault as _vault

        content = _vault.read_file(path)
        body = content.get("content", "") if isinstance(content, dict) else str(content)
        return parser.parse(body)
