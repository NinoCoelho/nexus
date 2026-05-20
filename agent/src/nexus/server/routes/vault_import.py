"""Routes for vault zip/folder import, file processing, and CSV → app migration.

Endpoints:
  POST /vault/upload/zip-preview    — upload zip, extract to temp, return tree + stats
  POST /vault/import/zip            — confirm zip import, SSE progress stream
  POST /vault/import/batch          — import dropped files, SSE progress stream
  DELETE /vault/import/zip/{id}     — cancel and clean up temp
  POST /vault/csv-analyze           — analyze CSV with LLM, return data model proposal
  POST /vault/csv-migrate           — execute approved CSV migration, SSE progress
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

_TEMP_BASE = Path("~/.nexus/tmp/zip-import").expanduser()
_CSV_TEMP_BASE = Path("~/.nexus/tmp/csv-app").expanduser()
_MAX_ZIP_SIZE = 500 * 1024 * 1024
_MAX_EXTRACTED_SIZE = 2 * 1024 * 1024 * 1024
_PREVIEW_TTL = 3600
_PROCESS_INPUT_CAP = 100_000
_TEXT_EXTS = {
    ".md", ".mdx", ".txt", ".markdown", ".csv", ".json",
    ".yaml", ".yml", ".toml", ".xml", ".html", ".css",
    ".js", ".ts", ".py", ".rs", ".go", ".sh", ".bash", ".zsh",
    ".tsx", ".jsx", ".mjs", ".cjs", ".rb", ".java", ".kt",
    ".swift", ".c", ".h", ".cpp", ".hpp", ".cs", ".sql",
    ".r", ".R", ".m", ".pl", ".lua", ".vim", ".el",
    ".scss", ".less", ".sass", ".styl",
    ".ini", ".cfg", ".conf", ".log", ".diff", ".patch",
    ".rtf", ".tex", ".bib",
}


class _TempImport(BaseModel):
    temp_dir: str
    created_at: float
    tree: list[dict[str, Any]]
    stats: dict[str, Any]
    export_format: dict[str, Any] | None = None


_active_imports: dict[str, _TempImport] = {}


def _cleanup_expired() -> None:
    now = time.time()
    expired = [k for k, v in _active_imports.items() if now - v.created_at > _PREVIEW_TTL]
    for k in expired:
        info = _active_imports.pop(k, None)
        if info:
            try:
                shutil.rmtree(info.temp_dir, ignore_errors=True)
            except Exception:
                pass


def _sanitize_zip_path(rel: str) -> str:
    parts = rel.replace("\\", "/").split("/")
    cleaned = [p for p in parts if p and p != "." and p != ".."]
    result = "/".join(cleaned)
    return result


def _build_tree_from_dir(temp_dir: Path, prefix: str = "") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(temp_dir.iterdir())
    except PermissionError:
        return entries
    for child in children:
        name = child.name
        if name.startswith(".") or name == "__MACOSX":
            continue
        rel = f"{prefix}/{name}" if prefix else name
        if child.is_dir() and not child.is_symlink():
            sub = _build_tree_from_dir(child, rel)
            entries.append({"name": name, "path": rel, "type": "dir", "children": sub})
        elif child.is_file():
            stat = child.stat()
            entries.append({"name": name, "path": rel, "type": "file", "size": stat.st_size})
    return entries


def _count_tree(nodes: list[dict[str, Any]]) -> tuple[int, int]:
    files = 0
    total_size = 0
    for n in nodes:
        if n["type"] == "file":
            files += 1
            total_size += n.get("size", 0)
        elif n["type"] == "dir" and "children" in n:
            f, s = _count_tree(n["children"])
            files += f
            total_size += s
    return files, total_size


def _find_csvs(nodes: list[dict[str, Any]], temp_dir: Path) -> list[dict[str, Any]]:
    csvs: list[dict[str, Any]] = []
    for n in nodes:
        if n["type"] == "file" and n["path"].lower().endswith(".csv"):
            full = temp_dir / n["path"]
            headers: list[str] = []
            row_est = 0
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    reader = csv.reader(f)
                    headers = next(reader, [])
                    for _ in range(5):
                        next(reader, None)
                    row_est = sum(1 for _ in f) + 6
            except Exception:
                pass
            csvs.append({
                "path": n["path"],
                "name": n["name"],
                "headers": headers,
                "column_count": len(headers),
                "estimated_rows": row_est,
                "size": n.get("size", 0),
            })
        elif n["type"] == "dir" and "children" in n:
            csvs.extend(_find_csvs(n["children"], temp_dir))
    return csvs


def _detect_export_format(temp_dir: Path) -> dict[str, Any] | None:
    from ...vault_import_parsers import detect_format
    fmt = detect_format(temp_dir)
    if not fmt:
        return None
    count = 0
    try:
        if fmt == "chatgpt" and (temp_dir / "conversations.json").is_file():
            with open(temp_dir / "conversations.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            count = len(data) if isinstance(data, list) else 1
        elif fmt == "gemini":
            gj = temp_dir / "Gemini.json"
            if not gj.is_file():
                gj = temp_dir / "Takeout" / "Gemini" / "Gemini.json"
            if gj.is_file():
                with open(gj, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    count = 1
        elif fmt == "claude":
            for p in temp_dir.rglob("*.json"):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "chat_messages" in data:
                        count += 1
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and "chat_messages" in item:
                                count += 1
                except Exception:
                    continue
    except Exception:
        pass
    return {"format": fmt, "conversation_count": count}


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _is_text_file(path: str) -> bool:
    _, ext = os.path.splitext(path.lower())
    return ext in _TEXT_EXTS


async def _process_file_llm(
    content: str,
    filename: str,
    prompt: str,
    agent: Any,
    cfg: Any,
) -> str:
    from ...agent.llm import ChatMessage, Role

    target = cfg.agent.default_model or ""
    provider, upstream = agent._resolve_provider(target)
    if provider is None:
        raise ValueError("no LLM provider configured")

    if len(content) > _PROCESS_INPUT_CAP:
        content = content[:_PROCESS_INPUT_CAP] + "\n\n[...truncated]"

    no_think = {
        "thinking": {"type": "disabled"},
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    resp = await provider.chat(
        [
            ChatMessage(
                role=Role.SYSTEM,
                content="You are a file processor. Apply the instruction to the file content. Output only the result, no commentary.",
            ),
            ChatMessage(
                role=Role.USER,
                content=f'<file name="{filename}">\n{content}\n</file>\n\nInstruction: {prompt}',
            ),
        ],
        model=upstream,
        max_tokens=16384,
        extra_payload=no_think,
    )
    return (resp.content or "").strip()


async def _analyze_csv_llm(
    headers: list[str],
    sample_rows: list[dict[str, Any]],
    total_rows: int,
    agent: Any,
    cfg: Any,
) -> dict[str, Any]:
    from ...agent.llm import ChatMessage, Role

    target = cfg.agent.default_model or ""
    provider, upstream = agent._resolve_provider(target)
    if provider is None:
        raise ValueError("no LLM provider configured")

    sample_str = json.dumps(sample_rows[:20], indent=2, ensure_ascii=False)
    prompt = (
        "You are a data architect. Analyze the CSV data below and propose a normalized data model "
        "as related data-table entities suitable for a personal knowledge base.\n\n"
        f"Headers: {json.dumps(headers)}\n"
        f"Total rows: {total_rows}\n"
        f"Sample rows:\n{sample_str}\n\n"
        "Respond with ONLY a JSON object (no markdown fences) with this structure:\n"
        "{\n"
        '  "entities": [\n'
        "    {\n"
        '      "name": "EntityName",\n'
        '      "fields": [\n'
        '        {"name": "field_name", "kind": "text|number|select|date|boolean", '
        '"choices": ["a","b"], "required": true}\n'
        "      ],\n"
        '      "sample_values": {"field_name": ["val1", "val2"]}\n'
        "    }\n"
        "  ],\n"
        '  "relationships": [\n'
        "    {\n"
        '      "from": "EntityA",\n'
        '      "to": "EntityB",\n'
        '      "type": "one_to_many|many_to_many|one_to_one",\n'
        '      "via_field": "field_name_in_from_entity",\n'
        '      "description": "brief explanation"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Guidelines:\n"
        "- Normalize: if a column contains repeated delimited values or clearly belongs to a separate entity, split it out.\n"
        "- Use 'ref' kind for foreign-key fields pointing to another entity.\n"
        "- For many-to-many relationships, create a junction entity.\n"
        "- Keep entity names singular and PascalCase.\n"
        "- Preserve all original data — don't drop columns.\n"
    )

    resp = await provider.chat(
        [ChatMessage(role=Role.USER, content=prompt)],
        model=upstream,
        max_tokens=4096,
    )
    raw = (resp.content or "").strip()
    fence_match = re.match(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    if fence_match:
        raw = fence_match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"entities": [{"name": "DataTable", "fields": [{"name": h, "kind": "text"} for h in headers]}], "relationships": []}


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("/vault/upload/zip-preview")
async def zip_preview(request: Request) -> dict[str, Any]:
    _cleanup_expired()

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="expected multipart/form-data")

    form = await request.form()
    file_field = form.get("file") or (form.getlist("files") or [None])[0]
    if not file_field or not hasattr(file_field, "filename"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="file field required")

    raw = await file_field.read()
    if len(raw) > _MAX_ZIP_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="zip exceeds 500 MB limit")

    if not raw[:4].startswith(b"PK"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not a valid zip file")

    import_id = uuid.uuid4().hex[:16]
    temp_dir = _TEMP_BASE / import_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            total_extracted = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                safe = _sanitize_zip_path(info.filename)
                if not safe:
                    continue
                target = temp_dir / safe
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        total_extracted += len(chunk)
                        if total_extracted > _MAX_EXTRACTED_SIZE:
                            shutil.rmtree(temp_dir, ignore_errors=True)
                            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="zip bomb detected: exceeds 2 GB extracted")
                        dst.write(chunk)
    except HTTPException:
        raise
    except (zipfile.BadZipFile, Exception) as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"zip extraction failed: {exc}")

    tree = _build_tree_from_dir(temp_dir)
    total_files, total_size = _count_tree(tree)
    csvs = _find_csvs(tree, temp_dir)
    export_format = _detect_export_format(temp_dir)

    stats = {
        "total_files": total_files,
        "total_size": total_size,
        "csvs": csvs,
    }

    info = _TempImport(
        temp_dir=str(temp_dir),
        created_at=time.time(),
        tree=tree,
        stats=stats,
        export_format=export_format,
    )
    _active_imports[import_id] = info

    return {
        "import_id": import_id,
        "tree": tree,
        "stats": stats,
        "export_format": export_format,
    }


@router.delete("/vault/import/zip/{import_id}")
async def zip_cancel(import_id: str) -> dict[str, str]:
    info = _active_imports.pop(import_id, None)
    if info:
        try:
            shutil.rmtree(info.temp_dir, ignore_errors=True)
        except Exception:
            pass
    return {"status": "cancelled"}


@router.post("/vault/import/zip")
async def zip_import(request: Request) -> StreamingResponse:
    body = await request.json()
    import_id = body.get("import_id", "")
    info = _active_imports.get(import_id)
    if not info:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="import session not found or expired")

    selected_paths = set(body.get("selected_paths", []))
    dest_dir = (body.get("dest_dir") or "").strip().strip("/")
    csv_options: dict[str, str] = body.get("csv_options", {})
    process_options: dict[str, Any] | None = body.get("process_options")
    export_options: dict[str, Any] | None = body.get("export_options")

    temp_dir = Path(info.temp_dir)
    agent = request.app.state.agent
    cfg = request.app.state.mutable_state.get("cfg")

    async def _stream() -> AsyncIterator[str]:
        from ...vault import write_file, write_file_bytes
        from ...vault_import_parsers import parse_chatgpt, parse_claude, parse_gemini, conversations_to_markdown

        imported = 0
        processed = 0
        errors = 0
        csv_apps: list[str] = []

        all_files = _collect_selected_files(temp_dir, selected_paths, info.tree)

        conv_done = False
        if export_options and export_options.get("import_as") == "conversations" and not conv_done:
            conv_done = True
            fmt = export_options.get("format", "")
            conv_path = _find_conversations_file(temp_dir, info.tree, all_files)
            if conv_path:
                yield _sse_event("file_start", {"path": conv_path[0], "action": "convert"})
                try:
                    convos: list[Any] = []
                    if fmt == "chatgpt":
                        convos = parse_chatgpt(conv_path[1])
                    elif fmt == "claude":
                        convos = parse_claude(conv_path[1])
                    elif fmt == "gemini":
                        convos = parse_gemini(conv_path[1])
                    md_files = conversations_to_markdown(convos, dest_dir)
                    for mf in md_files:
                        content_bytes = mf["content"].encode("utf-8", errors="replace")
                        if len(content_bytes) <= 1024 * 1024:
                            write_file(mf["path"], mf["content"])
                        else:
                            write_file_bytes(mf["path"], content_bytes)
                        yield _sse_event("file_done", {"path": mf["path"], "action": "converted", "size": len(content_bytes)})
                        imported += 1
                    if not md_files:
                        yield _sse_event("file_error", {"path": conv_path[0], "error": "No conversations found in file"})
                        errors += 1
                except Exception as exc:
                    errors += 1
                    yield _sse_event("file_error", {"path": conv_path[0], "error": str(exc)})

        for rel_path, full_path in all_files:
            if await request.is_disconnected():
                break

            if csv_options.get(rel_path) == "app":
                csv_apps.append(rel_path)
                yield _sse_event("file_done", {"path": rel_path, "action": "csv_app_queued"})
                continue

            if export_options and export_options.get("import_as") == "conversations":
                continue

            vault_path = f"{dest_dir}/{rel_path}" if dest_dir else rel_path
            is_text = _is_text_file(rel_path)

            if process_options and process_options.get("prompt") and is_text:
                yield _sse_event("file_start", {"path": rel_path, "action": "process"})
                try:
                    content = full_path.read_text(encoding="utf-8", errors="replace")
                    result = await _process_file_llm(content, rel_path, process_options["prompt"], agent, cfg)
                    result_bytes = result.encode("utf-8", errors="replace")
                    if len(result_bytes) <= 1024 * 1024:
                        write_file(vault_path, result)
                    else:
                        write_file_bytes(vault_path, result_bytes)
                    if process_options.get("keep_originals", True):
                        write_file_bytes(f"{dest_dir}/_originals/{rel_path}" if dest_dir else f"_originals/{rel_path}", content.encode("utf-8"))
                    yield _sse_event("file_done", {"path": vault_path, "action": "processed", "size": len(result)})
                    processed += 1
                except Exception as exc:
                    errors += 1
                    yield _sse_event("file_error", {"path": rel_path, "error": str(exc)})
                continue

            yield _sse_event("file_start", {"path": rel_path, "action": "import"})
            try:
                data = full_path.read_bytes()
                if is_text:
                    text = data.decode("utf-8", errors="replace")
                    if len(text.encode("utf-8", errors="replace")) <= 1024 * 1024:
                        write_file(vault_path, text)
                    else:
                        write_file_bytes(vault_path, data)
                else:
                    write_file_bytes(vault_path, data)
                yield _sse_event("file_done", {"path": vault_path, "action": "imported", "size": len(data)})
                imported += 1
            except Exception as exc:
                errors += 1
                yield _sse_event("file_error", {"path": rel_path, "error": str(exc)})

        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            _active_imports.pop(import_id, None)
        except Exception:
            pass

        yield _sse_event("done", {"stats": {"imported": imported, "processed": processed, "errors": errors}, "csv_apps": csv_apps})

    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/vault/import/batch")
async def batch_import(request: Request) -> StreamingResponse:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="expected multipart/form-data")

    form = await request.form()
    options_raw = form.get("options")
    if not options_raw:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="options field required")
    options = json.loads(options_raw) if isinstance(options_raw, str) else options_raw

    dest_dir = (options.get("dest_dir") or "").strip().strip("/")
    csv_options: dict[str, str] = options.get("csv_options", {})
    process_options: dict[str, Any] | None = options.get("process_options")

    files_field = form.getlist("files")
    file_map: dict[str, Any] = {}
    for f in files_field:
        if hasattr(f, "filename") and f.filename:
            rel = form.get(f"paths.{f.filename}", f.filename)
            file_map[rel] = f

    agent = request.app.state.agent
    cfg = request.app.state.mutable_state.get("cfg")

    batch_id = uuid.uuid4().hex[:16]
    csv_temp_dir = _CSV_TEMP_BASE / batch_id
    csv_temp_dir.mkdir(parents=True, exist_ok=True)

    async def _stream() -> AsyncIterator[str]:
        from ...vault import write_file, write_file_bytes

        imported = 0
        processed = 0
        errors = 0
        csv_apps: list[str] = []

        for rel_path, upload in file_map.items():
            if await request.is_disconnected():
                break

            if csv_options.get(rel_path) == "app":
                csv_apps.append(rel_path)
                raw = await upload.read()
                csv_target = csv_temp_dir / rel_path.replace("/", "_")
                csv_target.parent.mkdir(parents=True, exist_ok=True)
                csv_target.write_bytes(raw)
                yield _sse_event("file_done", {"path": rel_path, "action": "csv_app_queued"})
                continue

            vault_path = f"{dest_dir}/{rel_path}" if dest_dir else rel_path
            raw = await upload.read()

            if process_options and process_options.get("prompt") and _is_text_file(rel_path):
                yield _sse_event("file_start", {"path": rel_path, "action": "process"})
                try:
                    text = raw.decode("utf-8", errors="replace")
                    result = await _process_file_llm(text, rel_path, process_options["prompt"], agent, cfg)
                    write_file(vault_path, result)
                    if process_options.get("keep_originals", True):
                        orig_path = f"{dest_dir}/_originals/{rel_path}" if dest_dir else f"_originals/{rel_path}"
                        write_file_bytes(orig_path, raw)
                    yield _sse_event("file_done", {"path": vault_path, "action": "processed", "size": len(result)})
                    processed += 1
                except Exception as exc:
                    errors += 1
                    yield _sse_event("file_error", {"path": rel_path, "error": str(exc)})
                continue

            yield _sse_event("file_start", {"path": rel_path, "action": "import"})
            try:
                if _is_text_file(rel_path) and len(raw) <= 1024 * 1024:
                    write_file(vault_path, raw.decode("utf-8", errors="replace"))
                else:
                    write_file_bytes(vault_path, raw)
                yield _sse_event("file_done", {"path": vault_path, "action": "imported", "size": len(raw)})
                imported += 1
            except Exception as exc:
                errors += 1
                yield _sse_event("file_error", {"path": rel_path, "error": str(exc)})

        yield _sse_event("done", {"stats": {"imported": imported, "processed": processed, "errors": errors}, "csv_apps": csv_apps, "batch_id": batch_id})

    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/vault/csv-analyze")
async def csv_analyze(request: Request) -> dict[str, Any]:
    body = await request.json()
    csv_path = body.get("csv_path", "")
    import_id = body.get("import_id")
    batch_id = body.get("batch_id")
    source = body.get("source", "temp")

    full: Path | None = None
    if source == "vault":
        from ...vault import resolve_path
        full = resolve_path(csv_path)
    elif import_id:
        info = _active_imports.get(import_id)
        if info:
            full = Path(info.temp_dir) / csv_path
    elif batch_id:
        csv_temp = _CSV_TEMP_BASE / batch_id
        candidate = csv_temp / csv_path.replace("/", "_")
        if candidate.is_file():
            full = candidate
        else:
            csvs = list(csv_temp.glob("*.csv"))
            if csvs:
                full = csvs[0]

    if not full or not full.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"CSV file not found: {csv_path}")

    import duckdb
    con = duckdb.connect(database=":memory:")
    try:
        con.execute(f"CREATE VIEW csv_data AS SELECT * FROM read_csv_auto('{full}')")
        cols_result = con.execute("DESCRIBE csv_data").fetchall()
        headers = [r[0] for r in cols_result]
        sample_rows = [dict(zip(headers, r)) for r in con.execute("SELECT * FROM csv_data LIMIT 20").fetchall()]
        total_rows = con.execute("SELECT COUNT(*) FROM csv_data").fetchone()[0]
    finally:
        con.close()

    agent = request.app.state.agent
    cfg = request.app.state.mutable_state.get("cfg")
    proposal = await _analyze_csv_llm(headers, sample_rows, total_rows, agent, cfg)

    return {
        "proposal": proposal,
        "csv_stats": {"rows": total_rows, "columns": len(headers), "headers": headers},
    }


@router.post("/vault/csv-migrate")
async def csv_migrate(request: Request) -> StreamingResponse:
    body = await request.json()
    csv_path = body.get("csv_path", "")
    import_id = body.get("import_id")
    batch_id = body.get("batch_id")
    source = body.get("source", "temp")
    dest_dir = (body.get("dest_dir") or "").strip().strip("/")
    approved_plan: dict[str, Any] = body.get("approved_plan", {})

    full: Path | None = None
    if source == "vault":
        from ...vault import resolve_path
        full = resolve_path(csv_path)
    elif import_id:
        info = _active_imports.get(import_id)
        if info:
            full = Path(info.temp_dir) / csv_path
    elif batch_id:
        csv_temp = _CSV_TEMP_BASE / batch_id
        candidate = csv_temp / csv_path.replace("/", "_")
        if candidate.is_file():
            full = candidate
        else:
            csvs = list(csv_temp.glob("*.csv"))
            if csvs:
                full = csvs[0]

    if not full or not full.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"CSV file not found: {csv_path}")

    entities = approved_plan.get("entities", [])
    relationships = approved_plan.get("relationships", [])

    entity_name_to_path: dict[str, str] = {}
    for ent in entities:
        name = ent.get("name", "Table")
        fname = f"{name}.md"
        entity_name_to_path[name] = f"{dest_dir}/{fname}" if dest_dir else fname

    async def _stream() -> AsyncIterator[str]:
        from ...vault import write_file
        from ...vault_datatable import _serialize

        import duckdb
        con = duckdb.connect(database=":memory:")
        try:
            con.execute(f"CREATE VIEW csv_data AS SELECT * FROM read_csv_auto('{full}');")
            all_rows = [dict(r) for r in con.execute("SELECT * FROM csv_data").fetchall()]
        finally:
            con.close()

        for ent in entities:
            if await request.is_disconnected():
                break
            name = ent.get("name", "Table")
            fields = ent.get("fields", [])
            table_path = entity_name_to_path.get(name, f"{name}.md")

            yield _sse_event("file_start", {"path": table_path, "action": "create_table", "entity": name})

            schema_fields: list[dict[str, Any]] = [{"name": "_id", "kind": "text", "required": True}]
            field_map: dict[str, str] = {}
            for f in fields:
                fname = f.get("name", "")
                kind = f.get("kind", "text")
                field_def: dict[str, Any] = {"name": fname, "kind": kind}
                if f.get("choices"):
                    field_def["choices"] = f["choices"]
                if f.get("required"):
                    field_def["required"] = True

                rel = next(
                    (r for r in relationships if r.get("via_field") == fname and r.get("from") == name),
                    None,
                )
                if rel and rel.get("to"):
                    target_ent = rel["to"]
                    target_path = entity_name_to_path.get(target_ent, f"{target_ent}.md")
                    field_def["kind"] = "ref"
                    field_def["target_table"] = target_path
                    field_def["cardinality"] = "many" if "many" in rel.get("type", "") else "one"

                schema_fields.append(field_def)
                field_map[fname] = fname

            schema = {"title": name, "fields": schema_fields}
            fm = {"data-table-plugin": "basic"}
            content = _serialize(fm, schema, [])
            write_file(table_path, content)
            yield _sse_event("file_done", {"path": table_path, "action": "table_created", "entity": name})

            yield _sse_event("file_start", {"path": table_path, "action": "migrate_data", "entity": name})

            from ...vault_datatable import add_rows_with_report

            rows_to_add: list[dict[str, Any]] = []
            for row in all_rows:
                dt_row: dict[str, Any] = {"_id": uuid.uuid4().hex[:8]}
                for f in fields:
                    fname = f.get("name", "")
                    if fname in row:
                        dt_row[fname] = row[fname]
                rows_to_add.append(dt_row)

            try:
                report = add_rows_with_report(table_path, rows_to_add)
                yield _sse_event("file_done", {
                    "path": table_path,
                    "action": "data_migrated",
                    "entity": name,
                    "added": report.get("added", 0),
                    "skipped": report.get("skipped", 0),
                })
            except Exception as exc:
                yield _sse_event("file_error", {"path": table_path, "entity": name, "error": str(exc)})

        try:
            if batch_id:
                shutil.rmtree(_CSV_TEMP_BASE / batch_id, ignore_errors=True)
        except Exception:
            pass

        yield _sse_event("done", {"stats": {"entities_created": len(entities), "rows_migrated": len(all_rows)}})

    return StreamingResponse(_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _collect_selected_files(
    temp_dir: Path,
    selected_paths: set[str],
    tree: list[dict[str, Any]],
) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    selected_dirs: set[str] = set()

    for p in selected_paths:
        full = temp_dir / p
        if full.is_dir():
            selected_dirs.add(p)

    def _walk(nodes: list[dict[str, Any]]) -> None:
        for n in nodes:
            p = n["path"]
            if n["type"] == "dir" and "children" in n:
                if p in selected_dirs or any(p.startswith(d + "/") for d in selected_dirs):
                    _walk(n["children"])
                elif p in selected_paths:
                    _walk(n["children"])
            elif n["type"] == "file":
                if p in selected_paths or any(p.startswith(d + "/") for d in selected_dirs):
                    full = temp_dir / p
                    if full.is_file():
                        result.append((p, full))

    _walk(tree)

    if not result and not selected_paths:
        def _walk_all(nodes: list[dict[str, Any]]) -> None:
            for n in nodes:
                if n["type"] == "dir" and "children" in n:
                    _walk_all(n["children"])
                elif n["type"] == "file":
                    full = temp_dir / n["path"]
                    if full.is_file():
                        result.append((n["path"], full))
        _walk_all(tree)

    return result


def _find_conversations_file(
    temp_dir: Path,
    tree: list[dict[str, Any]],
    all_files: list[tuple[str, Path]],
) -> tuple[str, Path] | None:
    candidates = ["conversations.json", "Gemini.json"]
    for cand in candidates:
        full = temp_dir / cand
        if full.is_file():
            return (cand, full)
    for cand in candidates:
        for rel, fp in all_files:
            if rel.split("/")[-1] == cand:
                return (rel, fp)
    for rel, fp in all_files:
        if rel.lower().endswith(".json"):
            try:
                import json as _json
                with open(fp, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    if "chat_messages" in data[0] or "mapping" in data[0]:
                        return (rel, fp)
            except Exception:
                continue
    return None
