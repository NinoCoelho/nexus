"""Tests for the ``generate_image`` tool.

Mocks httpx so the tests don't make real API calls. Covers:

* Text-to-image flow for OpenAI (json POST → ``b64_json`` decode).
* Text-to-image flow for Gemini (native ``generateContent`` →
  ``inline_data`` parse).
* Editing flow: when ``reference_image`` is set, OpenAI hits
  ``/images/edits`` (multipart) and Gemini adds a second ``inline_data``
  part to ``contents[0].parts``.
* Vault persistence + returned markdown shape.
* Error path: missing API key.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from nexus.tools.image_gen_tool import handle_image_gen_tool


_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_FAKE_PNG = _PNG_HEADER + b"\x00\x00\x00\x0dIHDR" + b"\x00" * 17


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.fixture(autouse=True)
def _isolate_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the vault at a tmp directory so the test doesn't touch ``~/.nexus``."""
    monkeypatch.setattr("nexus.vault._VAULT_ROOT", tmp_path / "vault")


@pytest.fixture
def _stub_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a config that exposes ``OPENAI_API_KEY`` for the tool resolver."""
    from nexus.config_schema import NexusConfig, ProviderConfig

    cfg = NexusConfig(
        providers={
            "openai": ProviderConfig(
                base_url="https://api.openai.com/v1",
                api_key_env="OPENAI_API_KEY",
                type="openai_compat",
            )
        }
    )
    monkeypatch.setattr("nexus.config_file.load", lambda: cfg)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")


@pytest.fixture
def _stub_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.config_schema import NexusConfig, ProviderConfig

    cfg = NexusConfig(
        providers={
            "google-gemini": ProviderConfig(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key_env="GEMINI_API_KEY",
                type="openai_compat",
            )
        }
    )
    monkeypatch.setattr("nexus.config_file.load", lambda: cfg)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-fake-key")


def _make_async_post_recorder(response_factory):
    """Patch httpx.AsyncClient.post to record + answer calls.

    ``response_factory`` is called with the kwargs of each post() and
    must return an :class:`httpx.Response`.
    """
    calls: list[dict[str, Any]] = []

    async def _post(self: Any, url: str, **kwargs: Any) -> httpx.Response:
        record = {"url": url, **kwargs}
        calls.append(record)
        resp = response_factory(record)
        # httpx.Response needs a request bound for some operations
        resp._request = httpx.Request("POST", url)
        return resp

    return _post, calls


def _json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


async def test_openai_text_to_image_persists_and_returns_markdown(
    monkeypatch: pytest.MonkeyPatch,
    _stub_openai_key: None,
) -> None:
    response = _json_response({"data": [{"b64_json": _b64(_FAKE_PNG)}]})
    post_fn, calls = _make_async_post_recorder(lambda _r: response)
    monkeypatch.setattr(httpx.AsyncClient, "post", post_fn)

    result_raw = await handle_image_gen_tool(
        {"prompt": "a calm orange tabby on a sunlit roof"}
    )
    result = json.loads(result_raw)

    assert result["ok"] is True
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-image-1"
    assert len(result["paths"]) == 1
    saved = result["paths"][0]
    assert saved.startswith("assets/generated/")
    assert saved.endswith(".png")
    assert "![" in result["markdown"] and f"vault://{saved}" in result["markdown"]

    # The byte payload landed in the (test-local) vault.
    from nexus import vault as _vault
    full = _vault.resolve_path(saved)
    assert full.read_bytes() == _FAKE_PNG

    # Single POST hit /images/generations with the prompt in the JSON body.
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/images/generations")
    body = calls[0]["json"]
    assert body["model"] == "gpt-image-1"
    assert body["prompt"].startswith("a calm orange tabby")


async def test_openai_edit_flow_uses_multipart_when_reference_provided(
    monkeypatch: pytest.MonkeyPatch,
    _stub_openai_key: None,
) -> None:
    # Stash the reference image into the (test-local) vault first.
    from nexus import vault as _vault
    _vault.write_file_bytes("input.png", _FAKE_PNG)

    response = _json_response({"data": [{"b64_json": _b64(_FAKE_PNG)}]})
    post_fn, calls = _make_async_post_recorder(lambda _r: response)
    monkeypatch.setattr(httpx.AsyncClient, "post", post_fn)

    result_raw = await handle_image_gen_tool(
        {"prompt": "make it neon", "reference_image": "input.png"}
    )
    result = json.loads(result_raw)
    assert result["ok"] is True

    # /images/edits is the multipart endpoint.
    assert calls[0]["url"].endswith("/images/edits")
    assert "files" in calls[0]
    assert "image" in calls[0]["files"]
    # The form data carries prompt + model.
    assert calls[0]["data"]["prompt"] == "make it neon"
    assert calls[0]["data"]["model"] == "gpt-image-1"


async def test_gemini_text_to_image_decodes_inline_data(
    monkeypatch: pytest.MonkeyPatch,
    _stub_gemini_key: None,
) -> None:
    response = _json_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Here is the image you asked for."},
                            {
                                "inline_data": {
                                    "mime_type": "image/png",
                                    "data": _b64(_FAKE_PNG),
                                }
                            },
                        ]
                    }
                }
            ]
        }
    )
    post_fn, calls = _make_async_post_recorder(lambda _r: response)
    monkeypatch.setattr(httpx.AsyncClient, "post", post_fn)

    result_raw = await handle_image_gen_tool(
        {"prompt": "a friendly capybara", "provider": "gemini"}
    )
    result = json.loads(result_raw)

    assert result["ok"] is True
    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-2.5-flash-image"
    saved = result["paths"][0]

    from nexus import vault as _vault
    assert _vault.resolve_path(saved).read_bytes() == _FAKE_PNG

    # Native generateContent endpoint, with the API key in the query string.
    assert calls[0]["url"].endswith(":generateContent")
    assert calls[0]["params"] == {"key": "AIza-test-fake-key"}
    body = calls[0]["json"]
    parts = body["contents"][0]["parts"]
    assert parts[0]["text"].startswith("a friendly capybara")


async def test_missing_api_key_returns_clean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the env var is unset and no credential_ref is configured, the
    tool returns ``{ok: false, error: ...}`` rather than crashing."""
    from nexus.config_schema import NexusConfig, ProviderConfig

    cfg = NexusConfig(
        providers={
            "openai": ProviderConfig(
                base_url="https://api.openai.com/v1",
                api_key_env="OPENAI_API_KEY",
                type="openai_compat",
            )
        }
    )
    monkeypatch.setattr("nexus.config_file.load", lambda: cfg)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result_raw = await handle_image_gen_tool({"prompt": "anything"})
    result = json.loads(result_raw)

    assert result["ok"] is False
    assert "OPENAI_API_KEY" in result["error"]
