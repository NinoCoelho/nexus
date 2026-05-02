"""Tests for multimodal input encoding.

Covers:

* Capability lookup from the catalog (vision/document tags).
* ``materialize_message`` lowering — image+vision passes through;
  image without vision becomes a text breadcrumb; PDF without document
  capability gets text-extracted (or breadcrumbed when pypdf isn't
  installed).
* Provider encoders translating ``ContentPart``s to OpenAI ``image_url``
  blocks and Anthropic native ``image`` source blocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent.llm.types import ChatMessage, ContentPart
from nexus.agent.llm.openai import _encode_msg as _encode_openai
from nexus.agent.llm.anthropic import _encode_msg_anthropic
from nexus.multimodal import materialize_message
from nexus.providers.catalog import (
    capabilities_for_model_name,
    load_catalog,
)
from loom.types import Role


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _isolate_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nexus.vault._VAULT_ROOT", tmp_path / "vault")
    # The catalog is process-cached; clear it so other tests don't see
    # capability mutations leak across modules.
    load_catalog.cache_clear()
    # Isolate from the real ``~/.nexus/config.toml`` — without this, any
    # OCR/vision routing the user has configured leaks into the
    # breadcrumb branches and changes the wording these tests assert.
    monkeypatch.setattr("nexus.ocr._read_ocr_section", lambda: {})
    monkeypatch.setattr("nexus.ocr._resolve_vision_model", lambda: None)


def _write_vault(path: str, data: bytes) -> None:
    from nexus import vault as _vault
    _vault.write_file_bytes(path, data)


def test_capability_lookup_recognises_vision_models() -> None:
    caps = capabilities_for_model_name("claude-sonnet-4-6")
    assert "chat" in caps and "vision" in caps and "document" in caps

    caps_openai = capabilities_for_model_name("gpt-4o")
    assert "vision" in caps_openai

    # Unknown model → empty set (encoder will fall back to breadcrumb).
    assert capabilities_for_model_name("model-that-does-not-exist") == set()

    # Image-only models exist in the catalog and don't claim "chat".
    caps_imggen = capabilities_for_model_name("gpt-image-1")
    assert "image" in caps_imggen
    assert "chat" not in caps_imggen


def test_user_config_tags_extend_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model unknown to the catalog gets vision capability when the
    user's ``[[models]] tags`` declares it. Escape hatch for local GGUFs."""
    from nexus.config_schema import ModelEntry, NexusConfig

    cfg = NexusConfig(
        models=[
            ModelEntry(
                id="local-gemma/gemma-4-26b",
                provider="local-gemma",
                model_name="gemma-4-26b",
                tags=["local", "vision"],
            )
        ]
    )
    monkeypatch.setattr("nexus.config_file.load", lambda: cfg)
    caps = capabilities_for_model_name("gemma-4-26b")
    assert "vision" in caps
    # Non-capability tags ("local") are filtered out.
    assert "local" not in caps


async def test_materialize_passes_image_through_vision_capable_model() -> None:
    _write_vault("uploads/cat.png", _PNG)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="what do you see?"),
            ContentPart(
                kind="image", vault_path="uploads/cat.png", mime_type="image/png"
            ),
        ],
    )
    out = await materialize_message(msg, {"chat", "tools", "vision"})
    assert isinstance(out.content, list)
    assert [p.kind for p in out.content] == ["text", "image"]


async def test_materialize_drops_image_for_non_vision_model() -> None:
    _write_vault("uploads/cat.png", _PNG)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="describe the image"),
            ContentPart(
                kind="image", vault_path="uploads/cat.png", mime_type="image/png"
            ),
        ],
    )
    out = await materialize_message(msg, {"chat", "tools"})
    assert isinstance(out.content, list)
    kinds = [p.kind for p in out.content]
    assert kinds == ["text", "text"]
    assert "does not support vision" in (out.content[1].text or "")


async def test_materialize_extracts_pdf_text_when_no_document_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_vault("uploads/notes.pdf", b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "nexus.multimodal.extract_text_from_document",
        lambda data, mime: "Hello from a PDF",
    )
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="summarize the doc"),
            ContentPart(
                kind="document",
                vault_path="uploads/notes.pdf",
                mime_type="application/pdf",
            ),
        ],
    )
    out = await materialize_message(msg, {"chat", "tools"})
    assert isinstance(out.content, list)
    assert [p.kind for p in out.content] == ["text", "text"]
    assert "Hello from a PDF" in (out.content[1].text or "")


async def test_materialize_passes_pdf_through_to_anthropic() -> None:
    _write_vault("uploads/notes.pdf", b"%PDF-1.4 fake")
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="summarize the doc"),
            ContentPart(
                kind="document",
                vault_path="uploads/notes.pdf",
                mime_type="application/pdf",
            ),
        ],
    )
    out = await materialize_message(
        msg, {"chat", "tools", "vision", "document"}
    )
    assert isinstance(out.content, list)
    assert [p.kind for p in out.content] == ["text", "document"]


def test_openai_encoder_emits_image_url_data_url() -> None:
    _write_vault("uploads/cat.png", _PNG)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="see this"),
            ContentPart(
                kind="image", vault_path="uploads/cat.png", mime_type="image/png"
            ),
        ],
    )
    encoded = _encode_openai(msg)
    assert encoded["role"] == "user"
    parts = encoded["content"]
    assert parts[0] == {"type": "text", "text": "see this"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_anthropic_encoder_emits_native_image_source() -> None:
    _write_vault("uploads/cat.png", _PNG)
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="see this"),
            ContentPart(
                kind="image", vault_path="uploads/cat.png", mime_type="image/png"
            ),
        ],
    )
    encoded = _encode_msg_anthropic(msg)
    assert encoded["role"] == "user"
    parts = encoded["content"]
    assert parts[0] == {"type": "text", "text": "see this"}
    assert parts[1]["type"] == "image"
    assert parts[1]["source"]["type"] == "base64"
    assert parts[1]["source"]["media_type"] == "image/png"
    assert isinstance(parts[1]["source"]["data"], str) and parts[1]["source"]["data"]


def test_anthropic_encoder_emits_native_document_source() -> None:
    _write_vault("uploads/notes.pdf", b"%PDF-1.4 fake")
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(
                kind="document",
                vault_path="uploads/notes.pdf",
                mime_type="application/pdf",
            ),
        ],
    )
    encoded = _encode_msg_anthropic(msg)
    parts = encoded["content"]
    assert parts[0]["type"] == "document"
    assert parts[0]["source"]["media_type"] == "application/pdf"


def test_legacy_string_content_still_encodes_unchanged() -> None:
    msg = ChatMessage(role=Role.USER, content="hello world")
    assert _encode_openai(msg)["content"] == "hello world"
    assert _encode_msg_anthropic(msg)["content"] == "hello world"
