from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .client import BrokerClient
from .crypto import load_or_generate_private_key, rsa_decrypt

log = logging.getLogger(__name__)


class BrokerPoller:
    def __init__(
        self,
        client: BrokerClient,
        agent: Any = None,
        workflow_engine: Any = None,
    ) -> None:
        self._client = client
        self._agent = agent
        self._workflow_engine = workflow_engine
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

        try:
            from .sync import sync_broker_endpoints
            await sync_broker_endpoints(self._client)
        except Exception:
            log.exception("broker: initial sync failed")

        # Endpoint discovery + token registration is pure sync file/SQLite I/O
        # (vault walk, read_file, store.lookup_webhook_token) — run it off the
        # event loop so the always-open SSE streams can keep flushing.
        await asyncio.to_thread(self._ensure_webhook_tokens)

        while not self._stop_event.is_set():
            try:
                await self._poll_all()
            except Exception:
                log.exception("broker: poll cycle failed")

            try:
                from ..config_file import load_cached as load_config
                interval = load_config().broker.poll_interval_seconds
            except Exception:
                interval = 30
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def _ensure_webhook_tokens(self) -> None:
        if self._workflow_engine is None:
            return
        try:
            store = self._workflow_engine._store
        except AttributeError:
            return
        import datetime
        endpoints = self._discover_endpoints()
        for ep in endpoints:
            if ep["type"] != "workflow":
                continue
            token = ep.get("local_token")
            if not token:
                continue
            existing = store.lookup_webhook_token(token)
            if existing is not None:
                continue
            workflow_path = ep.get("workflow_path", ep.get("vault_path", ""))
            trigger_id = ep.get("trigger_id", "")
            if not workflow_path or not trigger_id:
                key = ep.get("key", "")
                if ":" in key:
                    workflow_path, trigger_id = key.rsplit(":", 1)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            store.register_webhook_token(token, workflow_path, trigger_id, now)
            log.info("broker: re-registered webhook token for %s", workflow_path)

    async def _poll_all(self) -> None:
        if not self._client.available:
            return

        # _discover_endpoints walks the entire vault (os.walk + read_file +
        # YAML parse for every .md) and load_or_generate_private_key reads a
        # file — all synchronous. Run them in a worker thread so the 30s poll
        # cycle doesn't freeze the event loop (and the open SSE streams).
        def _discover() -> tuple[list[dict[str, Any]], str]:
            endpoints = self._discover_endpoints()
            _, priv_pem = load_or_generate_private_key()
            return endpoints, priv_pem

        endpoints, priv_pem = await asyncio.to_thread(_discover)

        for ep in endpoints:
            if ep["type"] == "workflow" and not self._is_workflow_enabled(ep):
                log.info("broker: skipping disabled workflow %s", ep.get("workflow_path", ep.get("key", "")))
                continue
            try:
                await self._poll_endpoint(ep, priv_pem)
            except Exception:
                log.exception("broker: error polling %s %s", ep["type"], ep["key"])

    def _is_workflow_enabled(self, ep: dict[str, Any]) -> bool:
        fpath = ep.get("workflow_path", "")
        if not fpath:
            return True
        try:
            from ..workflows import parser
            from .. import vault as _vault
            content = _vault.read_file(fpath)
            raw = content.get("content", "") if isinstance(content, dict) else str(content)
            wf = parser.parse(raw)
            return wf.enabled
        except Exception:
            return True

    async def _poll_endpoint(self, ep: dict[str, Any], priv_pem: str) -> None:
        broker_id = ep.get("broker_id")
        if not broker_id:
            return

        while True:
            msg = await self._client.dequeue(broker_id)
            if msg is None:
                break

            dispatched = False
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
                    try:
                        await self._client.error(broker_id, msg.id, "missing encryption fields")
                    except Exception:
                        log.exception("broker: failed to error message %s", msg.id)
                    continue

                await self._dispatch(ep, plaintext)
                dispatched = True
                try:
                    await self._client.commit(broker_id, msg.id)
                except Exception:
                    log.exception("broker: failed to commit message %s", msg.id)
                    try:
                        await self._client.error(broker_id, msg.id, "commit failed")
                    except Exception:
                        log.exception("broker: failed to error message %s after commit failure", msg.id)
            except Exception as exc:
                log.exception("broker: failed to process message %s (dispatched=%s)", msg.id, dispatched)
                try:
                    await self._client.error(broker_id, msg.id, str(exc)[:1000])
                except Exception:
                    log.exception("broker: failed to error message %s", msg.id)

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
        card_title, card_body = await _sanitise_payload(plaintext, self._agent)

        from .. import vault_kanban
        vault_kanban.add_card(board_path, lane_id, card_title, card_body)
        log.info("broker: created card in kanban %s lane %s", board_path, lane_id)

    async def _dispatch_workflow(self, ep: dict[str, Any], plaintext: str) -> None:
        try:
            payload: dict[str, Any] = json.loads(plaintext)
        except Exception:
            payload = {"raw": plaintext}

        token = ep["local_token"]

        engine = self._workflow_engine
        if engine is None:
            return

        try:
            store = engine._store
        except AttributeError:
            return

        result = store.lookup_webhook_token(token)
        if result is None:
            log.warning("broker: webhook token %s not found in store for workflow dispatch", token)
            return

        workflow_path, trigger_id = result

        try:
            from .. import vault as _vault
            from ..workflows import parser
            from ..workflows.models import TriggerType

            content = _vault.read_file(workflow_path)
            raw = content.get("content", "") if isinstance(content, dict) else str(content)
            wf = parser.parse(raw)

            if not wf.enabled:
                return

            await engine.run_workflow(
                workflow_path=workflow_path,
                trigger_id=trigger_id,
                trigger_type=TriggerType.webhook,
                trigger_payload=payload,
                wf_def=wf,
            )
        except Exception:
            log.exception("broker: workflow dispatch failed for %s", workflow_path)

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
                        and lane.broker_id
                    ):
                        out.append({
                            "type": "kanban",
                            "key": f"{bp['path']}:{lane.id}",
                            "local_token": lane.webhook_token,
                            "broker_id": lane.broker_id,
                            "broker_slug": lane.broker_slug,
                        })
        except Exception:
            log.exception("broker: kanban endpoint discovery failed")

    def _discover_workflow_endpoints(self, out: list[dict[str, Any]]) -> None:
        try:
            from ..workflows import parser
            from ..workflows.models import TriggerType
            from .. import vault as _vault
            from .. import home as _home

            vault_path = _home.vault_root()
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
                            and trigger.broker_id
                        ):
                            out.append({
                                "type": "workflow",
                                "key": f"{fpath}:{trigger.id}",
                                "local_token": trigger.token,
                                "broker_id": trigger.broker_id,
                                "broker_slug": trigger.broker_slug,
                                "workflow_path": fpath,
                                "trigger_id": trigger.id,
                            })
        except Exception:
            log.exception("broker: workflow endpoint discovery failed")
