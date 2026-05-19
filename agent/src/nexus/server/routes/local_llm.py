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


@router.post("/local/download/{task_id}/cancel")
async def cancel_download(task_id: str) -> dict[str, Any]:
    from ...local_llm.downloader import cancel_download as _cancel
    ok = _cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Download not found or already finished")
    return {"cancelled": task_id}


# ---------------------------------------------------------------------------
# Binary updates
# ---------------------------------------------------------------------------

@router.get("/local/binary/status")
async def binary_status() -> dict[str, Any]:
    from ...local_llm.binary_update import current_version, check_latest, is_downloading
    cur = current_version()
    latest_info = check_latest()
    latest = latest_info["version"] if latest_info else cur
    return {
        "current_version": cur,
        "latest_version": latest,
        "update_available": latest > cur,
        "downloading": is_downloading(),
    }


@router.post("/local/binary/update")
async def binary_update() -> dict[str, Any]:
    import asyncio
    import logging
    from ...local_llm.binary_update import needs_update, download_and_install, is_downloading

    if is_downloading():
        raise HTTPException(status_code=409, detail="Update already in progress")

    info = needs_update()
    if info is None:
        return {"status": "up_to_date"}

    async def _do_update() -> None:
        try:
            download_and_install(info["url"], info["tag"], info["dist"])
            logging.getLogger(__name__).info("[binary_update] updated to %s", info["tag"])
        except Exception as exc:
            logging.getLogger(__name__).error("[binary_update] failed: %s", exc)

    asyncio.get_running_loop().run_in_executor(None, _do_update)
    return {"status": "updating", "tag": info["tag"], "version": info["latest"]}


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
        has_mamba = False
        if not is_mmproj:
            try:
                has_mamba = manager._gguf_has_mamba_layers(p)
            except Exception:
                pass
        results.append({
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "is_mmproj": is_mmproj,
            "is_running": info is not None and not is_mmproj,
            "is_active": info is not None and not is_mmproj,
            "port": info["port"] if info else None,
            "slug": info["slug"] if info else manager.slugify(p.stem),
            "has_mamba_layers": has_mamba,
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

    try:
        from ...ocr_server import pause as _ocr_pause
        _ocr_pause()
    except Exception:
        pass

    model_id = manager.add_to_config(
        handle.slug, handle.port,
        is_embedding=handle.is_embedding,
        is_vision=handle.is_vision,
        context_window=handle.context_window or ctx_size,
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
        from ...ocr_server import resume as _ocr_resume
        _ocr_resume()
    except Exception:
        pass

    try:
        from ...config_file import load as load_cfg
        new_cfg = load_cfg()
        _rebuild_registry(new_cfg, app_state, agent)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("registry rebuild after stop failed: %s", exc)

    return {"stopped": body.filename}


@router.post("/local/stop-all")
async def stop_all_models(
    app_state: dict[str, Any] = Depends(get_app_state),
    agent: Any = Depends(get_agent),
) -> dict[str, Any]:
    """Stop every running local llama-server and unregister all."""
    from ...local_llm import manager
    from .config import _rebuild_registry

    running = manager.list_running()
    if not running:
        return {"stopped": []}

    slugs = []
    for info in running:
        slug = manager.slugify(Path(info["filename"]).stem)
        slugs.append(slug)
        manager.stop(info["filename"])

    for slug in slugs:
        manager.remove_from_config(slug)

    try:
        from ...ocr_server import resume as _ocr_resume
        _ocr_resume()
    except Exception:
        pass

    try:
        from ...config_file import load as load_cfg
        new_cfg = load_cfg()
        _rebuild_registry(new_cfg, app_state, agent)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("registry rebuild after stop-all failed: %s", exc)

    return {"stopped": [info["filename"] for info in running]}


# Legacy alias kept so older UI builds keep working.
@router.post("/local/activate")
async def activate_model_legacy(
    body: FilenameRequest,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent: Any = Depends(get_agent),
) -> dict[str, Any]:
    return await start_model(body, app_state, agent)


@router.delete("/local/installed/{filename}")
async def delete_installed(
    filename: str,
    app_state: dict[str, Any] = Depends(get_app_state),
    agent: Any = Depends(get_agent),
) -> dict[str, str]:
    """Delete an installed GGUF file. Stops the server first if running."""
    from ...local_llm import manager
    from .config import _rebuild_registry

    model_path = _MODELS_DIR / filename
    if not model_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    if manager.is_running(filename):
        slug = manager.slugify(Path(filename).stem)
        manager.stop(filename)
        manager.remove_from_config(slug)

        try:
            from ...ocr_server import resume as _ocr_resume
            _ocr_resume()
        except Exception:
            pass

        try:
            from ...config_file import load as load_cfg
            new_cfg = load_cfg()
            _rebuild_registry(new_cfg, app_state, agent)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("registry rebuild after stop-for-delete failed: %s", exc)

    try:
        model_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"deleted": filename}
