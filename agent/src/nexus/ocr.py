"""OCR engine abstraction.

Single entry point ``ocr_image()`` used by the ``ocr_image`` agent tool.
The agent calls the tool when it needs the text inside a screenshot,
photo, or scanned PDF — there is no implicit "auto-OCR" path on the
chat hot loop. The breadcrumbs emitted by ``multimodal.materialize_message``
nudge the agent toward calling the tool.

Engine selected via ``[ocr]`` in ``~/.nexus/config.toml``. Degrades
gracefully — no engine configured / dep not installed / engine raised →
returns an empty :class:`OcrResult` and the tool surfaces a helpful
"configure the Vision role" error instead of crashing.

Engines:

* ``llm`` — talks to a vision-capable OpenAI-compatible endpoint (the
  recommended path for ``datalab-to/chandra-ocr-2`` served behind
  llama.cpp / vLLM / LM Studio / HF Inference Endpoints). Reuses
  ``[providers.<id>]`` credentials. No new Python deps in the agent
  process.
* ``rapidocr`` — pure-Python ONNX runtime. Install with
  ``uv sync --extra ocr``.
* ``tesseract`` — system ``tesseract`` binary via ``pytesseract``.

Routing: the user marks one ``[[models]]`` entry as the **vision** role
through the settings UI (the same place ``default`` / ``embedding`` /
``extraction`` are picked). That writes ``agent.vision_model`` in
``config.toml``; ``ocr.py`` resolves it to ``(provider, model_name)`` at
call time. Empty = no model wired up; OCR is then either disabled or
served by an explicit ``[ocr] engine`` override.

Example config (after picking the model in Settings → Models →
"Vision")::

    [agent]
    vision_model = "local-chandra-ocr-2/chandra-ocr-2"

    [ocr]                     # (optional) overrides — the role pick alone
                              # is enough when you only want LLM-based OCR.
    engine = ""               # "" = use the vision-role model.
                              # "rapidocr" / "tesseract" force a local engine.
    fallback = ""             # used when primary fails
    timeout_seconds = 120
    # prompt = "..."          # override the default extract-text prompt
"""

from __future__ import annotations

import asyncio
import base64
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Tuned for chandra-ocr-2's preferred output style (layout-aware Markdown
# with tables/equations). Generic enough for tesseract/rapidocr too —
# they ignore the prompt entirely.
_DEFAULT_PROMPT = (
    "Extract every piece of text in this image as Markdown. "
    "Preserve layout: use Markdown headings, lists, and tables where "
    "they appear. Render equations in LaTeX. Do not summarize, "
    "interpret, or describe the image — only return what is written."
)


@dataclass(frozen=True)
class OcrResult:
    text: str
    engine: str

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


def _read_ocr_section() -> dict[str, Any]:
    """Return just the ``[ocr]`` block from ``~/.nexus/config.toml``.

    Avoids extending NexusConfig because OCR settings are sparse and
    optional — users without an ``[ocr]`` block keep the existing
    breadcrumb behavior with zero configuration.
    """
    path = Path.home() / ".nexus" / "config.toml"
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except Exception:  # noqa: BLE001
        log.warning("ocr: failed to read config.toml", exc_info=True)
        return {}
    section = raw.get("ocr") or {}
    return section if isinstance(section, dict) else {}


def _resolve_vision_model() -> tuple[str, str] | None:
    """Return ``(provider_id, model_name)`` for the model picked as the
    ``vision`` role in the settings UI (``cfg.agent.vision_model``).
    Returns ``None`` if the role is unset or points at a model that no
    longer exists.
    """
    try:
        from .config_file import load as load_config

        cfg = load_config()
    except Exception:  # noqa: BLE001
        log.warning("ocr: failed to load config", exc_info=True)
        return None
    target_id = (cfg.agent.vision_model or "").strip()
    if not target_id:
        return None
    for entry in cfg.models:
        if entry.id == target_id:
            return entry.provider, entry.model_name
    log.warning(
        "ocr: agent.vision_model %r does not match any [[models]] entry",
        target_id,
    )
    return None


def is_configured() -> bool:
    """True when OCR can run — either an explicit ``[ocr] engine`` is set
    OR a model has been picked as the ``vision`` role.
    """
    if (_read_ocr_section().get("engine") or "").strip():
        return True
    return _resolve_vision_model() is not None


def configured_engine() -> str:
    """Effective engine name. Returns ``[ocr] engine`` when explicit,
    ``"llm"`` when a vision-role model auto-routes, or ``""`` when
    nothing is configured.
    """
    explicit = (_read_ocr_section().get("engine") or "").strip().lower()
    if explicit:
        return explicit
    if _resolve_vision_model() is not None:
        return "llm"
    return ""


async def ocr_image(data: bytes, mime: str) -> OcrResult:
    """Run OCR over an image blob using the configured engine.

    Tries the primary engine, then ``fallback`` when set. Returns an
    empty :class:`OcrResult` (``text=""``) when nothing succeeds —
    callers should treat empty as *fall back to a breadcrumb*.
    """
    if not data:
        return OcrResult(text="", engine="none")

    section = _read_ocr_section()
    primary = (section.get("engine") or "").strip().lower()
    fallback = (section.get("fallback") or "").strip().lower()
    # When the user hasn't set [ocr] engine but picked a vision-role
    # model in the UI, auto-route to the LLM engine — the model is
    # resolved in _ocr_via_llm.
    if not primary and _resolve_vision_model() is not None:
        primary = "llm"

    seen: set[str] = set()
    for engine in (primary, fallback):
        if not engine or engine in seen:
            continue
        seen.add(engine)
        try:
            text = await _dispatch(engine, data, mime, section)
        except Exception:  # noqa: BLE001
            log.warning("ocr: engine %r raised; trying fallback", engine, exc_info=True)
            continue
        if text and text.strip():
            return OcrResult(text=text.strip(), engine=engine)

    return OcrResult(text="", engine="none")


async def _dispatch(
    engine: str, data: bytes, mime: str, section: dict[str, Any]
) -> str:
    if engine == "llm":
        return await _ocr_via_llm(data, mime, section)
    if engine == "rapidocr":
        return await asyncio.to_thread(_ocr_via_rapidocr, data, mime)
    if engine == "tesseract":
        return await asyncio.to_thread(_ocr_via_tesseract, data, mime)
    log.warning("ocr: unknown engine %r", engine)
    return ""


# --- engine adapters --------------------------------------------------------


async def _ocr_via_llm(data: bytes, mime: str, section: dict[str, Any]) -> str:
    """Call a vision LLM over OpenAI-compat chat/completions.

    Resolves the provider's credentials via the same precedence the LLM
    provider registry uses (``credential_ref`` → ``use_inline_key`` →
    ``api_key_env``). The chandra path runs vLLM with ``--api-key`` empty
    or anonymous, so a missing key is fine.
    """
    import os as _os

    import httpx as _httpx

    from . import secrets as _secrets
    from .config_file import load as load_config

    prompt = (section.get("prompt") or _DEFAULT_PROMPT).strip()
    timeout = float(section.get("timeout_seconds") or 120.0)

    resolved = _resolve_vision_model()
    if resolved is None:
        log.warning(
            "ocr.llm: no model configured. Pick a model as the "
            '"Vision" role in Settings → Models.'
        )
        return ""
    provider_id, model = resolved

    cfg = load_config()
    pcfg = cfg.providers.get(provider_id)
    if pcfg is None:
        log.warning("ocr.llm: provider %r not in [providers.*]", provider_id)
        return ""
    base_url = (pcfg.base_url or "").rstrip("/")
    if not base_url:
        return ""

    api_key = ""
    cred_ref = getattr(pcfg, "credential_ref", None)
    if cred_ref:
        api_key = _secrets.resolve(cred_ref) or ""
    elif getattr(pcfg, "use_inline_key", False):
        api_key = _secrets.get(provider_id) or ""
    elif getattr(pcfg, "api_key_env", ""):
        api_key = _os.environ.get(pcfg.api_key_env, "")

    b64 = base64.b64encode(data).decode("ascii")
    payload = {
        "model": model,
        "temperature": 0.0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime or 'image/png'};base64,{b64}"
                        },
                    },
                ],
            }
        ],
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with _httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/chat/completions", json=payload, headers=headers
        )
        resp.raise_for_status()
        body = resp.json()
    choice = (body.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    if isinstance(content, list):
        # Some providers return multipart content even for text replies.
        content = "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content).strip()


def _ocr_via_rapidocr(data: bytes, mime: str) -> str:
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ImportError:
        log.warning("ocr.rapidocr: not installed (uv sync --extra ocr)")
        return ""
    try:
        import io

        import numpy as np  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        log.warning("ocr.rapidocr: Pillow/numpy not installed (uv sync --extra ocr)")
        return ""

    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.array(img)
    engine = RapidOCR()
    result, _ = engine(arr)
    if not result:
        return ""
    lines = [r[1] for r in result if len(r) > 1 and isinstance(r[1], str)]
    return "\n".join(lines).strip()


def _ocr_via_tesseract(data: bytes, mime: str) -> str:
    try:
        import pytesseract  # type: ignore[import-not-found]
    except ImportError:
        log.warning(
            "ocr.tesseract: pytesseract not installed; "
            "`pip install pytesseract` and `brew install tesseract`"
        )
        return ""
    try:
        import io

        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        log.warning("ocr.tesseract: Pillow not installed")
        return ""
    img = Image.open(io.BytesIO(data))
    return (pytesseract.image_to_string(img) or "").strip()


# --- scanned-PDF helper -----------------------------------------------------


async def ocr_pdf_pages(data: bytes, *, max_pages: int = 4) -> str:
    """Rasterize the first ``max_pages`` pages of a PDF and OCR each.

    Used by ``multimodal.extract_text_from_document`` when ``pypdf``
    returned no extractable text — i.e. the PDF is image-only/scanned.
    Returns concatenated, page-tagged Markdown. Empty string when no OCR
    engine is configured or ``pdf2image``/Poppler isn't installed.
    """
    if not is_configured():
        return ""
    try:
        from pdf2image import convert_from_bytes  # type: ignore[import-not-found]
    except ImportError:
        log.warning(
            "ocr.pdf: pdf2image not installed — `uv sync --extra ocr` "
            "and ensure poppler is on PATH (brew install poppler)"
        )
        return ""

    try:
        images = convert_from_bytes(data, last_page=max_pages, dpi=200)
    except Exception:  # noqa: BLE001
        log.warning("ocr.pdf: rasterization failed", exc_info=True)
        return ""

    chunks: list[str] = []
    for idx, img in enumerate(images, start=1):
        import io

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result = await ocr_image(buf.getvalue(), "image/png")
        if result.ok:
            chunks.append(f"## Page {idx}\n\n{result.text}")
    return "\n\n".join(chunks)
