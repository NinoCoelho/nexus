"""Broker webhook reconciliation — provision, validate, clean up.

Called from:
  * Status watcher — when brokerApiKey first arrives.
  * Poller startup — reconcile all existing webhooks.
  * Kanban/workflow routes — on enable / delete.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import BrokerClient
from .provision import ensure_broker_endpoint
from .registry import WebhookRegistry, get_registry

log = logging.getLogger(__name__)


async def sync_broker_endpoints(client: BrokerClient) -> dict[str, list[str]]:
    """Scan all local webhooks, provision missing ones, verify existing ones.

    Returns ``{created: [...], verified: [...], failed: [...], evicted: [...]}
    """
    if not client.available:
        return {"created": [], "verified": [], "failed": [], "evicted": []}

    registry = get_registry()
    result: dict[str, list[str]] = {
        "created": [], "verified": [], "failed": [], "evicted": [],
    }

    needed = _discover_needed_endpoints()

    for ep in needed:
        existing = registry.get_by_key(ep["endpoint_type"], ep["endpoint_key"])
        if existing and existing.get("is_active"):
            try:
                wh = await client.get_webhook(existing["broker_id"])
                if wh and wh.is_active:
                    registry.mark_verified(existing["broker_id"])
                    result["verified"].append(existing["broker_id"])
                    continue
            except Exception:
                log.warning("broker sync: verify failed for %s", existing["broker_id"])
            registry.mark_gone(existing["broker_id"])

        broker_wh = await ensure_broker_endpoint(
            client=client,
            endpoint_type=ep["endpoint_type"],
            endpoint_key=ep["endpoint_key"],
            name=ep["name"],
            existing_broker_id=None,
        )
        if broker_wh:
            registry.register(
                broker_id=broker_wh.id,
                broker_slug=broker_wh.slug,
                endpoint_type=ep["endpoint_type"],
                endpoint_key=ep["endpoint_key"],
                name=ep["name"],
                local_token=ep.get("local_token"),
                vault_path=ep.get("vault_path", ""),
            )
            _write_broker_fields(ep, broker_wh)
            result["created"].append(broker_wh.id)
        else:
            result["failed"].append(ep["endpoint_key"])

    stale = _find_stale_endpoints(registry, needed)
    for row in stale:
        try:
            await client.delete_webhook(row["broker_id"])
            registry.remove(row["broker_id"])
            result["evicted"].append(row["broker_id"])
            log.info("broker sync: evicted stale endpoint %s (%s %s)",
                     row["broker_id"], row["endpoint_type"], row["endpoint_key"])
        except Exception:
            log.exception("broker sync: failed to evict %s", row["broker_id"])

    if any(result.values()):
        log.info(
            "broker sync: created=%d verified=%d failed=%d evicted=%d",
            len(result["created"]), len(result["verified"]),
            len(result["failed"]), len(result["evicted"]),
        )
    return result


async def delete_broker_endpoint(client: BrokerClient, broker_id: str) -> bool:
    registry = get_registry()
    try:
        await client.delete_webhook(broker_id)
    except Exception:
        log.exception("broker: failed to delete webhook %s", broker_id)
        return False
    registry.remove(broker_id)
    log.info("broker: deleted webhook %s", broker_id)
    return True


async def cleanup_kanban_board(client: BrokerClient, board_path: str) -> int:
    registry = get_registry()
    removed = 0
    rows = registry.remove_by_vault_path(board_path)
    for row in rows:
        try:
            await client.delete_webhook(row["broker_id"])
            removed += 1
        except Exception:
            log.exception("broker: failed to delete %s during board cleanup", row["broker_id"])
    if removed:
        log.info("broker: cleaned up %d webhook(s) for deleted board %s", removed, board_path)
    return removed


async def cleanup_workflow(client: BrokerClient, workflow_path: str) -> int:
    return await cleanup_kanban_board(client, workflow_path)


async def cleanup_kanban_lane(
    client: BrokerClient, board_path: str, lane_id: str,
) -> bool:
    registry = get_registry()
    row = registry.remove_by_key("kanban", f"{board_path}:{lane_id}")
    if row is None:
        return False
    try:
        await client.delete_webhook(row["broker_id"])
    except Exception:
        log.exception("broker: failed to delete %s during lane cleanup", row["broker_id"])
        return False
    log.info("broker: cleaned up webhook for lane %s in %s", lane_id, board_path)
    return True


async def cleanup_workflow_trigger(
    client: BrokerClient, workflow_path: str, trigger_id: str,
) -> bool:
    registry = get_registry()
    row = registry.remove_by_key("workflow", f"{workflow_path}:{trigger_id}")
    if row is None:
        return False
    try:
        await client.delete_webhook(row["broker_id"])
    except Exception:
        log.exception("broker: failed to delete %s during trigger cleanup", row["broker_id"])
        return False
    log.info("broker: cleaned up webhook for trigger %s in %s", trigger_id, workflow_path)
    return True


def _discover_needed_endpoints() -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []

    try:
        from .. import vault_kanban
        boards = vault_kanban.list_boards()
        for bp in boards:
            path = bp["path"]
            try:
                board = vault_kanban.read_board(path)
            except Exception:
                continue
            for lane in board.lanes:
                if lane.webhook_token:
                    endpoints.append({
                        "endpoint_type": "kanban",
                        "endpoint_key": f"{path}:{lane.id}",
                        "name": f"Kanban: {lane.title}",
                        "local_token": lane.webhook_token,
                        "vault_path": path,
                        "lane_id": lane.id,
                        "board_path": path,
                        "broker_id": lane.broker_id,
                        "broker_slug": lane.broker_slug,
                        "webhook_enabled": lane.webhook_enabled,
                    })
    except Exception:
        log.exception("broker sync: kanban discovery failed")

    try:
        from ..workflows import parser
        from ..workflows.models import TriggerType
        from .. import vault as _vault
        from .. import home as _home
        import os

        vault_path = _home.vault_root()
        if vault_path:
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
                        if trigger.type == TriggerType.webhook and trigger.token:
                            endpoints.append({
                                "endpoint_type": "workflow",
                                "endpoint_key": f"{fpath}:{trigger.id}",
                                "name": f"Workflow: {wf.title}",
                                "local_token": trigger.token,
                                "vault_path": fpath,
                                "trigger_id": trigger.id,
                                "workflow_path": fpath,
                                "broker_id": trigger.broker_id,
                                "broker_slug": trigger.broker_slug,
                            })
    except Exception:
        log.exception("broker sync: workflow discovery failed")

    return endpoints


def _find_stale_endpoints(
    registry: WebhookRegistry,
    needed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    needed_keys = {(ep["endpoint_type"], ep["endpoint_key"]) for ep in needed}
    stale: list[dict[str, Any]] = []
    for row in registry.list_all(active_only=True):
        key = (row["endpoint_type"], row["endpoint_key"])
        if key not in needed_keys:
            stale.append(row)
    return stale


def _write_broker_fields(ep: dict[str, Any], broker_wh: Any) -> None:
    if ep["endpoint_type"] == "kanban":
        try:
            from .. import vault_kanban
            updates: dict[str, Any] = {
                "broker_id": broker_wh.id,
                "broker_slug": broker_wh.slug,
            }
            vault_kanban.update_lane(
                ep["board_path"], ep["lane_id"], updates,
            )
        except Exception:
            log.exception("broker sync: failed to write broker fields for kanban lane")
    elif ep["endpoint_type"] == "workflow":
        try:
            from ..workflows import parser
            from .. import vault as _vault

            fpath = ep["workflow_path"]
            content = _vault.read_file(fpath)
            raw = content.get("content", "") if isinstance(content, dict) else str(content)
            wf = parser.parse(raw)
            for t in wf.triggers:
                if t.id == ep["trigger_id"]:
                    t.broker_id = broker_wh.id
                    t.broker_slug = broker_wh.slug
                    break
            md = parser.serialize(wf, original_content=raw)
            _vault.write_file(fpath, md)
        except Exception:
            log.exception("broker sync: failed to write broker fields for workflow trigger")
