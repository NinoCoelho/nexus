"""Broker webhook management API — list, create, delete, assign, unassign."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ...broker.client import BrokerClient
from ...broker.crypto import load_or_generate_private_key
from ...broker.registry import get_registry
from ...broker.sync import delete_broker_endpoint

log = logging.getLogger(__name__)

router = APIRouter()


def _client(request: Request) -> BrokerClient:
    return BrokerClient()


@router.get("/broker/webhooks")
async def list_broker_webhooks(request: Request) -> dict:
    client = _client(request)
    if not client.available:
        from ... import secrets as _secrets
        return {
            "connected": False,
            "signed_in": bool(_secrets.get("nexus_api_key")),
            "webhooks": [],
            "quota": None,
        }

    try:
        remote = await client.list_webhooks()
    except Exception:
        log.exception("broker: list_webhooks failed")
        raise HTTPException(status_code=502, detail="failed to list broker webhooks")

    registry = get_registry()
    remote_by_id = {w.id: w for w in remote}
    local_all = registry.list_all()

    out = []
    seen_ids: set[str] = set()

    for row in local_all:
        bid = row["broker_id"]
        seen_ids.add(bid)
        rw = remote_by_id.get(bid)
        assigned = bool(row.get("endpoint_type"))
        out.append({
            "broker_id": bid,
            "broker_slug": row["broker_slug"],
            "name": row.get("name") or (rw.name if rw else ""),
            "url": f"{client._base_url}/wh/{row['broker_slug']}",
            "assigned": assigned,
            "assignment": _assignment_view(row) if assigned else None,
            "is_active": rw.is_active if rw else False,
            "exists_on_broker": rw is not None,
            "message_count": rw.message_count if rw else 0,
            "created_at": row.get("created_at"),
            "last_verified_at": row.get("last_verified_at"),
        })

    for rw in remote:
        if rw.id not in seen_ids:
            out.append({
                "broker_id": rw.id,
                "broker_slug": rw.slug,
                "name": rw.name,
                "url": rw.url,
                "assigned": False,
                "assignment": None,
                "is_active": rw.is_active,
                "exists_on_broker": True,
                "message_count": rw.message_count,
                "created_at": None,
                "last_verified_at": None,
                "orphan": True,
            })

    return {
        "connected": True,
        "signed_in": True,
        "webhooks": out,
        "quota": {
            "used": len(remote),
            "local_assigned": registry.count_assigned(),
            "local_unassigned": registry.count_active() - registry.count_assigned(),
        },
    }


@router.post("/broker/webhooks")
async def create_broker_webhook(request: Request) -> dict:
    body = await request.json()
    name = body.get("name", "Nexus Webhook")

    client = _client(request)
    if not client.available:
        raise HTTPException(status_code=400, detail="broker not connected")

    pub_pem, _ = load_or_generate_private_key()
    try:
        wh = await client.create_webhook(
            name=name,
            public_key_pem=pub_pem,
            key_type="rsa-2048",
        )
    except Exception:
        log.exception("broker: create_webhook failed")
        raise HTTPException(status_code=502, detail="failed to create broker webhook")

    registry = get_registry()
    registry.register(
        broker_id=wh.id,
        broker_slug=wh.slug,
        name=name,
    )

    return {
        "broker_id": wh.id,
        "broker_slug": wh.slug,
        "name": name,
        "url": wh.url,
        "assigned": False,
    }


@router.delete("/broker/webhooks/{broker_id}")
async def delete_broker_webhook(broker_id: str, request: Request) -> dict:
    client = _client(request)
    if not client.available:
        raise HTTPException(status_code=400, detail="broker not connected")

    registry = get_registry()
    local = registry.get(broker_id)
    if local and local.get("endpoint_type"):
        _clear_local_assignment(local, request)

    await delete_broker_endpoint(client, broker_id)
    return {"ok": True}


@router.post("/broker/webhooks/{broker_id}/assign")
async def assign_broker_webhook(broker_id: str, request: Request) -> dict:
    body = await request.json()
    target_type = body.get("type")
    path = body.get("path")
    lane_id = body.get("lane_id")
    trigger_id = body.get("trigger_id")

    if target_type not in ("kanban", "workflow"):
        raise HTTPException(status_code=400, detail="type must be 'kanban' or 'workflow'")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")

    client = _client(request)
    registry = get_registry()

    local = registry.get(broker_id)
    if local is None:
        raise HTTPException(status_code=404, detail="webhook not found in local registry")
    if local.get("endpoint_type"):
        raise HTTPException(status_code=409, detail="webhook is already assigned")

    if target_type == "kanban":
        return await _assign_kanban(client, registry, broker_id, local, path, lane_id, request)
    else:
        return await _assign_workflow(client, registry, broker_id, local, path, trigger_id, request)


@router.post("/broker/webhooks/{broker_id}/unassign")
async def unassign_broker_webhook(broker_id: str, request: Request) -> dict:
    registry = get_registry()

    local = registry.get(broker_id)
    if local is None:
        raise HTTPException(status_code=404, detail="webhook not found in local registry")
    if not local.get("endpoint_type"):
        raise HTTPException(status_code=400, detail="webhook is not assigned")

    _clear_local_assignment(local, request)
    registry.unassign(broker_id)
    return {"ok": True}


async def _assign_kanban(
    client: BrokerClient, registry, broker_id: str, local: dict,
    board_path: str, lane_id: str, request: Request,
) -> dict:
    import secrets as _secrets_mod
    from ... import vault_kanban

    try:
        board = vault_kanban.read_board(board_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="board not found")
    from ...vault_kanban.lanes import _find_lane
    lane = _find_lane(board, lane_id)
    if lane is None:
        raise HTTPException(status_code=404, detail="lane not found")

    token = lane.webhook_token or _secrets_mod.token_hex(16)
    endpoint_key = f"{board_path}:{lane_id}"

    vault_kanban.update_lane(board_path, lane_id, {
        "webhook_token": token,
        "webhook_enabled": True,
        "broker_id": broker_id,
        "broker_slug": local["broker_slug"],
    })

    registry.assign(
        broker_id,
        endpoint_type="kanban",
        endpoint_key=endpoint_key,
        local_token=token,
        vault_path=board_path,
    )

    from ...config_file import load as load_config
    broker_base = load_config().broker.url.rstrip("/")
    url = f"{broker_base}/wh/{local['broker_slug']}"

    return {"ok": True, "url": url, "token": token}


async def _assign_workflow(
    client: BrokerClient, registry, broker_id: str, local: dict,
    workflow_path: str, trigger_id: str, request: Request,
) -> dict:
    from ... import vault as _vault
    from ...workflows import parser

    try:
        content = _vault.read_file(workflow_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="workflow not found")

    raw = content.get("content", "") if isinstance(content, dict) else str(content)
    wf = parser.parse(raw)

    trigger = next((t for t in wf.triggers if t.id == trigger_id), None)
    if trigger is None:
        raise HTTPException(status_code=404, detail="trigger not found")

    from ...workflows.models import TriggerType
    trigger.type = TriggerType.webhook
    if not trigger.token:
        import secrets as _secrets_mod
        trigger.token = _secrets_mod.token_hex(16)
    trigger.broker_id = broker_id
    trigger.broker_slug = local["broker_slug"]

    md = parser.serialize(wf, original_content=raw)
    _vault.write_file(workflow_path, md)

    endpoint_key = f"{workflow_path}:{trigger_id}"
    registry.assign(
        broker_id,
        endpoint_type="workflow",
        endpoint_key=endpoint_key,
        local_token=trigger.token,
        vault_path=workflow_path,
    )

    from ...config_file import load as load_config
    broker_base = load_config().broker.url.rstrip("/")
    url = f"{broker_base}/wh/{local['broker_slug']}"

    store = getattr(request.app.state, "workflow_store", None)
    if store:
        import datetime
        store.register_webhook_token(
            trigger.token, workflow_path, trigger_id,
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

    return {"ok": True, "url": url, "token": trigger.token}


def _clear_local_assignment(local: dict, request: Request) -> None:
    etype = local.get("endpoint_type")
    ekey = local.get("endpoint_key", "")
    if etype == "kanban" and ":" in ekey:
        board_path, lane_id = ekey.rsplit(":", 1)
        try:
            from ... import vault_kanban
            vault_kanban.update_lane(board_path, lane_id, {
                "webhook_token": None,
                "webhook_enabled": False,
                "broker_id": None,
                "broker_slug": None,
            })
        except Exception:
            log.exception("broker: failed to clear kanban assignment for %s", ekey)
    elif etype == "workflow" and ":" in ekey:
        wf_path, trigger_id = ekey.rsplit(":", 1)
        try:
            from ...workflows import parser
            from ... import vault as _vault
            content = _vault.read_file(wf_path)
            raw = content.get("content", "") if isinstance(content, dict) else str(content)
            wf = parser.parse(raw)
            for t in wf.triggers:
                if t.id == trigger_id:
                    t.broker_id = None
                    t.broker_slug = None
                    break
            md = parser.serialize(wf, original_content=raw)
            _vault.write_file(wf_path, md)
        except Exception:
            log.exception("broker: failed to clear workflow assignment for %s", ekey)


def _assignment_view(row: dict) -> dict | None:
    etype = row.get("endpoint_type")
    ekey = row.get("endpoint_key", "")
    if not etype or not ekey:
        return None
    if etype == "kanban" and ":" in ekey:
        path, lane_id = ekey.rsplit(":", 1)
        return {"type": "kanban", "path": path, "lane_id": lane_id}
    if etype == "workflow" and ":" in ekey:
        path, trigger_id = ekey.rsplit(":", 1)
        return {"type": "workflow", "path": path, "trigger_id": trigger_id}
    return {"type": etype, "key": ekey}
