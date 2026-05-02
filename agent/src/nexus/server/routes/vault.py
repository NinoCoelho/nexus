"""Routes for vault file/folder CRUD, search, graph, and move operations."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/vault/tree")
async def vault_tree() -> list[dict]:
    from ...vault import list_tree
    entries = list_tree()
    return [{"path": e.path, "type": e.type, "size": e.size, "mtime": e.mtime} for e in entries]


@router.get("/vault/tags")
async def vault_list_tags() -> list[dict]:
    from ... import vault_index
    if vault_index.is_empty():
        vault_index.rebuild_from_disk()
    return vault_index.list_tags()


@router.get("/vault/tags/{tag}")
async def vault_files_for_tag(tag: str) -> dict:
    from ... import vault_index
    if vault_index.is_empty():
        vault_index.rebuild_from_disk()
    return {"tag": tag, "files": vault_index.files_with_tag(tag)}


@router.get("/vault/backlinks")
async def vault_backlinks_endpoint(path: str) -> dict:
    from ... import vault_index
    if vault_index.is_empty():
        vault_index.rebuild_from_disk()
    return {"path": path, "backlinks": vault_index.backlinks(path)}


@router.get("/vault/forward-links")
async def vault_forward_links_endpoint(path: str) -> dict:
    from ... import vault_index
    if vault_index.is_empty():
        vault_index.rebuild_from_disk()
    return {"path": path, "forward_links": vault_index.forward_links(path)}


@router.get("/vault/raw")
async def vault_read_raw(path: str):
    """Stream raw file bytes from the vault with a guessed Content-Type.

    Used by the UI to render images, PDFs, video, audio, and to provide
    a direct "open in new tab" link for any vault file.
    """
    import mimetypes
    from ...vault import resolve_path
    try:
        full = resolve_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not full.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such file: {path!r}")
    mime, _ = mimetypes.guess_type(full.name)
    return FileResponse(
        full,
        media_type=mime or "application/octet-stream",
        filename=full.name,
    )


# Per-(path, mtime) memo for /vault/transcribe so the bubble's "show
# transcript" badge doesn't re-run whisper on every click. Bounded to a
# few hundred entries — voice memos are short-lived in practice.
_transcript_cache: dict[tuple[str, int], str] = {}
_TRANSCRIPT_CACHE_MAX = 256


@router.get("/vault/transcribe")
async def vault_transcribe(path: str) -> dict:
    """Transcribe a vault audio file lazily and cache by (path, mtime).

    Used by the chat UI to reveal the text behind a voice-memo attachment
    without persisting the transcript on the message itself. Returns
    ``{"text": str}``; an empty string means transcription was attempted
    but produced no output.
    """
    from ...multimodal import transcribe_bytes
    from ...vault import resolve_path

    try:
        full = resolve_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not full.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such file: {path!r}")

    mtime_ns = full.stat().st_mtime_ns
    cache_key = (path, mtime_ns)
    cached = _transcript_cache.get(cache_key)
    if cached is not None:
        return {"text": cached}

    import mimetypes
    mime, _ = mimetypes.guess_type(full.name)
    if not (mime and mime.startswith("audio/")) and not full.name.lower().endswith(".webm"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"not an audio file: {path!r}",
        )
    # The file may be a webm voice memo whose extension sniffs to video/webm;
    # transcribe_bytes only cares about giving whisper a sensible suffix.
    effective_mime = mime if (mime and mime.startswith("audio/")) else "audio/webm"

    data = full.read_bytes()
    text = await transcribe_bytes(data, effective_mime)

    if len(_transcript_cache) >= _TRANSCRIPT_CACHE_MAX:
        # Drop the oldest entry — Python 3.7+ dicts preserve insertion order.
        _transcript_cache.pop(next(iter(_transcript_cache)))
    _transcript_cache[cache_key] = text
    return {"text": text}


@router.get("/vault/file")
async def vault_read_file(path: str) -> dict:
    from ...vault import read_file
    from ... import vault_index
    try:
        result = read_file(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    try:
        if vault_index.is_empty():
            vault_index.rebuild_from_disk()
        result["tags"] = vault_index.tags_for_file(path)
        result["backlinks"] = vault_index.backlinks(path)
    except Exception:
        log.warning("vault_index: failed to attach tags/backlinks", exc_info=True)
    return result


@router.put("/vault/file", status_code=status.HTTP_204_NO_CONTENT)
async def vault_write_file(body: dict) -> None:
    from ...vault import write_file
    path = body.get("path", "")
    content = body.get("content", "")
    if not path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
    try:
        write_file(path, content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete("/vault/file", status_code=status.HTTP_204_NO_CONTENT)
async def vault_delete_file(path: str, recursive: bool = False) -> None:
    from ...vault import delete
    try:
        delete(path, recursive=recursive)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/vault/folder", status_code=status.HTTP_201_CREATED)
async def vault_create_folder(body: dict) -> dict:
    from ...vault import create_folder
    path = body.get("path", "")
    if not path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
    try:
        create_folder(path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"path": path}


@router.post("/vault/upload")
async def vault_upload(request: Request) -> dict:
    from ...vault import write_file, write_file_bytes

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="expected multipart/form-data",
        )
    form = await request.form()
    files = form.getlist("files")
    if not files:
        file_field = form.get("file")
        if file_field is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="`file` or `files` field required",
            )
        files = [file_field]
    dest_dir = (form.get("path") or "").strip().strip("/")
    uploaded: list[dict[str, Any]] = []
    for upload in files:
        if not hasattr(upload, "filename") or upload.filename is None:
            continue
        import re as _re

        safe_name = _re.sub(r"[^\w.\-]+", "_", upload.filename)
        rel = f"{dest_dir}/{safe_name}" if dest_dir else safe_name
        raw = await upload.read()
        text_exts = {
            ".md", ".mdx", ".txt", ".markdown", ".csv", ".json",
            ".yaml", ".yml", ".toml", ".xml", ".html", ".css",
            ".js", ".ts", ".py", ".rs", ".go", ".sh", ".bash", ".zsh",
        }
        _, ext = os.path.splitext(safe_name.lower())
        try:
            if ext in text_exts:
                write_file(rel, raw.decode("utf-8", errors="replace"))
            else:
                write_file_bytes(rel, raw)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        uploaded.append({"path": rel, "size": len(raw)})
    return {"uploaded": uploaded}


@router.get("/vault/mention")
async def vault_mention_endpoint(q: str = "", limit: int = 8) -> dict:
    """Path/name autocomplete for the chat `@` mention picker.

    Ranks vault files and folders by how well their basename or path
    matches the query. Substring matches in basename rank highest, then
    substring in path, then in-order character matches (loose fuzzy).
    Returns at most `limit` entries with the same shape as `/vault/tree`.
    """
    from ...vault import list_tree
    query = q.strip().lower()
    entries = list_tree()
    if not query:
        ranked = entries[:limit]
    else:
        scored: list[tuple[float, str, object]] = []
        for e in entries:
            p = e.path.lower()
            base = p.rsplit("/", 1)[-1]
            idx = base.find(query)
            if idx >= 0:
                s = 1000 - idx
            else:
                idx = p.find(query)
                if idx >= 0:
                    s = 500 - idx
                else:
                    i = 0
                    for ch in p:
                        if ch == query[i]:
                            i += 1
                            if i == len(query):
                                break
                    if i < len(query):
                        continue
                    s = 100
            scored.append((s, e.path, e))
        scored.sort(key=lambda r: (-r[0], r[1]))
        ranked = [r[2] for r in scored[:limit]]
    return {
        "results": [
            {"path": e.path, "type": e.type, "size": e.size, "mtime": e.mtime}
            for e in ranked
        ],
        "q": q,
    }


@router.get("/vault/search")
async def vault_search_endpoint(q: str = "", limit: int = 50) -> dict:
    from ... import vault_search
    q = q.strip()
    if not q:
        return {"results": [], "q": q, "count": 0}
    if vault_search.is_empty():
        vault_search.rebuild_from_disk()
    results = vault_search.search(q, limit=limit)
    return {"results": results, "q": q, "count": len(results)}


@router.get("/vault/events")
async def vault_events() -> Any:
    """SSE stream of vault/index events.

    Emits ``vault.indexed``, ``vault.removed``, ``graphrag.indexed``, and
    ``graphrag.removed`` events as they occur. Best-effort: dropped frames
    on slow consumers, no replay on reconnect.
    """
    import asyncio
    import json
    from fastapi.responses import StreamingResponse
    from ..event_bus import subscribe, unsubscribe

    queue = subscribe()

    async def _gen():
        try:
            yield ": ok\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            unsubscribe(queue)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/vault/reindex")
async def vault_reindex(full: bool = False) -> dict:
    """Re-index FTS and tag/link metadata.

    With ``?full=1`` rebuilds from scratch. Default is incremental:
    skip files whose ``(mtime, size)`` matches the index, and prune rows
    for files that have disappeared on disk.
    """
    from ... import vault_search, vault_index
    n = vault_search.rebuild_from_disk(full=full)
    try:
        vault_index.rebuild_from_disk(full=full)
    except Exception:
        log.warning("vault_index rebuild failed", exc_info=True)
    return {"indexed": n, "full": full}


@router.get("/vault/graph")
async def vault_graph(
    scope: str = "all",
    seed: str = "",
    hops: int = 1,
    edge_types: str = "link",
) -> dict:
    from ...vault_graph import build_graph, build_scoped_graph
    if scope == "all" and not seed:
        data = build_graph()
        return {
            "nodes": data["nodes"],
            "edges": [{"from": e["from_"], "to": e["to"]} for e in data["edges"]],
            "orphans": data["orphans"],
        }
    hops = max(1, min(int(hops), 3))
    data = build_scoped_graph(scope=scope, seed=seed, hops=hops, edge_types=edge_types)
    return {
        "nodes": data["nodes"],
        "edges": [{"from": e["from_"], "to": e["to_"], "type": e["type"]} for e in data["edges"]],
        "entity_nodes": data["entity_nodes"],
        "orphans": data["orphans"],
    }


@router.get("/vault/graph/entity-sources")
async def vault_graph_entity_sources(path: str) -> dict:
    from ...agent.graphrag_manager import entities_for_source
    return {"path": path, "entities": entities_for_source(path)}


@router.get("/vault/graph/source-files")
async def vault_graph_source_files(entity_id: int) -> dict:
    from ...agent.graphrag_manager import sources_for_entity
    return {"entity_id": entity_id, "source_files": sources_for_entity(entity_id)}


@router.get("/vault/csv")
async def vault_csv_read(
    path: str,
    offset: int = 0,
    limit: int = 100,
    sort: str | None = None,
    sort_dir: str = "asc",
) -> dict:
    from ... import vault_csv
    try:
        return vault_csv.csv_read_page(
            path, offset=offset, limit=limit, sort=sort, sort_dir=sort_dir
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/vault/csv/row")
async def vault_csv_add_row(body: dict) -> dict:
    from ... import vault_csv
    path = body.get("path", "")
    values = body.get("values") or {}
    if not path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`path` required")
    try:
        return vault_csv.csv_append_row(path, values)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        msg = str(exc)
        code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if "too large" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg)


@router.patch("/vault/csv/cell")
async def vault_csv_update_cell(body: dict) -> dict:
    from ... import vault_csv
    path = body.get("path", "")
    row_index = body.get("row_index")
    column = body.get("column", "")
    value = body.get("value", "")
    if not path or row_index is None or not column:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`path`, `row_index`, `column` required",
        )
    try:
        return vault_csv.csv_update_cell(path, int(row_index), column, value)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        msg = str(exc)
        code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if "too large" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg)


@router.delete("/vault/csv/row")
async def vault_csv_delete_row(path: str, row_index: int) -> dict:
    from ... import vault_csv
    try:
        return vault_csv.csv_delete_row(path, row_index)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        msg = str(exc)
        code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if "too large" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg)


@router.post("/vault/csv/schema")
async def vault_csv_schema_endpoint(body: dict) -> dict:
    from ... import vault_csv
    path = body.get("path", "")
    columns = body.get("columns") or []
    if not path or not columns:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`path` and `columns` required",
        )
    try:
        return vault_csv.csv_set_schema(path, columns)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        msg = str(exc)
        code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if "too large" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg)


@router.post("/vault/move", status_code=status.HTTP_204_NO_CONTENT)
async def vault_move(body: dict) -> None:
    from ...vault import move
    from_path = body.get("from", "")
    to_path = body.get("to", "")
    if not from_path or not to_path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`from` and `to` required")
    try:
        move(from_path, to_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
