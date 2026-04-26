"""Routes for model management: /models CRUD, /models/suggest-tier, /routing, /models/roles."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_agent, get_app_state
from ..schemas import ModelRolePayload
from .config import _rebuild_registry

router = APIRouter()


@router.get("/models")
async def list_models(app_state: dict[str, Any] = Depends(get_app_state)) -> list[dict[str, Any]]:
    cfg = app_state["cfg"]
    if not cfg:
        return []
    return [m.model_dump() for m in cfg.models]


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def add_model(
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
) -> dict[str, Any]:
    from ...config_file import load as load_cfg, save as save_cfg, ModelEntry
    from ...agent.model_profiles import suggest_tier
    cfg = app_state["cfg"] or load_cfg()
    # Legacy callers may still send `strengths` — drop it silently.
    body.pop("strengths", None)
    if not body.get("tier"):
        body["tier"] = suggest_tier(body.get("model_name", ""))
    m = ModelEntry(**body)
    cfg.models.append(m)
    # Auto-set as default if nothing is set yet — the DWIM path: a user
    # who just configured their first model expects it to be usable.
    if not cfg.agent.default_model:
        cfg.agent.default_model = m.id
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)
    return m.model_dump()


@router.patch("/models/{model_id:path}")
async def patch_model(
    model_id: str,
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
) -> dict[str, Any]:
    from ...config_file import load as load_cfg, save as save_cfg
    cfg = app_state["cfg"] or load_cfg()
    for i, m in enumerate(cfg.models):
        if m.id == model_id:
            # id and provider are immutable after creation — avoid cascading
            # breakage (role assignments, last_used_model references).
            updates = {k: v for k, v in body.items() if k in {"model_name", "tags", "tier", "notes", "is_embedding_capable", "context_window"}}
            if "tier" in updates and updates["tier"] not in ("fast", "balanced", "heavy"):
                raise HTTPException(400, "tier must be fast|balanced|heavy")
            if "context_window" in updates:
                try:
                    cw = int(updates["context_window"])
                except (TypeError, ValueError):
                    raise HTTPException(400, "context_window must be an integer")
                if cw < 0:
                    raise HTTPException(400, "context_window must be >= 0 (0 = use server default)")
                updates["context_window"] = cw
            cfg.models[i] = m.model_copy(update=updates)
            save_cfg(cfg)
            _rebuild_registry(cfg, app_state, a)
            return cfg.models[i].model_dump()
    raise HTTPException(404, f"model {model_id!r} not found")


@router.post("/models/suggest-tier")
async def suggest_tier_endpoint(body: dict[str, Any]) -> dict[str, str]:
    from ...agent.model_profiles import suggest_tier, suggestion_source
    name = body.get("model_name", "") or ""
    return {"tier": suggest_tier(name), "source": suggestion_source(name)}


@router.delete("/models/{model_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: str,
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
) -> None:
    from ...config_file import load as load_cfg, save as save_cfg
    cfg = app_state["cfg"] or load_cfg()
    cfg.models = [m for m in cfg.models if m.id != model_id]
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)


@router.get("/routing")
async def get_routing(app_state: dict[str, Any] = Depends(get_app_state)) -> dict[str, Any]:
    cfg = app_state["cfg"]
    pr = app_state["prov_reg"]
    available = pr.available_model_ids() if pr else []
    if not cfg:
        return {"default_model": None, "last_used_model": None,
                "available_models": available}
    embedding_id = cfg.graphrag.embedding_model_id
    chat_available = [m for m in available if m != embedding_id]
    return {
        "default_model": cfg.agent.default_model,
        "last_used_model": cfg.agent.last_used_model,
        "available_models": chat_available,
        "embedding_model_id": embedding_id,
        "extraction_model_id": cfg.graphrag.extraction_model_id,
    }


@router.put("/routing")
async def set_routing(
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
) -> dict[str, Any]:
    from ...config_file import load as load_cfg, save as save_cfg
    cfg = app_state["cfg"] or load_cfg()
    if "default_model" in body:
        cfg.agent.default_model = body["default_model"]
    if "last_used_model" in body:
        cfg.agent.last_used_model = body["last_used_model"]
    if "embedding_model_id" in body:
        cfg.graphrag.embedding_model_id = body["embedding_model_id"]
    if "extraction_model_id" in body:
        cfg.graphrag.extraction_model_id = body["extraction_model_id"]
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)
    if "embedding_model_id" in body or "extraction_model_id" in body:
        from ...agent.graphrag_manager import initialize as graphrag_init
        try:
            await graphrag_init(cfg)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("[graphrag] reinit failed", exc_info=True)
    return {
        "default_model": cfg.agent.default_model,
        "last_used_model": cfg.agent.last_used_model,
        "embedding_model_id": cfg.graphrag.embedding_model_id,
        "extraction_model_id": cfg.graphrag.extraction_model_id,
    }


@router.put("/models/roles")
async def set_model_role(
    body: ModelRolePayload,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict[str, str]:
    from ...config_file import load as load_cfg, save as save_cfg
    cfg = app_state["cfg"] or load_cfg()
    new_id = body.model_id or ""
    if body.role == "embedding":
        if new_id:
            target = next((m for m in cfg.models if m.id == new_id), None)
            if target is None:
                raise HTTPException(404, f"model {new_id!r} not found")
            if not target.is_embedding_capable:
                raise HTTPException(
                    400,
                    f"model {new_id!r} is not marked as embedding-capable — "
                    "edit the model and enable 'Embedding capable' first.",
                )
        cfg.graphrag.embedding_model_id = new_id
    elif body.role == "extraction":
        cfg.graphrag.extraction_model_id = new_id
    else:
        raise HTTPException(400, f"Unknown role: {body.role}")
    save_cfg(cfg)
    app_state["cfg"] = cfg
    from ...agent.graphrag_manager import initialize as graphrag_init
    try:
        await graphrag_init(cfg)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("[graphrag] reinit failed", exc_info=True)
    return {"role": body.role, "model_id": new_id}
