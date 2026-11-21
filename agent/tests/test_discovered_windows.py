"""Runtime context-window discovery (LM Studio native API → overflow budget).

Regression for the 2026-11-21 CFO starvation loop: a 256K nemotron had no
static/family match in ``known_context_window``, fell to the 32K fallback,
and after tools overhead + output headroom budgeted on ~10.4K usable tokens —
so mid-turn compaction hard_trimmed almost the whole history every iteration.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Self

import pytest

from nexus.agent import discovered_windows
from nexus.agent.loop import overflow


@pytest.fixture(autouse=True)
def _clean_cache():
    discovered_windows.reset()
    yield
    discovered_windows.reset()


# The exact shape LM Studio returns at /api/v0/models (observed 2026-11-21,
# host :1235). max_context_length is architectural; loaded_context_length is
# the window actually serving right now.
_LMSTUDIO_PAYLOAD = {
    "data": [
        {
            "id": "phonon-40m-multilingual",
            "object": "model",
            "type": "llm",
            "state": "loaded",
            "loaded_context_length": 2048,
        },
        {
            "id": "nvidia-nemotron-3-nano-30b-a3b-mlx",
            "object": "model",
            "type": "llm",
            "state": "loaded",
            "max_context_length": 262144,
            "loaded_context_length": 262144,
            "capabilities": ["tool_use"],
        },
        {
            "id": "deepseek-v4-pro-qwen3.5-9b-uncensored-heretic",
            "object": "model",
            "type": "vlm",
            "state": "not-loaded",
            "max_context_length": 262144,
            "capabilities": ["tool_use"],
        },
        {
            "id": "text-embedding-nomic-embed-text-v1.5",
            "object": "model",
            "type": "embeddings",
            "state": "not-loaded",
            "max_context_length": 2048,
        },
        "not-a-dict",
    ],
    "object": "list",
}


class _FakeResp:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """httpx.AsyncClient stand-in returning a canned payload."""

    last_url: str | None = None
    payload: Any = None
    error: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str) -> _FakeResp:
        type(self).last_url = url
        if type(self).error is not None:
            raise type(self).error
        return _FakeResp(type(self).payload)


def test_windows_from_payload_parses_lmstudio() -> None:
    windows = discovered_windows._windows_from_payload(_LMSTUDIO_PAYLOAD)
    # Loaded at 256K → the live serving window.
    assert windows["nvidia-nemotron-3-nano-30b-a3b-mlx"] == 262144
    # Not-loaded models fall back to their architectural max.
    assert windows["deepseek-v4-pro-qwen3.5-9b-uncensored-heretic"] == 262144
    # Loaded-only model (no max field) still counts.
    assert windows["phonon-40m-multilingual"] == 2048
    # Embeddings are irrelevant to chat budgeting; garbage entries skipped.
    assert "text-embedding-nomic-embed-text-v1.5" not in windows


def test_api_root_strips_v1() -> None:
    assert discovered_windows._api_root("http://localhost:1235/v1") == "http://localhost:1235"
    assert discovered_windows._api_root("http://localhost:1235/v1/") == "http://localhost:1235"
    assert discovered_windows._api_root("http://localhost:1235") == "http://localhost:1235"


async def test_refresh_populates_lookup(monkeypatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    _FakeClient.payload = _LMSTUDIO_PAYLOAD
    _FakeClient.error = None

    count = await discovered_windows.refresh("lmstudio", "http://localhost:1235/v1")
    assert count == 3
    assert _FakeClient.last_url == "http://localhost:1235/api/v0/models"

    # Both the bare upstream name and the Nexus registry id resolve.
    assert discovered_windows.lookup("nvidia-nemotron-3-nano-30b-a3b-mlx") == 262144
    assert discovered_windows.lookup("lmstudio/nvidia-nemotron-3-nano-30b-a3b-mlx") == 262144
    assert discovered_windows.lookup("totally-unknown-model") == 0
    assert discovered_windows.lookup("") == 0


async def test_refresh_failure_is_soft(monkeypatch) -> None:
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    _FakeClient.payload = None
    _FakeClient.error = RuntimeError("connection refused")

    assert await discovered_windows.refresh("lmstudio", "http://localhost:1235/v1") == 0
    # Cache survives (stale windows are better than none) and no exception
    # escaped — the periodic refresh task relies on this.
    _CACHE = discovered_windows._CACHE
    _CACHE["keepme"] = 12345
    assert await discovered_windows.refresh("lmstudio", "http://localhost:1235/v1") == 0
    assert _CACHE["keepme"] == 12345


def test_known_context_window_uses_discovered() -> None:
    # The nemotron has no static or family match: without discovery this was
    # 0 → DEFAULT_FALLBACK_WINDOW, the starvation budget.
    assert overflow.known_context_window("lmstudio/nvidia-nemotron-3-nano-30b-a3b-mlx") == 0
    discovered_windows._CACHE["nvidia-nemotron-3-nano-30b-a3b-mlx"] = 262144
    assert overflow.known_context_window("lmstudio/nvidia-nemotron-3-nano-30b-a3b-mlx") == 262144
    # effective_context_window still never returns 0 and picks the live value.
    assert (
        overflow.effective_context_window("lmstudio/nvidia-nemotron-3-nano-30b-a3b-mlx")
        == 262144
    )


def test_discovered_beats_family_but_not_static(monkeypatch) -> None:
    # Curated static entries keep priority over the live server.
    monkeypatch.setattr(overflow, "KNOWN_WINDOWS", {"fakemodel-x": 5000})
    discovered_windows._CACHE["fakemodel-x"] = 999_999
    assert overflow.known_context_window("lmstudio/fakemodel-x") == 5000

    # A family guess loses to a live value: "qwen-*" would classify as 128K
    # offline; the server says 256K.
    discovered_windows._CACHE["qwen-live-30b"] = 262_144
    assert overflow.known_context_window("lmstudio/qwen-live-30b") == 262_144

    # No discovery → family fallback still applies (offline behavior intact).
    discovered_windows._CACHE.pop("qwen-live-30b")
    assert overflow.known_context_window("lmstudio/qwen-live-30b") == 128_000


def test_discovery_targets_selects_lmstudio_only() -> None:
    from nexus.server.app_lifespan import _discovery_targets

    cfg = SimpleNamespace(
        providers={
            "lmstudio": SimpleNamespace(
                catalog_id="lmstudio", base_url="http://localhost:1235/v1",
            ),
            # lmstudio without a base_url — unusable, skipped.
            "lmstudio-broken": SimpleNamespace(catalog_id="lmstudio", base_url=""),
            # other local server — /api/v0/models would 404-spam; opt-out.
            "ollama": SimpleNamespace(
                catalog_id="ollama", base_url="http://localhost:11434",
            ),
            # wizard-era configs may leave catalog_id empty.
            "zai-coding-plan": SimpleNamespace(
                catalog_id="", base_url="https://api.example/v1",
            ),
        },
    )
    assert _discovery_targets(cfg) == [("lmstudio", "http://localhost:1235/v1")]
    assert _discovery_targets(SimpleNamespace(providers={})) == []
    assert _discovery_targets(SimpleNamespace(providers=None)) == []


async def test_startup_task_none_without_targets() -> None:
    from nexus.server.app_lifespan import _startup_context_window_discovery

    assert _startup_context_window_discovery(SimpleNamespace(providers={})) is None
