"""Image-generation clients for OpenAI ``gpt-image-1`` and Gemini ``nano banana``.

Two thin async functions, each returning ``list[bytes]`` of decoded PNG/JPEG
data. Errors raise :class:`LLMTransportError` so the existing error
classifier formats them consistently. No vendor SDKs — both providers ship
plain HTTP shapes that are easier to read and reason about than yet
another wrapper.

* OpenAI uses ``POST /v1/images/generations`` (text-to-image) and
  ``POST /v1/images/edits`` (image+prompt → image). The ``gpt-image-1``
  model defaults its response to ``data[i].b64_json`` and does not accept
  the legacy ``response_format`` knob.
* Gemini uses the **native** ``generateContent`` endpoint. The
  ``/v1beta/openai`` compatibility shim does not expose image generation,
  so we hit ``generativelanguage.googleapis.com/v1beta/models`` directly
  with the API key as a query parameter and parse ``inline_data`` parts.

Both providers reuse the API keys already configured in the unified
provider catalog (``OPENAI_API_KEY`` / ``GEMINI_API_KEY``) — the tool
handler in :mod:`nexus.tools.image_gen_tool` resolves them via the same
secrets precedence used by :func:`nexus.agent.registry.build_registry`.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from .llm.types import LLMTransportError

log = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

DEFAULT_OPENAI_MODEL = "gpt-image-1"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-image"


def _decode_b64(value: str) -> bytes:
    try:
        return base64.b64decode(value)
    except (ValueError, TypeError) as exc:
        raise LLMTransportError(
            f"image-gen: malformed base64 in upstream response: {exc}"
        ) from exc


def _raise_for_status(resp: httpx.Response, *, where: str) -> None:
    if resp.status_code < 400:
        return
    body: dict[str, Any] = {}
    try:
        parsed = resp.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:  # noqa: BLE001 — non-json error body
        body = {}
    raise LLMTransportError(
        f"{where}: HTTP {resp.status_code}: {resp.text[:400]}",
        status_code=resp.status_code,
        body=body,
    )


async def openai_image(
    api_key: str,
    prompt: str,
    *,
    base_url: str = OPENAI_BASE_URL,
    model: str = DEFAULT_OPENAI_MODEL,
    size: str = "1024x1024",
    n: int = 1,
    reference_image: bytes | None = None,
    reference_mime: str = "image/png",
) -> list[bytes]:
    """Generate (or edit) one or more images via the OpenAI Images API.

    When ``reference_image`` is provided the call switches to the
    ``/images/edits`` endpoint, which expects ``multipart/form-data`` —
    OpenAI uses the reference as the source for an instruction-driven
    edit.

    Returns: a list of raw image byte strings (one per ``n``).
    """
    if not api_key:
        raise LLMTransportError("openai: missing API key for image generation")
    if not prompt:
        raise LLMTransportError("openai: prompt is required")

    headers = {"Authorization": f"Bearer {api_key}"}
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        if reference_image is None:
            payload = {
                "model": model,
                "prompt": prompt,
                "n": int(n),
                "size": size,
            }
            resp = await client.post(
                f"{base}/images/generations",
                json=payload,
                headers={**headers, "Content-Type": "application/json"},
            )
        else:
            files: dict[str, Any] = {
                "image": ("reference" + _ext_from_mime(reference_mime),
                          reference_image, reference_mime),
            }
            data = {
                "model": model,
                "prompt": prompt,
                "n": str(int(n)),
                "size": size,
            }
            resp = await client.post(
                f"{base}/images/edits",
                data=data,
                files=files,
                headers=headers,
            )

    _raise_for_status(resp, where="openai-image")

    body = resp.json()
    items = body.get("data") or []
    if not items:
        raise LLMTransportError(f"openai-image: empty data field; body={body!r}")

    out: list[bytes] = []
    for item in items:
        b64 = item.get("b64_json")
        if not b64:
            url = item.get("url")
            if not url:
                raise LLMTransportError(
                    f"openai-image: response item has neither b64_json nor url: {item!r}"
                )
            out.append(await _fetch_url_bytes(url))
            continue
        out.append(_decode_b64(b64))
    return out


async def gemini_image(
    api_key: str,
    prompt: str,
    *,
    base_url: str = GEMINI_BASE_URL,
    model: str = DEFAULT_GEMINI_MODEL,
    n: int = 1,
    reference_image: bytes | None = None,
    reference_mime: str = "image/png",
) -> list[bytes]:
    """Generate (or edit) images via Gemini's native ``generateContent``.

    The OpenAI compatibility shim (``/v1beta/openai``) does **not** expose
    image generation, so this function bypasses ``ProviderConfig.base_url``
    and hits the native endpoint directly. ``api_key`` rides the URL as a
    query parameter — that's the only auth scheme the native endpoint
    accepts; the ``Authorization`` header is ignored.

    Pass ``reference_image`` to drive an image-edit (the model treats the
    reference as the source and ``prompt`` as the instruction).
    """
    if not api_key:
        raise LLMTransportError("gemini: missing API key for image generation")
    if not prompt:
        raise LLMTransportError("gemini: prompt is required")

    parts: list[dict[str, Any]] = [{"text": prompt}]
    if reference_image is not None:
        parts.append(
            {
                "inline_data": {
                    "mime_type": reference_mime,
                    "data": base64.b64encode(reference_image).decode("ascii"),
                }
            }
        )

    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
    }
    if n and n > 1:
        # Gemini exposes a ``candidate_count`` knob; not all image-gen
        # models honour it but we forward it anyway so callers can ask
        # for more than one image without an error from our side.
        payload["generation_config"] = {"candidate_count": int(n)}

    base = base_url.rstrip("/")
    url = f"{base}/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        resp = await client.post(
            url,
            params={"key": api_key},
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    _raise_for_status(resp, where="gemini-image")

    body = resp.json()
    candidates = body.get("candidates") or []
    if not candidates:
        # Gemini sometimes returns ``promptFeedback.blockReason`` instead
        # of candidates when the request was filtered upstream — surface
        # that as the error so the user sees why nothing came back.
        feedback = body.get("promptFeedback") or {}
        raise LLMTransportError(
            f"gemini-image: no candidates returned (feedback={feedback!r})"
        )

    out: list[bytes] = []
    for cand in candidates:
        content_parts = (cand.get("content") or {}).get("parts") or []
        for part in content_parts:
            inline = part.get("inline_data") or part.get("inlineData")
            if not inline:
                continue
            data = inline.get("data")
            if not data:
                continue
            out.append(_decode_b64(data))
    if not out:
        raise LLMTransportError(
            f"gemini-image: candidates had no inline_data parts; body={body!r}"
        )
    return out


# ---------------------------------------------------------------------------
# Helpers


def _ext_from_mime(mime: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime, ".png")


async def _fetch_url_bytes(url: str) -> bytes:
    """Download an image when the upstream returned a URL instead of base64.

    Used for the rare case where a provider/proxy switches the response
    shape on us — keeps the caller contract (``list[bytes]``) consistent.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.get(url)
    if resp.status_code >= 400:
        raise LLMTransportError(
            f"image-gen: download failed: HTTP {resp.status_code}",
            status_code=resp.status_code,
        )
    return resp.content


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OPENAI_MODEL",
    "GEMINI_BASE_URL",
    "OPENAI_BASE_URL",
    "gemini_image",
    "openai_image",
]
