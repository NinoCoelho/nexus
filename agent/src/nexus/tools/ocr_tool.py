"""``ocr_image`` agent tool — OCR a vault file on demand.

The single OCR entry point exposed to the agent. It reads the file from
the vault, runs the OCR engine (typically the model picked as the
"Vision" role in Settings → Models), and returns the extracted text.

Results are cached as a sidecar ``<file>.ocr.txt`` keyed by source
mtime, so repeated calls on the same file are free.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agent.llm import ToolSpec

log = logging.getLogger(__name__)


OCR_IMAGE_TOOL = ToolSpec(
    name="ocr_image",
    description=(
        "Extract text from an image or scanned PDF stored in the vault. "
        "Call this whenever you see a breadcrumb like '[image attached: "
        "X — call `ocr_image`...]' or '[document attached: Y — likely "
        "scanned...]', or when the user asks you to read a screenshot, "
        "photo, or scanned document. Routes to the model the user picked "
        "as the 'Vision' role in Settings → Models (typically a "
        "specialist OCR model like chandra-ocr-2). Returns Markdown that "
        "preserves layout (headings, tables, lists). Results are cached "
        "as a sidecar .ocr.txt file keyed by mtime — repeated calls on "
        "the same file are free."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Vault-relative path to the image or PDF.",
            },
            "force": {
                "type": "boolean",
                "description": (
                    "Bypass the sidecar cache and re-OCR. Default false."
                ),
            },
        },
        "required": ["path"],
    },
)


@dataclass
class OcrToolResult:
    ok: bool
    text: str = ""
    engine: str = ""
    cached: bool = False
    error: str | None = None

    def to_text(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "text": self.text,
                "engine": self.engine,
                "cached": self.cached,
                "error": self.error,
            },
            ensure_ascii=False,
        )


def _sidecar_path(full: Path) -> Path:
    return full.with_suffix(full.suffix + ".ocr.txt")


def _read_cached(full: Path) -> str | None:
    sidecar = _sidecar_path(full)
    if not sidecar.exists():
        return None
    try:
        if sidecar.stat().st_mtime < full.stat().st_mtime:
            return None  # stale — source rewritten since cache
        return sidecar.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_cached(full: Path, text: str) -> None:
    sidecar = _sidecar_path(full)
    try:
        sidecar.write_text(text, encoding="utf-8")
        # Make sure the cache mtime is >= the source mtime so the
        # freshness check in ``_read_cached`` succeeds on the next call.
        src_mtime = full.stat().st_mtime
        os.utime(sidecar, (src_mtime, src_mtime))
    except OSError:
        log.warning("ocr_image: failed to write sidecar %s", sidecar, exc_info=True)


def _resolve_with_fallback(rel_path: str) -> tuple[Path | None, str | None]:
    """Resolve a vault-relative path, tolerant of agents that drop the
    upload prefix.

    Returns ``(full_path, None)`` on a hit or ``(None, error_message)``
    on a miss. The fallback order:

    1. Exact path as given.
    2. When the agent passed a bare basename (no ``/``), rglob the vault
       for matching files (capped at 5). One hit → use it. Multiple hits
       → surface them in the error so the agent retries with a qualified
       path.
    """
    from ..vault import _vault_root, resolve_path

    try:
        full = resolve_path(rel_path)
    except Exception as exc:  # noqa: BLE001
        return None, f"invalid vault path: {exc}"
    if full.is_file():
        return full, None

    if "/" not in rel_path:
        root = _vault_root()
        matches: list[Path] = []
        for hit in root.rglob(rel_path):
            if hit.is_file():
                matches.append(hit)
                if len(matches) >= 5:
                    break
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            rels = sorted(str(p.relative_to(root)) for p in matches)
            return None, (
                f"no such file: {rel_path}. Multiple matches: "
                f"{', '.join(rels)} — pass the full vault-relative path."
            )

    return None, f"no such file: {rel_path}"


async def handle_ocr_image_tool(args: dict[str, Any]) -> str:
    rel_path = (args.get("path") or "").strip()
    force = bool(args.get("force"))
    if not rel_path:
        return OcrToolResult(ok=False, error="`path` is required").to_text()

    from .. import ocr as _ocr
    from ..multimodal import is_pdf, sniff_mime

    full, err = _resolve_with_fallback(rel_path)
    if full is None:
        return OcrToolResult(ok=False, error=err).to_text()

    if not force:
        cached = _read_cached(full)
        if cached is not None:
            return OcrToolResult(
                ok=True, text=cached, engine="cache", cached=True
            ).to_text()

    if not _ocr.is_configured():
        return OcrToolResult(
            ok=False,
            error=(
                "no OCR engine configured; set [ocr] engine in "
                "~/.nexus/config.toml"
            ),
        ).to_text()

    mime = sniff_mime(rel_path)
    data = full.read_bytes()
    if is_pdf(mime):
        text = await _ocr.ocr_pdf_pages(data)
        engine = "pdf+" + _ocr.configured_engine()
    else:
        result = await _ocr.ocr_image(data, mime)
        text = result.text
        engine = result.engine
    if not text:
        return OcrToolResult(
            ok=False, engine=engine, error="OCR returned no text"
        ).to_text()
    _write_cached(full, text)
    return OcrToolResult(ok=True, text=text, engine=engine).to_text()
