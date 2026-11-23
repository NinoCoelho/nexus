"""Routes for in-process MLX model management, under ``/local/mlx/*``.

Parallel to ``/local/*`` (llama.cpp) rather than discriminated by a ``runtime``
field, deliberately. ``GET /local/installed`` returns a GGUF-shaped record whose
unit of identity is a **filename** (``is_mmproj``, ``has_mamba_layers``,
``has_mtp``, ``is_shard_group``, ``parts``, ``port``); MLX's unit of identity is
a **snapshot directory**, and ``port`` does not exist for an in-process
provider. Threading a discriminator through would make half of every record
conditionally valid and reshape four existing test modules' fixtures. A
separate router costs one file and breaks nothing.

``GET /local/hardware`` is runtime-agnostic and is **not** duplicated here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_agent, get_app_state

log = logging.getLogger(__name__)

router = APIRouter()


class RepoRequest(BaseModel):
    repo_id: str


class ActivateRequest(BaseModel):
    slug: str
    #: Force this model to become ``[agent].default_model``. Left unset, a
    #: tool-capable model becomes the default only when nothing valid is set,
    #: and a tool-less model never does.
    set_default: bool | None = None
    #: Skip the fits-in-memory refusal. The check is a guard, not a law — a
    #: user who knows their machine can override it.
    force: bool = False


def _available_or_503() -> None:
    from ...agent.llm.mlx_runtime import MlxRuntime

    ok, reason = MlxRuntime.available()
    if not ok:
        raise HTTPException(status_code=503, detail=reason)


# ---------------------------------------------------------------------------
# Availability + status
# ---------------------------------------------------------------------------


@router.get("/local/mlx/available")
async def mlx_available() -> dict[str, Any]:
    """Whether MLX can run here. The UI hides the whole section when false.

    Never 503s — "can you do this at all" must be answerable everywhere,
    including on the Linux box someone is running the server on.
    """
    from ...agent.llm.mlx_runtime import MlxRuntime

    ok, reason = MlxRuntime.available()
    return {"available": ok, "reason": reason}


@router.get("/local/mlx/status")
async def mlx_status() -> dict[str, Any]:
    """Resident model, prefix-cache stats, queue depth."""
    from ...agent.llm.mlx_runtime import MlxRuntime

    ok, reason = MlxRuntime.available()
    if not ok:
        return {"available": False, "reason": reason, "loaded": False}
    status = MlxRuntime.get().status()
    status["available"] = True
    return status


@router.post("/local/mlx/unload")
async def mlx_unload() -> dict[str, Any]:
    """Evict the resident model now, freeing unified memory."""
    _available_or_503()
    from ...agent.llm.mlx_runtime import MlxRuntime

    await MlxRuntime.get().unload()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@router.get("/local/mlx/hf/search")
async def mlx_hf_search(q: str, limit: int = 20) -> list[dict[str, Any]]:
    from ...local_llm.mlx_hf_search import HfSearchError, search_mlx_repos

    try:
        return search_mlx_repos(q, limit=limit)
    except HfSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/local/mlx/hf/repo/{owner}/{repo}")
async def mlx_repo_details(owner: str, repo: str) -> dict[str, Any]:
    """Quantization, window and a fits-in-memory verdict before downloading.

    Reads only ``config.json`` — a few KB — which is what makes a 20 GB
    download an informed choice rather than a gamble.
    """
    from ...local_llm.mlx_hf_search import HfSearchError, repo_details

    try:
        return repo_details(f"{owner}/{repo}")
    except HfSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


@router.post("/local/mlx/download")
async def mlx_download(body: RepoRequest) -> dict[str, Any]:
    from ...local_llm.mlx_downloader import start_snapshot_download

    repo_id = (body.repo_id or "").strip()
    if not repo_id or "/" not in repo_id:
        raise HTTPException(status_code=422, detail="repo_id must look like 'owner/name'")
    task = start_snapshot_download(repo_id)
    return {"task_id": task.task_id, "repo_id": task.repo_id, "slug": task.filename}


@router.get("/local/mlx/downloads")
async def mlx_downloads() -> list[dict[str, Any]]:
    from ...local_llm.mlx_downloader import list_tasks

    return [
        {
            "task_id": t.task_id,
            "repo_id": t.repo_id,
            "slug": t.filename,
            "total_bytes": t.total_bytes,
            "downloaded_bytes": t.downloaded_bytes,
            "status": t.status,
            "error": t.error,
        }
        for t in list_tasks()
    ]


@router.post("/local/mlx/download/{task_id}/cancel")
async def mlx_download_cancel(task_id: str) -> dict[str, Any]:
    from ...local_llm.mlx_downloader import cancel_download

    return {"cancelled": cancel_download(task_id)}


# ---------------------------------------------------------------------------
# Installed models
# ---------------------------------------------------------------------------


@router.get("/local/mlx/installed")
async def mlx_installed() -> list[dict[str, Any]]:
    from ...local_llm import mlx_manager

    active = mlx_manager.active_model_ids()
    out: list[dict[str, Any]] = []
    for info in mlx_manager.list_installed():
        row = info.to_public()
        row["is_active"] = mlx_manager.model_id_for(info.slug) in active
        row["model_id"] = mlx_manager.model_id_for(info.slug)
        out.append(row)
    return out


@router.post("/local/mlx/activate")
async def mlx_activate(
    body: ActivateRequest,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent=Depends(get_agent),
) -> dict[str, Any]:
    """Register an installed snapshot as a provider + model, then reload.

    Refuses (409) when the model cannot fit in unified memory, or when a
    tool-less model is being made the default — the agent loop is tool-driven,
    so a tool-less chat model yields a chatbot, not an agent. Both refusals are
    overridable: ``force`` for the memory check, and a tool-less model can
    still be activated as long as it isn't the default.
    """
    _available_or_503()
    from ...agent.llm.mlx_support import estimate_resident_gb, fits_in_unified_memory
    from ...local_llm import mlx_manager
    from ...local_llm.hardware import probe
    from .config import _rebuild_registry

    info = mlx_manager.find_installed(body.slug)
    if info is None:
        raise HTTPException(status_code=404, detail=f"no installed MLX model {body.slug!r}")

    if not body.force:
        need = estimate_resident_gb(
            info.local_dir, context_tokens=info.max_position_embeddings or 8192
        )
        fits, reason = fits_in_unified_memory(need, probe())
        if not fits:
            raise HTTPException(status_code=409, detail=reason)

    if body.set_default and not info.supports_tools:
        raise HTTPException(
            status_code=409,
            detail=(
                "this model's chat template has no tool-calling support, so it "
                "cannot be the agent's default model. It can still be used as "
                "the vision model."
            ),
        )

    model_id = mlx_manager.add_to_config(info, set_default=body.set_default)

    try:
        from ...config_file import load as load_cfg

        _rebuild_registry(load_cfg(), app_state, agent)
    except Exception as exc:
        log.warning("[mlx] registry rebuild after activate failed: %s", exc)

    return {"model_id": model_id, "slug": info.slug, "supports_tools": info.supports_tools}


@router.post("/local/mlx/deactivate")
async def mlx_deactivate(
    body: ActivateRequest,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent=Depends(get_agent),
) -> dict[str, Any]:
    """Unregister a model and evict it if it is the one currently resident."""
    from ...local_llm import mlx_manager
    from .config import _rebuild_registry

    mlx_manager.remove_from_config(body.slug)

    try:
        from ...agent.llm.mlx_runtime import MlxRuntime

        ok, _reason = MlxRuntime.available()
        if ok:
            runtime = MlxRuntime.get()
            resident = (runtime.status() or {}).get("model_path") or ""
            if resident and resident.endswith(body.slug):
                await runtime.unload()
    except Exception as exc:  # pragma: no cover - eviction is best effort
        log.debug("[mlx] unload after deactivate skipped: %s", exc)

    try:
        from ...config_file import load as load_cfg

        _rebuild_registry(load_cfg(), app_state, agent)
    except Exception as exc:
        log.warning("[mlx] registry rebuild after deactivate failed: %s", exc)

    return {"ok": True, "slug": body.slug}


@router.delete("/local/mlx/installed/{slug}")
async def mlx_delete(
    slug: str,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent=Depends(get_agent),
) -> dict[str, Any]:
    """Deactivate and delete a snapshot from disk."""
    from ...local_llm import mlx_manager
    from .config import _rebuild_registry

    removed = mlx_manager.delete_installed(slug)

    try:
        from ...config_file import load as load_cfg

        _rebuild_registry(load_cfg(), app_state, agent)
    except Exception as exc:
        log.warning("[mlx] registry rebuild after delete failed: %s", exc)

    if not removed:
        raise HTTPException(status_code=404, detail=f"no installed MLX model {slug!r}")
    return {"deleted": True, "slug": slug}
