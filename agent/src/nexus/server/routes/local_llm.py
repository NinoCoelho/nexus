"""Routes for local LLM management.

Endpoints under /local/* for hardware probing, HuggingFace model search,
download management, and llama-server lifecycle control.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_agent, get_app_state

router = APIRouter()

_MODELS_DIR = Path.home() / ".nexus" / "models" / "llm"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class DownloadRequest(BaseModel):
    repo_id: str
    filename: str


class ActivateRequest(BaseModel):
    filename: str


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

@router.get("/local/hardware")
async def get_hardware() -> dict[str, Any]:
    """Return hardware capabilities of the current host."""
    from ...local_llm.hardware import probe
    return probe()


# ---------------------------------------------------------------------------
# HuggingFace search
# ---------------------------------------------------------------------------

@router.get("/local/hf/search")
async def hf_search(q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search HuggingFace Hub for GGUF model repositories."""
    from ...local_llm.hf_search import search_gguf_repos, HfSearchError
    try:
        return search_gguf_repos(q, limit=limit)
    except HfSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/local/hf/repo/{owner}/{repo}/files")
async def hf_repo_files(owner: str, repo: str) -> list[dict[str, Any]]:
    """List GGUF files in a HuggingFace repository, annotated with fits_in_ram."""
    from ...local_llm.hf_search import list_repo_ggufs, HfSearchError
    from ...local_llm.hardware import probe

    repo_id = f"{owner}/{repo}"
    try:
        files = list_repo_ggufs(repo_id)
    except HfSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    hw = probe()
    ram_bytes = hw["ram_gb"] * 1e9
    for f in files:
        # Heuristic: file needs ~30% extra headroom for KV cache
        f["fits_in_ram"] = (f["size_bytes"] * 1.3) < ram_bytes

    return files


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

@router.post("/local/download")
async def start_download(body: DownloadRequest) -> dict[str, str]:
    """Enqueue a background download of a GGUF file from HuggingFace."""
    from ...local_llm.downloader import start_download as _start

    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    task = _start(body.repo_id, body.filename, _MODELS_DIR)
    return {"task_id": task.task_id}


@router.get("/local/downloads")
async def list_downloads() -> list[dict[str, Any]]:
    """List all download tasks (active and completed)."""
    from ...local_llm.downloader import list_tasks
    tasks = list_tasks()
    return [
        {
            "task_id": t.task_id,
            "repo_id": t.repo_id,
            "filename": t.filename,
            "total_bytes": t.total_bytes,
            "downloaded_bytes": t.downloaded_bytes,
            "status": t.status,
            "error": t.error,
        }
        for t in tasks
    ]


# ---------------------------------------------------------------------------
# Installed models
# ---------------------------------------------------------------------------

@router.get("/local/installed")
async def list_installed() -> list[dict[str, Any]]:
    """List GGUF files installed in ~/.nexus/models/llm/."""
    from ...local_llm import manager

    active_info = manager.current()
    active_path = active_info["model_path"] if active_info else None

    if not _MODELS_DIR.is_dir():
        return []

    results = []
    for p in sorted(_MODELS_DIR.glob("*.gguf")):
        results.append({
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "is_active": str(p) == active_path,
        })
    return results


@router.post("/local/activate")
async def activate_model(
    body: ActivateRequest,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent: Any = Depends(get_agent),
) -> dict[str, Any]:
    """Start llama-server with the specified installed GGUF file.

    Stops any currently running local model, starts the new one, updates
    ``~/.nexus/config.toml``, and rebuilds the provider registry.
    """
    from ...local_llm import manager
    from .config import _rebuild_registry

    model_path = _MODELS_DIR / body.filename
    if not model_path.is_file():
        raise HTTPException(status_code=404, detail=f"Model file not found: {body.filename}")

    model_name = _slugify(model_path.stem)

    try:
        port, name = manager.restart(model_path, model_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    manager.seed_config(name, port)

    try:
        from ...config_file import load as load_cfg
        new_cfg = load_cfg()
        _rebuild_registry(new_cfg, app_state, agent)
    except Exception as exc:
        # Non-fatal: server is running, config is written; registry rebuild
        # will be retried on next restart.
        import logging
        logging.getLogger(__name__).warning("registry rebuild after activate failed: %s", exc)

    return {"model_name": name, "port": port}


@router.delete("/local/installed/{filename}")
async def delete_installed(filename: str) -> dict[str, str]:
    """Delete an installed GGUF file.

    Returns 409 if the model is currently active.
    """
    from ...local_llm import manager

    active_info = manager.current()
    if active_info and active_info.get("model_path") == str(_MODELS_DIR / filename):
        raise HTTPException(status_code=409, detail="Cannot delete the currently active model.")

    model_path = _MODELS_DIR / filename
    if not model_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    try:
        model_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"deleted": filename}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Convert a model stem to a lowercase slug suitable for config IDs."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug
