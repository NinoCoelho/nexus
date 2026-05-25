from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .client import BrokerClient
from .crypto import load_or_generate_private_key, rsa_decrypt

log = logging.getLogger(__name__)


class BrokerPoller:
    def __init__(self, client: BrokerClient) -> None:
        self._client = client
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.get_running_loop().create_task(self._run())
        log.info("broker: poller started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        log.info("broker: poller stopped")

    async def _run(self) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=3.0)
            return
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            try:
                await self._poll_all()
            except Exception:
                log.exception("broker: poll cycle failed")

            try:
                from ..config_file import load as load_config
                interval = load_config().broker.poll_interval_seconds
            except Exception:
                interval = 30
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _poll_all(self) -> None:
        if not self._client.available:
            return

        endpoints = self._discover_endpoints()
        _, priv_pem = load_or_generate_private_key()

        for ep in endpoints:
            try:
                await self._poll_endpoint(ep, priv_pem)
            except Exception:
                log.exception("broker: error polling %s %s", ep["type"], ep["key"])

    async def _poll_endpoint(self, ep: dict[str, Any], priv_pem: str) -> None:
        broker_id = ep.get("broker_id")
        if not broker_id:
            return

        msg = await self._client.dequeue(broker_id)
        if msg is None:
            return

        try:
            if msg.encrypted_key and msg.encryption_tag:
                plaintext = rsa_decrypt(
                    priv_pem,
                    msg.encrypted_key,
                    msg.encrypted_body,
                    msg.encryption_iv,
                    msg.encryption_tag,
                )
            else:
                log.error("broker: message %s missing RSA fields", msg.id)
                await self._client.error(broker_id, msg.id, "missing encryption fields")
                return

            await self._dispatch(ep, plaintext)
            await self._client.commit(broker_id, msg.id)
            log.debug("broker: committed message %s from %s", msg.id, broker_id)
        except Exception as exc:
            log.exception("broker: failed to process message %s", msg.id)
            await self._client.error(broker_id, msg.id, str(exc)[:1000])

    async def _dispatch(self, ep: dict[str, Any], plaintext: str) -> None:
        ep_type = ep["type"]

        if ep_type == "kanban":
            await self._dispatch_kanban(ep, plaintext)
        elif ep_type == "workflow":
            await self._dispatch_workflow(ep, plaintext)
        else:
            log.warning("broker: unknown endpoint type %s", ep_type)

    async def _dispatch_kanban(self, ep: dict[str, Any], plaintext: str) -> None:
        try:
            from ..server.routes.webhook import _sanitise_payload
            from ..server.routes.webhook import _find_lane_by_token
        except ImportError:
            log.error("broker: cannot import kanban webhook handlers")
            return

        found = _find_lane_by_token(ep["local_token"])
        if found is None:
            log.warning("broker: kanban token %s not found", ep["local_token"])
            return

        board_path, lane_id, _ = found

        try:
            from .. import main as _main
            agent = getattr(_main, "_agent_instance", None)
        except Exception:
            agent = None

        card_title, card_body = await _sanitise_payload(plaintext, agent)

        from .. import vault_kanban
        vault_kanban.add_card(board_path, lane_id, card_title, card_body)
        log.info("broker: created card in kanban %s lane %s", board_path, lane_id)

    async def _dispatch_workflow(self, ep: dict[str, Any], plaintext: str) -> None:
        try:
            json.loads(plaintext)
            payload: dict[str, Any] = json.loads(plaintext)
        except Exception:
            payload = {"raw": plaintext}

        token = ep["local_token"]
        try:
            from ..workflows.store import WorkflowStore
            from ..workflows import parser
            from ..workflows.engine import WorkflowEngine
            from ..workflows.models import TriggerType
            from .. import vault as _vault
            from .. import home as _home

            wf_db = str(_home.workflow_runs_db())
            store = WorkflowStore(wf_db)
            result = store.lookup_webhook_token(token)
            if result is None:
                log.warning("broker: workflow token %s not found", token)
                return

            workflow_path, trigger_id = result
            content = _vault.read_file(workflow_path)
            raw = content.get("content", "") if isinstance(content, dict) else str(content)
            wf = parser.parse(raw)

            if not wf.enabled:
                log.warning("broker: workflow %s is disabled", workflow_path)
                return

            engine = WorkflowEngine(store)
            await engine.run_workflow(
                workflow_path=workflow_path,
                trigger_id=trigger_id,
                trigger_type=TriggerType.webhook,
                trigger_payload=payload,
                wf_def=wf,
            )
            log.info("broker: triggered workflow %s", workflow_path)
        except Exception:
            log.exception("broker: workflow dispatch failed for token %s", token)

    def _discover_endpoints(self) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        self._discover_kanban_endpoints(endpoints)
        self._discover_workflow_endpoints(endpoints)
        return endpoints

    def _discover_kanban_endpoints(self, out: list[dict[str, Any]]) -> None:
        try:
            from .. import vault_kanban
            boards = vault_kanban.list_boards()
            for bp in boards:
                try:
                    board = vault_kanban.read_board(bp["path"])
                except Exception:
                    continue
                for lane in board.lanes:
                    if (
                        lane.webhook_enabled
                        and lane.webhook_token
                        and getattr(lane, "broker_id", None)
                    ):
                        out.append({
                            "type": "kanban",
                            "key": f"{bp['path']}:{lane.id}",
                            "local_token": lane.webhook_token,
                            "broker_id": lane.broker_id,
                            "broker_slug": getattr(lane, "broker_slug", None),
                        })
        except Exception:
            log.exception("broker: kanban endpoint discovery failed")

    def _discover_workflow_endpoints(self, out: list[dict[str, Any]]) -> None:
        try:
            from ..workflows import parser
            from ..workflows.models import TriggerType
            from .. import vault as _vault
            from .. import home as _home

            vault_path = _home.vault_dir()
            if not vault_path:
                return

            import os
            for root, _dirs, files in os.walk(vault_path):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        content = _vault.read_file(fpath)
                        raw = content.get("content", "") if isinstance(content, dict) else str(content)
                        wf = parser.parse(raw)
                    except Exception:
                        continue
                    for trigger in wf.triggers:
                        if (
                            trigger.type == TriggerType.webhook
                            and trigger.token
                            and getattr(trigger, "broker_id", None)
                        ):
                            out.append({
                                "type": "workflow",
                                "key": f"{fpath}:{trigger.id}",
                                "local_token": trigger.token,
                                "broker_id": trigger.broker_id,
                                "broker_slug": getattr(trigger, "broker_slug", None),
                            })
        except Exception:
            log.exception("broker: workflow endpoint discovery failed")
