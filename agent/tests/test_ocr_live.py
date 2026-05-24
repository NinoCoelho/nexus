"""Live vision/OCR tests against the **nexus** cloud model.

Sends real screenshots directly to the nexus model's chat/completions
endpoint and checks whether the model can understand and describe/OCR
the image content.

Reads provider config (base URL, credentials) from ``~/.nexus/config.toml``.
Skips automatically when the endpoint is unreachable.

Run::

    uv run pytest tests/test_ocr_live.py -v -s
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import httpx
import pytest

from nexus.config_file import load as load_config
from nexus.secrets import resolve as resolve_secret

_IMAGE_DIR = Path("/Users/nino/Desktop/May8")
_MAX_DIM = 2048


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


def _nexus_client_config() -> tuple[str, str, str]:
    """Return ``(base_url, model_name, api_key)`` for the nexus model."""
    cfg = load_config()
    entry = next((m for m in cfg.models if m.id == "nexus"), None)
    if entry is None:
        pytest.skip("No model with id='nexus' in config")
    pcfg = cfg.providers.get(entry.provider)
    if pcfg is None:
        pytest.skip("nexus provider not configured")
    api_key = ""
    cred_ref = getattr(pcfg, "credential_ref", None)
    if cred_ref:
        api_key = resolve_secret(cred_ref) or ""
    return pcfg.base_url.rstrip("/"), entry.model_name, api_key


@pytest.fixture(autouse=True)
def _require_nexus_endpoint() -> None:
    base_url, _, api_key = _nexus_client_config()
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = httpx.get(f"{base_url}/models", headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception:
        pytest.skip(f"nexus endpoint {base_url} unreachable")


async def _ask_nexus(image_data: bytes, mime: str, prompt: str) -> str:
    base_url, model_name, api_key = _nexus_client_config()

    b64 = base64.b64encode(image_data).decode()
    payload = {
        "model": model_name,
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
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{base_url}/chat/completions", json=payload, headers=headers
        )
    assert resp.status_code == 200, (
        f"nexus returned {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    return (body.get("choices") or [{}])[0].get("message", {}).get("content", "")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_TEST_IMAGES = _discover_images()


@pytest.mark.parametrize(
    "image_name", _TEST_IMAGES, ids=lambda n: n.replace("\u202f", " ")
)
async def test_nexus_model_describes_screenshot(image_name: str) -> None:
    """The nexus model should be able to describe the content of a screenshot."""
    raw = (_IMAGE_DIR / image_name).read_bytes()
    data, mime = _resize(raw)
    label = image_name.replace("\u202f", " ")

    text = await _ask_nexus(
        data,
        mime,
        "Describe what you see in this screenshot. List every visible UI element, "
        "text, window, or app. Be thorough.",
    )

    print(f"\n--- {label} ({len(raw)}b -> {len(data)}b) ---")
    print(text[:2000] if text else "(empty response)")
    print("---")

    assert len(text.strip()) >= 20, (
        f"nexus returned too little for {label}: {text!r}"
    )
    assert "cannot see" not in text.lower() and "no image" not in text.lower(), (
        f"nexus claims it cannot see the image for {label}"
    )


@pytest.mark.parametrize(
    "image_name", _TEST_IMAGES, ids=lambda n: n.replace("\u202f", " ")
)
async def test_nexus_model_ocr_screenshot(image_name: str) -> None:
    """The nexus model should extract text from screenshots."""
    raw = (_IMAGE_DIR / image_name).read_bytes()
    data, mime = _resize(raw)
    label = image_name.replace("\u202f", " ")

    text = await _ask_nexus(
        data,
        mime,
        "Extract every piece of visible text from this screenshot as Markdown. "
        "Preserve layout. Do not describe or interpret \u2014 only return what is written.",
    )

    print(f"\n--- OCR {label} ---")
    print(text[:2000] if text else "(empty response)")
    print("---")

    assert len(text.strip()) >= 10, (
        f"nexus OCR returned too little for {label}: {text!r}"
    )
