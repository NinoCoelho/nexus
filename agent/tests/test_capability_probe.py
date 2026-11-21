"""Capability-probe tests: lazy queue semantics, cache round-trip,
resolution precedence, and the runtime image-rejection fallback.

All config-touching tests run against a temp ``CONFIG_PATH`` — never the
user's real config (see test_model_resolution.py for the same pattern).
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from nexus import config_file as cf
from nexus.agent.llm import LLMProvider
from nexus.config_file import save as save_cfg
from nexus.config_schema import ModelEntry, ProviderConfig
from nexus.multimodal import image_rejected_by_upstream
from nexus.providers import probe as probe_mod
from nexus.providers.catalog import capabilities_for_model_name
from nexus.providers.probe import (
    get_probe_service,
    reset_probe_service,
)

# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def temp_config(monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cf.default_config()
    cfg.providers["local"] = ProviderConfig(
        base_url="http://127.0.0.1:18080/v1", runtime_kind="openai_compat"
    )
    cfg.models.append(
        ModelEntry(
            id="local/qwen", provider="local",
            model_name="Qwen2.5-VL-7B-Instruct",
        )
    )
    cfg.models.append(
        ModelEntry(
            id="local/tagged", provider="local", model_name="my-vl-model",
            tags=["vision"],
        )
    )
    save_cfg(cfg)
    return cfg


@pytest.fixture(autouse=True)
def _fresh_service():
    reset_probe_service()
    yield
    reset_probe_service()


def _patch_probe(monkeypatch, results: dict[str, dict | None], calls: list):
    """Stub the network call: ``results[model_id]`` per probe, recorded in
    ``calls``. None = undetermined."""

    async def fake_probe(model_id: str):
        calls.append(model_id)
        return results.get(model_id)

    monkeypatch.setattr(probe_mod, "probe_model", fake_probe)


async def _drain():
    """Let the detached drain task run to completion."""
    for _ in range(200):
        await asyncio.sleep(0)
        svc = get_probe_service()
        if not svc._draining and not svc._heap:
            return
    raise AssertionError("probe drain did not settle")


# ── family regex coverage ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "qwen2.5-vl-7b-instruct",
        "Qwen2-VL-7B-Instruct-GGUF",
        "qwen3-vl-plus",
        "qvq-72b-preview",
        "llava-1.5-7b",
        "pixtral-12b",
        "minicpm-v-2.6",
        "gemma-3-12b-it",
    ],
)
def test_vision_families_resolve(name):
    caps = capabilities_for_model_name(name)
    assert "vision" in caps, f"{name} should resolve vision-capable"


def test_non_vision_qwen_stays_text_only():
    # Qwen3 (dense text) must NOT gain vision from the *vl* family match.
    assert "vision" not in capabilities_for_model_name("qwen3-32b")


# ── resolution precedence ────────────────────────────────────────────────


def test_probed_tag_unions_into_capabilities(temp_config):
    cfg = cf.load()
    cfg.models[0].probed = {"vision": True}
    save_cfg(cfg)
    caps = capabilities_for_model_name("Qwen2.5-VL-7B-Instruct")
    assert "vision" in caps


def test_manual_tag_beats_probe_negative(temp_config):
    cfg = cf.load()
    cfg.models[1].probed = {"vision": False}
    save_cfg(cfg)
    # Manual tags=["vision"] must survive a probe that said no.
    assert "vision" in capabilities_for_model_name("my-vl-model")


# ── config round-trip (the [delegate]-wipe lesson) ───────────────────────


def test_probed_fields_survive_save_load(temp_config):
    cfg = cf.load()
    cfg.models[0].probed = {"vision": True}
    cfg.models[0].probed_at = "2026-01-01T00:00:00+00:00"
    save_cfg(cfg)
    reloaded = cf.load()
    entry = next(m for m in reloaded.models if m.id == "local/qwen")
    assert entry.probed == {"vision": True}
    assert entry.probed_at.startswith("2026-01-01")


def test_untagged_entry_drops_probed_keys(temp_config):
    """No probe cache → the key is omitted from config.toml entirely."""
    cfg = cf.load()
    assert all(not m.probed for m in cfg.models)


# ── error classification ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status,text,expected",
    [
        (200, "ok", False),
        (400, "model does not support images", True),
        (400, "mmproj not loaded", True),
        (401, "invalid api key", False),
        (400, "maximum context length exceeded", False),
        (500, "image blew up", False),  # 5xx is never a capability verdict
        (None, "transport error", False),
    ],
)
def test_image_rejection_classifier(status, text, expected):
    assert image_rejected_by_upstream(status, text) is expected


# ── service: lazy, sequential, dedup ─────────────────────────────────────


async def test_probe_runs_once_and_caches(temp_config, monkeypatch):
    calls: list[str] = []
    _patch_probe(monkeypatch, {"local/qwen": {"vision": True}}, calls)
    svc = get_probe_service()

    assert svc.enqueue_if_uncached("local/qwen") is True
    await _drain()
    assert calls == ["local/qwen"]

    # Second enqueue: cached decision → no network call.
    assert svc.enqueue_if_uncached("local/qwen") is False
    await _drain()
    assert calls == ["local/qwen"]

    entry = next(m for m in cf.load().models if m.id == "local/qwen")
    assert entry.probed == {"vision": True}
    assert entry.probed_at


async def test_manual_tagged_model_never_probed(temp_config, monkeypatch):
    calls: list[str] = []
    _patch_probe(monkeypatch, {}, calls)
    svc = get_probe_service()
    assert svc.enqueue_if_uncached("local/tagged") is False
    await _drain()
    assert calls == []


async def test_unknown_result_not_cached_but_backed_off(
    temp_config, monkeypatch,
):
    calls: list[str] = []
    _patch_probe(monkeypatch, {"local/qwen": None}, calls)
    svc = get_probe_service()

    for _ in range(3):
        svc.enqueue_if_uncached("local/qwen")
        await _drain()
    assert len(calls) == 3

    # Backoff: after 3 unknowns within the window, further enqueues no-op.
    assert svc.enqueue_if_uncached("local/qwen") is False
    assert len(calls) == 3
    # Cache untouched.
    entry = next(m for m in cf.load().models if m.id == "local/qwen")
    assert not entry.probed


async def test_negative_probe_is_cached(temp_config, monkeypatch):
    calls: list[str] = []
    _patch_probe(monkeypatch, {"local/qwen": {"vision": False}}, calls)
    svc = get_probe_service()
    svc.enqueue_if_uncached("local/qwen")
    await _drain()
    entry = next(m for m in cf.load().models if m.id == "local/qwen")
    assert entry.probed == {"vision": False}


async def test_media_priority_jumps_queue(temp_config, monkeypatch):
    calls: list[str] = []
    _patch_probe(monkeypatch, {"local/qwen": {"vision": True}}, calls)
    svc = get_probe_service()

    # Background probe for another model first, then a media-carrying one.
    cfg = cf.load()
    cfg.models.append(
        ModelEntry(
            id="local/other", provider="local", model_name="other-model"
        )
    )
    save_cfg(cfg)
    svc.enqueue_if_uncached("local/other")
    svc.enqueue_if_uncached("local/qwen", has_media=True)
    await _drain()
    # High priority (0) pops before normal (1).
    assert calls[0] == "local/qwen"


async def test_empty_model_id_resolves_default(temp_config, monkeypatch):
    calls: list[str] = []
    _patch_probe(monkeypatch, {"local/qwen": {"vision": True}}, calls)
    cfg = cf.load()
    cfg.agent.default_model = "local/qwen"
    save_cfg(cfg)
    assert get_probe_service().enqueue_if_uncached("") is True
    await _drain()
    assert calls == ["local/qwen"]


# ── routes: PATCH invalidation + re-test endpoint ─────────────────────────


class _NoopProvider(LLMProvider):
    async def chat(self, messages, *, tools=None, model=None, max_tokens=None):
        from nexus.agent.llm import ChatResponse, StopReason

        return ChatResponse(content="", stop_reason=StopReason.STOP)


@pytest_asyncio.fixture
async def api_client(temp_config, tmp_path, monkeypatch):
    """App client with config/secrets isolated to tmp_path (pattern from
    test_provider_wizard_route.py)."""
    import httpx

    from nexus import secrets as _s
    from nexus.agent.loop import Agent
    from nexus.server.app import create_app
    from nexus.server.session_store import SessionStore
    from nexus.server.settings import SettingsStore
    from nexus.skills.registry import SkillRegistry

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")
    monkeypatch.setattr(cf, "CONFIG_PATH", tmp_path / "config.toml")
    save_cfg(temp_config)

    skills = SkillRegistry(tmp_path / "skills")
    agent = Agent(provider=_NoopProvider(), registry=skills)
    app = create_app(
        agent=agent,
        registry=skills,
        sessions=SessionStore(db_path=tmp_path / "sessions.sqlite"),
        settings_store=SettingsStore(path=tmp_path / "settings.json"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_patch_model_name_change_clears_probe_cache(api_client):
    cfg = cf.load()
    cfg.models[0].probed = {"vision": False}
    cfg.models[0].probed_at = "2026-01-01T00:00:00+00:00"
    save_cfg(cfg)

    resp = await api_client.patch(
        "/models/local%2Fqwen",
        json={"model_name": "Qwen3-32B"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model_name"] == "Qwen3-32B"
    assert body["probed"] == {} and body["probed_at"] == ""


async def test_patch_other_fields_keep_probe_cache(api_client):
    cfg = cf.load()
    cfg.models[0].probed = {"vision": True}
    save_cfg(cfg)

    resp = await api_client.patch("/models/local%2Fqwen", json={"tier": "fast"})
    assert resp.status_code == 200
    assert resp.json()["probed"] == {"vision": True}


async def test_reprobe_clears_cache_and_enqueues(api_client, monkeypatch):
    calls: list[str] = []
    _patch_probe(monkeypatch, {"local/qwen": {"vision": True}}, calls)

    cfg = cf.load()
    cfg.models[0].probed = {"vision": False}
    cfg.models[0].probed_at = "2026-01-01T00:00:00+00:00"
    save_cfg(cfg)

    resp = await api_client.post("/models/local%2Fqwen/reprobe")
    assert resp.status_code == 200
    assert resp.json()["queued"] is True

    await _drain()
    entry = next(m for m in cf.load().models if m.id == "local/qwen")
    assert entry.probed == {"vision": True}


async def test_reprobe_bypasses_backoff(api_client):
    import time

    svc = get_probe_service()
    svc._unknown["local/qwen"] = [time.monotonic()] * 3  # backed off
    assert svc.enqueue_if_uncached("local/qwen") is False

    resp = await api_client.post("/models/local%2Fqwen/reprobe")
    assert resp.status_code == 200
    assert resp.json()["queued"] is True
    await _drain()
    # The 3 stale unknowns were dropped by clear_backoff; the fresh probe
    # attempt (real network fail against the fake endpoint) re-recorded
    # exactly one — backoff restarted from zero instead of staying maxed.
    assert len(svc._unknown.get("local/qwen", [])) == 1
