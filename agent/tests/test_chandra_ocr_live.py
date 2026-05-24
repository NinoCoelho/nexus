"""Live OCR tests against the local chandra-ocr-2 model.

Sends real screenshots to the local chandra vision endpoint and checks
whether the model can extract text from images.

Requires the local llama.cpp server at http://127.0.0.1:52013.
Run::

    uv run pytest tests/test_chandra_ocr_live.py -v -s
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import pytest

_BASE_URL = "http://127.0.0.1:52013"
_MODEL = "chandra-ocr-2.Q8_0.gguf"
_IMAGE_DIR = Path("/Users/nino/Desktop/May8")
_MAX_DIM = 1280
_TIMEOUT = 300


def _discover_images() -> list[str]:
    if not _IMAGE_DIR.is_dir():
        return []
    return sorted(p.name for p in _IMAGE_DIR.glob("Screenshot*.png"))


def _resize(data: bytes) -> tuple[bytes, str]:
    try:
        from PIL import Image
    except ImportError:
        return data, "image/png"

    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > _MAX_DIM:
        ratio = _MAX_DIM / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue(), "image/jpeg"


async def _ask_chandra(image_data: bytes, mime: str, prompt: str) -> str:
    b64 = base64.b64encode(image_data).decode()
    payload = {
        "model": _MODEL,
        "temperature": 0.0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
    }
    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_BASE_URL}/chat/completions", json=payload, headers=headers
        )
    assert resp.status_code == 200, (
        f"chandra returned {resp.status_code}: {resp.text[:500]}"
    )
    body = resp.json()
    content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content).strip()


@pytest.fixture(autouse=True)
def _require_chandra_endpoint() -> None:
    try:
        resp = httpx.get(f"{_BASE_URL}/models", timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        pytest.skip(f"chandra endpoint {_BASE_URL} unreachable: {exc}")


_TEST_IMAGES = _discover_images()


@pytest.mark.parametrize(
    "image_name", _TEST_IMAGES, ids=lambda n: n.replace("\u202f", " ")
)
async def test_chandra_ocr_screenshot(image_name: str) -> None:
    """chandra-ocr-2 should extract text from screenshots."""
    raw = (_IMAGE_DIR / image_name).read_bytes()
    data, mime = _resize(raw)
    label = image_name.replace("\u202f", " ")

    text = await _ask_chandra(
        data,
        mime,
        "Extract every piece of text in this image as Markdown. "
        "Preserve layout: use Markdown headings, lists, and tables where "
        "they appear. Render equations in LaTeX. Do not summarize, "
        "interpret, or describe the image — only return what is written.",
    )

    print(f"\n--- OCR {label} ({len(raw)}b -> {len(data)}b) ---")
    print(text[:3000] if text else "(empty response)")
    print("---")

    assert len(text.strip()) >= 10, (
        f"chandra OCR returned too little for {label}: {text!r}"
    )


@pytest.mark.parametrize(
    "image_name", _TEST_IMAGES, ids=lambda n: n.replace("\u202f", " ")
)
async def test_chandra_describes_screenshot(image_name: str) -> None:
    """chandra-ocr-2 should be able to describe screenshot content."""
    raw = (_IMAGE_DIR / image_name).read_bytes()
    data, mime = _resize(raw)
    label = image_name.replace("\u202f", " ")

    text = await _ask_chandra(
        data,
        mime,
        "Describe what you see in this screenshot. List every visible UI "
        "element, text, window, or app. Be thorough.",
    )

    print(f"\n--- DESC {label} ({len(raw)}b -> {len(data)}b) ---")
    print(text[:3000] if text else "(empty response)")
    print("---")

    assert len(text.strip()) >= 20, (
        f"chandra returned too little for {label}: {text!r}"
    )
    assert "cannot see" not in text.lower() and "no image" not in text.lower(), (
        f"chandra claims it cannot see the image for {label}"
    )
