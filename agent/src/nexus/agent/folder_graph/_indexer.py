"""Folder indexing as an SSE stream.

Mirrors the global ``graphrag_manager._indexer.index_vault_streaming`` shape
so the UI's progress component can be near-identical, but operates on an
arbitrary folder + per-folder engine + per-folder manifest.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator

from ._scanner import iter_indexable_files
from ._storage import (
    content_hash,
    get_meta_kv,
    is_file_current,
    normalize_folder,
    ontology_hash,
    remove_file,
    upsert_file,
)

log = logging.getLogger(__name__)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def list_indexable_files(folder: str | Path) -> list[dict[str, Any]]:
    """Cheap directory walk for "how big is this folder" UX previews."""
    return [
        {"rel_path": rp, "size": size, "mtime": mtime}
        for rp, _, mtime, size in iter_indexable_files(folder)
    ]


async def index_folder_streaming(folder, *, cfg, ontology, full: bool = False
                                 ) -> AsyncIterator[str]:
    """Yield SSE frames while indexing the folder into its per-folder engine.

    Event types: ``status``, ``phase``, ``file``, ``error``, ``stats``, ``done``.
    Phases: ``loading-embedder``, ``scanning``, ``extracting``, ``writing``.
    """
    from ._engine_pool import open_folder_engine

    folder_p = normalize_folder(folder)
    yield _sse("phase", {"phase": "loading-embedder"})

    try:
        entry = open_folder_engine(folder_p, ontology, cfg)
    except Exception as exc:
        log.exception("[folder_graph] failed to open engine for %s", folder_p)
        yield _sse("error", {"detail": f"engine init failed: {exc}"})
        return

    engine = entry["engine"]
    manifest = entry["manifest"]

    # Detect ontology drift: compare the ontology being indexed against the
    # hash recorded after the last *successful* build (``build_ontology_hash``).
    # ``ontology_hash`` in meta is updated immediately when the user saves the
    # ontology via PUT, so comparing against it would always match — we need
    # the build-time snapshot to detect real drift.
    build_hash = get_meta_kv(manifest, "build_ontology_hash") or ""
    new_hash = ontology_hash(ontology)
    if not full and build_hash and new_hash != build_hash:
        log.info("[folder_graph] ontology drift on %s — forcing full reindex", folder_p)
        full = True

    if full:
        yield _sse("status", {"message": "Dropping existing per-folder data…"})
        try:
            from ._storage import all_indexed_files
            for rel_path in all_indexed_files(manifest):
                try:
                    engine.remove_source(rel_path)
                except Exception:
                    pass
                remove_file(manifest, rel_path)
        except Exception:
            log.warning("[folder_graph] full-reindex drop failed", exc_info=True)

    yield _sse("phase", {"phase": "scanning"})
    files = list(iter_indexable_files(folder_p))
    total = len(files)
    label = "full reindex" if full else "incremental update"
    yield _sse("status", {"message": f"Found {total} file(s) — {label}"})

    yield _sse("phase", {"phase": "extracting"})

    files_done = 0
    files_indexed = 0
    files_skipped = 0
    t0 = time.monotonic()

    # Snapshot current on-disk paths for removal detection (incremental only).
    on_disk_paths = {rp for rp, *_ in files}
    if not full:
        from ._storage import all_indexed_files
        for stale_path in list(all_indexed_files(manifest).keys()):
            if stale_path not in on_disk_paths:
                try:
                    engine.remove_source(stale_path)
                except Exception:
                    log.warning("[folder_graph] remove_source(%s) failed", stale_path,
                                exc_info=True)
                remove_file(manifest, stale_path)

    for rel_path, abs_path, mtime, size in files:
        try:
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                yield _sse("error", {"path": rel_path, "detail": f"read failed: {exc}"})
                files_done += 1
                continue
            if not content.strip():
                files_done += 1
                files_skipped += 1
                continue

            hash_ = content_hash(content)
            if not full and is_file_current(manifest, rel_path, mtime=mtime, hash_=hash_):
                files_done += 1
                files_skipped += 1
                yield _sse("file", {
                    "path": rel_path,
                    "files_done": files_done,
                    "files_total": total,
                    "skipped": True,
                    "entities": engine._entity_graph.count_entities(),
                    "triples": engine._entity_graph.count_triples(),
                })
                continue

            await engine.index_source(rel_path, content)
            upsert_file(manifest, rel_path, mtime=mtime, size=size, hash_=hash_)
            files_done += 1
            files_indexed += 1
            yield _sse("file", {
                "path": rel_path,
                "files_done": files_done,
                "files_total": total,
                "skipped": False,
                "entities": engine._entity_graph.count_entities(),
                "triples": engine._entity_graph.count_triples(),
            })
        except Exception as exc:
            yield _sse("error", {"path": rel_path, "detail": str(exc)})
            files_done += 1

    elapsed = round(time.monotonic() - t0, 1)
    yield _sse("phase", {"phase": "writing"})

    from ._storage import set_meta_kv
    set_meta_kv(manifest, "build_ontology_hash", new_hash)

    yield _sse("stats", {
        "files_done": files_done,
        "files_total": total,
        "files_indexed": files_indexed,
        "files_skipped": files_skipped,
        "entities": engine._entity_graph.count_entities(),
        "triples": engine._entity_graph.count_triples(),
        "elapsed_s": elapsed,
    })
    yield _sse("done", {})
    log.info(
        "[folder_graph] %s complete on %s (%d indexed, %d skipped, %.1fs)",
        label, folder_p, files_indexed, files_skipped, elapsed,
    )
