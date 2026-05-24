"""Tests for the OCR engine abstraction + multimodal/agent-tool wiring.

We don't depend on a real OCR engine being installed — every adapter is
monkey-patched. The point is to exercise the routing logic:

* Engine selection from ``[ocr]`` in config.toml.
* Fallback when the primary engine returns empty.
* Image branch in ``materialize_message`` substitutes OCR text when
  configured (and falls back to the existing breadcrumb when not).
* Scanned-PDF branch calls ``ocr_pdf_pages`` only when ``pypdf``
  yielded nothing.
* The ``ocr_image`` agent tool caches results as a sidecar file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loom.types import Role
from nexus import ocr
from nexus.agent.llm.types import ChatMessage, ContentPart
from nexus.multimodal import materialize_message
from nexus.tools.ocr_tool import handle_ocr_image_tool


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_PDF = b"%PDF-1.4 fake"


@pytest.fixture(autouse=True)
def _isolate_vault_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point vault + config.toml + the OCR section reader at tmp_path so
    tests never touch the real ``~/.nexus``."""
    monkeypatch.setattr("nexus.vault._VAULT_ROOT", tmp_path / "vault")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("nexus.ocr.Path.home", staticmethod(lambda: tmp_path.parent))
    # ``Path.home() / ".nexus" / "config.toml"`` would resolve to
    # tmp_path.parent/.nexus/config.toml — easier to just override the
    # reader directly so we control exactly what each test sees.
    monkeypatch.setattr("nexus.ocr._read_ocr_section", lambda: _read_ocr_section.section)
    _read_ocr_section.section = {}
    # Empty config so _resolve_vision_model finds no vision-role model
    # by default — individual tests opt-in via ``_patch_cfg``.
    from nexus.config_schema import NexusConfig

    monkeypatch.setattr("nexus.config_file.load", lambda: NexusConfig())


class _read_ocr_section:
    section: dict = {}


def _set_ocr(section: dict) -> None:
    _read_ocr_section.section = section


def _write_vault(rel_path: str, data: bytes) -> None:
    from nexus import vault as _vault

    _vault.write_file_bytes(rel_path, data)


# --- ocr_image dispatch ---------------------------------------------------


async def test_ocr_image_returns_empty_when_no_engine_configured() -> None:
    result = await ocr.ocr_image(_PNG, "image/png")
    assert result.text == ""
    assert result.engine == "none"
    assert not result.ok


async def test_ocr_image_routes_to_configured_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_ocr({"engine": "rapidocr"})
    monkeypatch.setattr("nexus.ocr._ocr_via_rapidocr", lambda data, mime: "hello world")
    result = await ocr.ocr_image(_PNG, "image/png")
    assert result.text == "hello world"
    assert result.engine == "rapidocr"
    assert result.ok


async def test_ocr_image_falls_back_when_primary_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_ocr({"engine": "rapidocr", "fallback": "tesseract"})
    monkeypatch.setattr("nexus.ocr._ocr_via_rapidocr", lambda data, mime: "")
    monkeypatch.setattr("nexus.ocr._ocr_via_tesseract", lambda data, mime: "from tesseract")
    result = await ocr.ocr_image(_PNG, "image/png")
    assert result.text == "from tesseract"
    assert result.engine == "tesseract"


async def test_ocr_image_swallows_engine_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_ocr({"engine": "rapidocr", "fallback": "tesseract"})

    def _boom(data: bytes, mime: str) -> str:
        raise RuntimeError("dep missing")

    monkeypatch.setattr("nexus.ocr._ocr_via_rapidocr", _boom)
    monkeypatch.setattr("nexus.ocr._ocr_via_tesseract", lambda data, mime: "ok")
    result = await ocr.ocr_image(_PNG, "image/png")
    assert result.text == "ok"
    assert result.engine == "tesseract"


# --- materialize_message breadcrumbs ---------------------------------------


async def test_materialize_image_breadcrumb_hints_at_tool_when_ocr_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vision model + OCR configured → breadcrumb tells the agent
    it can call `ocr_image` to extract the text."""
    _set_ocr({"engine": "rapidocr"})
    _write_vault("uploads/scan.png", _PNG)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(
                kind="image", vault_path="uploads/scan.png", mime_type="image/png"
            ),
        ],
    )
    out = await materialize_message(msg, {"chat", "tools"})
    assert isinstance(out.content, list)
    assert [p.kind for p in out.content] == ["text"]
    text = out.content[0].text or ""
    assert "ocr_image" in text
    assert "uploads/scan.png" in text


async def test_materialize_image_breadcrumb_when_ocr_unconfigured() -> None:
    """No vision capability + no OCR engine → original breadcrumb."""
    _write_vault("uploads/scan.png", _PNG)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(
                kind="image", vault_path="uploads/scan.png", mime_type="image/png"
            ),
        ],
    )
    out = await materialize_message(msg, {"chat", "tools"})
    assert isinstance(out.content, list)
    assert "does not support vision" in (out.content[0].text or "")
    # No OCR configured → the breadcrumb must NOT advertise the tool.
    assert "ocr_image" not in (out.content[0].text or "")


async def test_materialize_image_passes_through_for_vision_model() -> None:
    _write_vault("uploads/scan.png", _PNG)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(
                kind="image", vault_path="uploads/scan.png", mime_type="image/png"
            ),
        ],
    )
    out = await materialize_message(msg, {"chat", "tools", "vision"})
    assert isinstance(out.content, list)
    assert [p.kind for p in out.content] == ["image"]


async def test_materialize_pdf_breadcrumb_hints_at_tool_when_pypdf_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scanned PDF (pypdf returns "") + OCR configured → the breadcrumb
    tells the agent the PDF is likely scanned and to call ocr_image."""
    _set_ocr({"engine": "rapidocr"})
    monkeypatch.setattr(
        "nexus.multimodal.extract_text_from_document", lambda data, mime: ""
    )

    _write_vault("uploads/scan.pdf", _PDF)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(
                kind="document",
                vault_path="uploads/scan.pdf",
                mime_type="application/pdf",
            ),
        ],
    )
    out = await materialize_message(msg, {"chat", "tools"})
    assert isinstance(out.content, list)
    text = out.content[0].text or ""
    assert "scanned" in text.lower()
    assert "ocr_image" in text


# --- ocr_image agent tool --------------------------------------------------


async def test_ocr_tool_extracts_then_caches_via_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_ocr({"engine": "rapidocr"})
    calls = {"n": 0}

    def _fake(data: bytes, mime: str) -> str:
        calls["n"] += 1
        return f"text from call {calls['n']}"

    monkeypatch.setattr("nexus.ocr._ocr_via_rapidocr", _fake)
    _write_vault("uploads/scan.png", _PNG)

    res1 = json.loads(await handle_ocr_image_tool({"path": "uploads/scan.png"}))
    assert res1["ok"] and res1["cached"] is False
    assert res1["text"] == "text from call 1"

    # Second call hits the sidecar.
    res2 = json.loads(await handle_ocr_image_tool({"path": "uploads/scan.png"}))
    assert res2["ok"] and res2["cached"] is True
    assert res2["text"] == "text from call 1"
    assert calls["n"] == 1

    # force=true bypasses the cache.
    res3 = json.loads(
        await handle_ocr_image_tool({"path": "uploads/scan.png", "force": True})
    )
    assert res3["ok"] and res3["cached"] is False
    assert res3["text"] == "text from call 2"
    assert calls["n"] == 2


async def test_ocr_tool_errors_when_engine_unconfigured() -> None:
    _write_vault("uploads/scan.png", _PNG)
    res = json.loads(await handle_ocr_image_tool({"path": "uploads/scan.png"}))
    assert res["ok"] is False
    assert "no OCR engine configured" in (res["error"] or "")


async def test_ocr_tool_errors_when_path_missing() -> None:
    _set_ocr({"engine": "rapidocr"})
    res = json.loads(await handle_ocr_image_tool({"path": "nope/missing.png"}))
    assert res["ok"] is False
    assert "no such file" in (res["error"] or "")


async def test_ocr_tool_resolves_bare_basename_via_uploads_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agents drop the ``uploads/`` prefix; the tool finds the file anyway."""
    _set_ocr({"engine": "rapidocr"})
    monkeypatch.setattr(
        "nexus.ocr._ocr_via_rapidocr", lambda data, mime: "extracted"
    )
    _write_vault("uploads/ReceiptSwiss.jpg", _PNG)
    res = json.loads(
        await handle_ocr_image_tool({"path": "ReceiptSwiss.jpg"})
    )
    assert res["ok"] is True
    assert res["text"] == "extracted"


async def test_ocr_tool_resolves_basename_via_glob_when_not_in_uploads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare basename + file lives elsewhere → rglob finds it."""
    _set_ocr({"engine": "rapidocr"})
    monkeypatch.setattr(
        "nexus.ocr._ocr_via_rapidocr", lambda data, mime: "extracted"
    )
    _write_vault("archive/2024/old.png", _PNG)
    res = json.loads(await handle_ocr_image_tool({"path": "old.png"}))
    assert res["ok"] is True
    assert res["text"] == "extracted"


async def test_ocr_tool_returns_candidates_on_ambiguous_basename() -> None:
    """Multiple basename matches → error lists them so the agent retries."""
    _set_ocr({"engine": "rapidocr"})
    _write_vault("uploads/receipt.jpg", _PNG)
    _write_vault("archive/receipt.jpg", _PNG)
    res = json.loads(await handle_ocr_image_tool({"path": "receipt.jpg"}))
    assert res["ok"] is False
    err = res["error"] or ""
    assert "Multiple matches" in err
    assert "uploads/receipt.jpg" in err
    assert "archive/receipt.jpg" in err


# --- vision-role auto-routing ----------------------------------------------


def _patch_cfg(
    monkeypatch: pytest.MonkeyPatch,
    *,
    models: list,
    providers: dict | None = None,
    vision_model: str = "",
) -> None:
    from nexus.config_schema import AgentConfig, NexusConfig

    cfg = NexusConfig(
        agent=AgentConfig(vision_model=vision_model),
        providers=providers or {},
        models=models,
    )
    monkeypatch.setattr("nexus.config_file.load", lambda: cfg)


def test_is_configured_picks_up_vision_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.config_schema import ModelEntry

    _patch_cfg(
        monkeypatch,
        models=[
            ModelEntry(
                id="local-chandra/chandra-ocr-2",
                provider="local-chandra",
                model_name="chandra-ocr-2",
            )
        ],
        vision_model="local-chandra/chandra-ocr-2",
    )
    assert ocr.is_configured() is True
    assert ocr.configured_engine() == "llm"


def test_is_not_configured_when_vision_model_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus.config_schema import ModelEntry

    _patch_cfg(
        monkeypatch,
        models=[
            ModelEntry(
                id="local-chandra/chandra-ocr-2",
                provider="local-chandra",
                model_name="chandra-ocr-2",
            )
        ],
        vision_model="",
    )
    assert ocr.is_configured() is False
    assert ocr.configured_engine() == ""


def test_is_not_configured_when_vision_model_id_dangles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dangling vision_model_id (model deleted) acts as if cleared."""
    from nexus.config_schema import ModelEntry

    _patch_cfg(
        monkeypatch,
        models=[
            ModelEntry(
                id="local-chandra/chandra-ocr-2",
                provider="local-chandra",
                model_name="chandra-ocr-2",
            )
        ],
        vision_model="something/that-was-deleted",
    )
    assert ocr.is_configured() is False


def test_explicit_engine_overrides_vision_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.config_schema import ModelEntry

    _set_ocr({"engine": "rapidocr"})
    _patch_cfg(
        monkeypatch,
        models=[
            ModelEntry(
                id="local-chandra/chandra-ocr-2",
                provider="local-chandra",
                model_name="chandra-ocr-2",
            )
        ],
        vision_model="local-chandra/chandra-ocr-2",
    )
    assert ocr.configured_engine() == "rapidocr"


async def test_llm_engine_resolves_vision_role_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """vision_model picks the (provider, model) tuple ocr_via_llm uses."""
    from nexus.config_schema import ModelEntry, ProviderConfig

    _patch_cfg(
        monkeypatch,
        models=[
            ModelEntry(
                id="local-chandra/chandra-ocr-2",
                provider="local-chandra",
                model_name="chandra-ocr-2",
            )
        ],
        providers={
            "local-chandra": ProviderConfig(
                base_url="http://127.0.0.1:59903",
                api_key_env="",
                use_inline_key=False,
                type="ollama",
            )
        },
        vision_model="local-chandra/chandra-ocr-2",
    )

    seen: dict = {}

    async def _fake_post(self, url, json=None, headers=None):  # noqa: ANN001
        seen["url"] = url
        seen["model"] = json["model"]

        class _R:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {
                    "choices": [{"message": {"content": "extracted from chandra"}}]
                }

        return _R()

    monkeypatch.setattr("httpx.AsyncClient.post", _fake_post)

    result = await ocr.ocr_image(_PNG, "image/png")
    assert result.text == "extracted from chandra"
    assert result.engine == "llm"
    assert seen["model"] == "chandra-ocr-2"
    assert seen["url"].endswith("/chat/completions")
