"""Schedule trigger — heartbeat driver that checks cron-based workflow triggers.

Registered as a heartbeat driver so it runs on the existing HeartbeatScheduler
tick interval (~60s). Scans all workflow vault files for schedule triggers
and fires any that are due.
"""

from __future__ import annotations

import asyncio
import logging
import datetime
from typing import Any

from loom.heartbeat import HeartbeatDriver, HeartbeatEvent

from nexus.workflows import parser as wf_parser
from nexus.workflows.models import TriggerType
from nexus.workflows.store import WorkflowStore

log = logging.getLogger(__name__)

_ENGINE_REF: Any = None


def set_engine_ref(engine: Any) -> None:
    global _ENGINE_REF
    _ENGINE_REF = engine


def _get_engine() -> Any:
    return _ENGINE_REF


class Driver(HeartbeatDriver):
    async def check(self, state: dict[str, Any]) -> tuple[list[HeartbeatEvent], dict[str, Any]]:
        events: list[HeartbeatEvent] = []
        now = datetime.datetime.now(datetime.timezone.utc)

        try:
            from nexus import vault as _vault
        except Exception:
            return events, state

        store: WorkflowStore | None = state.get("_store")
        if store is None:
            return events, state

        try:
            entries = _vault.list_tree()
        except Exception:
            return events, state

        for entry in entries:
            if entry.type != "file" or not entry.path.endswith(".md"):
                continue
            try:
                content = _vault.read_file(entry.path)
                body = content.get("content", "") if isinstance(content, dict) else str(content)
                if not body.startswith("---") or "workflow-plugin" not in body[:500]:
                    continue
                wf = wf_parser.parse(body)
                if not wf.enabled:
                    continue
                for trigger in wf.triggers:
                    if trigger.type != TriggerType.schedule or not trigger.cron:
                        continue
                    if self._is_due(trigger.cron, now, state, f"{entry.path}:{trigger.id}"):
                        payload = {
                            "cron": trigger.cron,
                            "fired_at": now.isoformat(),
                            "trigger_id": trigger.id,
                        }
                        engine = _get_engine()
                        if engine:
                            try:
                                asyncio.create_task(engine.run_workflow(
                                    workflow_path=entry.path,
                                    trigger_id=trigger.id,
                                    trigger_type=TriggerType.schedule,
                                    trigger_payload=payload,
                                ))
                            except Exception:
                                log.exception("schedule trigger: failed to dispatch %s", entry.path)
                        events.append(HeartbeatEvent(
                            id=f"wf-sched-{trigger.id}",
                            title=f"Workflow schedule: {wf.title}",
                            instructions="",
                        ))
            except Exception:
                continue

        return events, state

    def _is_due(self, cron: str, now: datetime.datetime, state: dict, key: str) -> bool:
        try:
            from croniter import croniter
        except ImportError:
            return False
        last_fire: str | None = state.get(key)
        if last_fire:
            try:
                last_dt = datetime.datetime.fromisoformat(last_fire)
            except (ValueError, TypeError):
                last_dt = now - datetime.timedelta(hours=1)
        else:
            last_dt = now - datetime.timedelta(hours=1)

        try:
            cron_iter = croniter(cron, last_dt)
            next_fire = cron_iter.get_next(datetime.datetime)
            if next_fire.tzinfo is None:
                next_fire = next_fire.replace(tzinfo=datetime.timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=datetime.timezone.utc)
            if next_fire <= now:
                state[key] = now.isoformat()
                return True
        except (ValueError, KeyError):
            pass
        return False
