"""Routes for local LLM management.

Endpoints under /local/* for hardware probing, HuggingFace model search,
download management, and llama-server lifecycle control.

Multiple llama-server processes can run concurrently — one per installed
GGUF — each registered as ``providers.local-<slug>`` while live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_agent, get_app_state

router = APIRouter()

_MODELS_DIR = Path.home() / ".nexus" / "models" / "llm"


class DownloadRequest(BaseModel):
    repo_id: str
    filename: str


class FilenameRequest(BaseModel):
    filename: str


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------

@router.get("/local/hardware")
async def get_hardware() -> dict[str, Any]:
    from ...local_llm.hardware import probe
    return probe()


# ---------------------------------------------------------------------------
# HuggingFace search
# ---------------------------------------------------------------------------

@router.get("/local/hf/search")
async def hf_search(q: str, limit: int = 20) -> list[dict[str, Any]]:
    from ...local_llm.hf_search import search_gguf_repos, HfSearchError
    try:
        return search_gguf_repos(q, limit=limit)
    except HfSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/local/hf/repo/{owner}/{repo}/files")
async def hf_repo_files(owner: str, repo: str) -> list[dict[str, Any]]:
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
        f["fits_in_ram"] = (f["size_bytes"] * 1.3) < ram_bytes

    return files


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

@router.post("/local/download")
async def start_download(body: DownloadRequest) -> dict[str, str]:
    from ...local_llm.downloader import start_download as _start

    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    task = _start(body.repo_id, body.filename, _MODELS_DIR)
    return {"task_id": task.task_id}


@router.get("/local/downloads")
async def list_downloads() -> list[dict[str, Any]]:
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
    """List GGUF files installed in ~/.nexus/models/llm/.

    Each entry includes ``is_running`` and (when running) the ``port`` of
    its llama-server. ``is_active`` is kept as an alias of ``is_running``
    for older UI builds.
    """
    from ...local_llm import manager

    if not _MODELS_DIR.is_dir():
        return []

    running = {info["filename"]: info for info in manager.list_running()}

    results = []
    for p in sorted(_MODELS_DIR.glob("*.gguf")):
        info = running.get(p.name)
        is_mmproj = manager.is_mmproj_file(p)
        results.append({
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            # Vision-language projector sidecars are listed so the UI's
            # auto-start gate can wait for the matching language GGUF —
            # but the UI hides them from the user-facing tile list.
            "is_mmproj": is_mmproj,
            "is_running": info is not None and not is_mmproj,
            "is_active": info is not None and not is_mmproj,  # legacy alias
            "port": info["port"] if info else None,
            "slug": info["slug"] if info else manager.slugify(p.stem),
        })
    return results


@router.post("/local/start")
async def start_model(
    body: FilenameRequest,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent: Any = Depends(get_agent),
) -> dict[str, Any]:
    """Spawn a llama-server for the given GGUF file (idempotent).

    Adds the corresponding ``providers.local-<slug>`` and ``[[models]]`` entries
    to ``~/.nexus/config.toml`` and rebuilds the provider registry so the model
    becomes selectable in the chat picker and routable for GraphRAG extraction.
    """
    from ...local_llm import manager
    from .config import _rebuild_registry

    model_path = _MODELS_DIR / body.filename
    if not model_path.is_file():
        raise HTTPException(status_code=404, detail=f"Model file not found: {body.filename}")

    # Look up any user-configured context_window for this model in the
    # current config so the spawned llama-server uses it. 0 = default.
    cfg = app_state.get("cfg")
    expected_slug = manager.slugify(model_path.stem)
    expected_id = f"local-{expected_slug}/{expected_slug}"
    ctx_size = 0
    if cfg is not None:
        for m in cfg.models:
            if m.id == expected_id and getattr(m, "context_window", 0) > 0:
                ctx_size = m.context_window
                break

    try:
        if ctx_size > 0:
            handle = manager.start(model_path, ctx_size=ctx_size)
        else:
            handle = manager.start(model_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    model_id = manager.add_to_config(
        handle.slug, handle.port,
        is_embedding=handle.is_embedding,
        is_vision=handle.is_vision,
        context_window=ctx_size,
    )

    # Auto-mark as the Vision role when a vision-capable local model
    # comes up and the user hasn't picked one yet — chat-side OCR via
    # ocr_image then "just works" without a manual click.
    try:
        from ...config_file import load as load_cfg, save as save_cfg

        cfg = load_cfg()
        if (
            handle.is_vision
            and not handle.is_embedding
            and not (cfg.agent.vision_model or "").strip()
        ):
            cfg.agent.vision_model = model_id
            save_cfg(cfg)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "auto-mark Vision after start failed", exc_info=True,
        )

    try:
        from ...config_file import load as load_cfg
        new_cfg = load_cfg()
        _rebuild_registry(new_cfg, app_state, agent)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("registry rebuild after start failed: %s", exc)

    return {"slug": handle.slug, "port": handle.port, "model_id": model_id}


@router.post("/local/stop")
async def stop_model(
    body: FilenameRequest,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent: Any = Depends(get_agent),
) -> dict[str, Any]:
    """Terminate the llama-server for the given GGUF file and unregister it."""
    from ...local_llm import manager
    from .config import _rebuild_registry

    if not manager.is_running(body.filename):
        raise HTTPException(status_code=404, detail="No server running for that file.")

    slug = manager.slugify(Path(body.filename).stem)
    manager.stop(body.filename)
    manager.remove_from_config(slug)

    try:
        from ...config_file import load as load_cfg
        new_cfg = load_cfg()
        _rebuild_registry(new_cfg, app_state, agent)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("registry rebuild after stop failed: %s", exc)

    return {"stopped": body.filename}


# Legacy alias kept so older UI builds keep working.
@router.post("/local/activate")
async def activate_model_legacy(
    body: FilenameRequest,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent: Any = Depends(get_agent),
) -> dict[str, Any]:
    return await start_model(body, app_state, agent)


@router.delete("/local/installed/{filename}")
async def delete_installed(filename: str) -> dict[str, str]:
    """Delete an installed GGUF file. Returns 409 if it's currently running."""
    from ...local_llm import manager

    if manager.is_running(filename):
        raise HTTPException(status_code=409, detail="Stop the model before deleting it.")

    model_path = _MODELS_DIR / filename
    if not model_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    try:
        model_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"deleted": filename}
