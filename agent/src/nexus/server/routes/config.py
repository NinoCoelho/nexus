"""Routes for config management: GET/PATCH /config."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends

from ..deps import get_agent, get_app_state

router = APIRouter()


def _redact_cfg(cfg: Any) -> dict[str, Any]:
    if cfg is None:
        return {}
    from ...secrets import get as secrets_get
    out: dict[str, Any] = {
        "agent": cfg.agent.model_dump(),
        "providers": {},
        "models": [m.model_dump() for m in cfg.models],
    }
    for name, p in cfg.providers.items():
        key_source: str | None = None
        if p.type == "ollama":
            key_source = "anonymous"
        elif p.use_inline_key and secrets_get(name):
            key_source = "inline"
        elif p.api_key_env and os.environ.get(p.api_key_env):
            key_source = "env"
        has_key = key_source is not None
        out["providers"][name] = {
            "base_url": p.base_url,
            "key_env": p.api_key_env,
            "has_key": has_key,
            "use_inline_key": p.use_inline_key,
            "type": p.type,
        }
    s = cfg.search
    out["search"] = {
        "enabled": s.enabled,
        "strategy": s.strategy,
        "providers": [
            {
                "type": p.type,
                "key_env": p.key_env,
                "timeout": p.timeout,
                # Synthesize a non-persisted "ready" flag so the UI can show
                # at-a-glance whether a provider will actually do anything.
                "ready": (p.type == "ddgs") or bool(p.key_env and os.environ.get(p.key_env)),
            }
            for p in s.providers
        ],
    }
    t = cfg.transcription
    out["transcription"] = {
        "mode": t.mode,
        "model": t.model,
        "language": t.language or "",
        "device": t.device,
        "compute_type": t.compute_type,
        "remote": {
            "base_url": t.remote.base_url,
            "api_key_env": t.remote.api_key_env,
            "model": t.remote.model,
        },
    }
    out["ui"] = {"language": cfg.ui.language}
    na = getattr(cfg, "nexus_account", None)
    if na is not None:
        out["nexus_account"] = {
            "base_url": na.base_url,
            "gateway_url": na.gateway_url,
            "poll_seconds": na.poll_seconds,
            "auto_upgrade_default": na.auto_upgrade_default,
        }
    tts = cfg.tts
    out["tts"] = {
        "enabled": tts.enabled,
        "ack_enabled": tts.ack_enabled,
        "ack_mode": tts.ack_mode,
        "ack_model": tts.ack_model,
        "voices_dir": tts.voices_dir,
    }
    mcp = getattr(cfg, "mcp", None)
    if mcp is not None:
        out["mcp"] = {
            "servers": {
                name: {
                    "transport": entry.transport,
                    "command": entry.command,
                    "env": entry.env,
                    "url": entry.url,
                    "headers": entry.headers,
                    "enabled": entry.enabled,
                }
                for name, entry in mcp.servers.items()
            },
            "server_enabled": mcp.server_enabled,
            "server_port": mcp.server_port,
            "server_expose": mcp.server_expose,
            "server_auth_token": "***" if mcp.server_auth_token else "",
        }
    return out


def _rebuild_registry(cfg: Any, app_state: dict[str, Any], agent: Any) -> None:
    from ...agent.registry import build_registry
    new_reg = build_registry(cfg)
    app_state["prov_reg"] = new_reg
    agent._provider_registry = new_reg
    agent._nexus_cfg = cfg
    app_state["cfg"] = cfg
    # Propagate the new registry into the loom adapter so the next turn
    # picks up fresh provider URLs (e.g. after a local model restart).
    _loom = getattr(agent, "_loom", None)
    if _loom is not None:
        _provider = getattr(_loom, "_provider", None)
        if _provider is not None and hasattr(_provider, "_registry"):
            _provider._registry = new_reg
        if _provider is not None and hasattr(_provider, "_default_model"):
            _provider._default_model = getattr(
                getattr(cfg, "agent", None), "default_model", None
            )


@router.get("/config")
async def get_config(app_state: dict[str, Any] = Depends(get_app_state)) -> dict[str, Any]:
    return _redact_cfg(app_state["cfg"])


@router.patch("/config")
async def patch_config(
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
) -> dict[str, Any]:
    from ...config_file import load as load_cfg, save as save_cfg, NexusConfig
    cfg = app_state["cfg"] or load_cfg()
    raw = cfg.model_dump()
    # Shallow merge for "agent"; NESTED merge for "providers" so a partial
    # edit (e.g. base_url only) doesn't wipe fields like `type` that the
    # client didn't send. "has_key" is a read-only synthesized flag and is
    # never persisted.
    if "agent" in body:
        raw["agent"].update(body["agent"])
    if "providers" in body:
        for pname, patch in body["providers"].items():
            existing = raw["providers"].get(pname, {})
            merged = {**existing, **{k: v for k, v in patch.items() if k != "has_key"}}
            raw["providers"][pname] = merged
    if "models" in body:
        raw["models"] = body["models"]
    if "search" in body:
        existing = raw.get("search", {}) or {}
        patch = body["search"] or {}
        merged = {**existing}
        if "enabled" in patch:
            merged["enabled"] = bool(patch["enabled"])
        if "strategy" in patch:
            merged["strategy"] = str(patch["strategy"])
        if "providers" in patch:
            merged["providers"] = [
                {
                    "type": str(p.get("type", "ddgs")),
                    "key_env": str(p.get("key_env", "")),
                    "timeout": float(p.get("timeout", 10.0)),
                }
                for p in (patch["providers"] or [])
            ]
        raw["search"] = merged
    if "transcription" in body:
        existing = raw.get("transcription", {}) or {}
        patch = body["transcription"] or {}
        merged = {**existing, **{k: v for k, v in patch.items() if k != "remote"}}
        if "remote" in patch:
            merged["remote"] = {
                **(existing.get("remote") or {}),
                **(patch["remote"] or {}),
            }
        if isinstance(merged.get("language"), str) and not merged["language"].strip():
            merged["language"] = None
        raw["transcription"] = merged
    if "ui" in body:
        existing = raw.get("ui", {}) or {}
        patch = body["ui"] or {}
        merged = {**existing}
        lang = patch.get("language")
        if lang in ("en", "pt-BR"):
            merged["language"] = lang
        raw["ui"] = merged
    if "tts" in body:
        existing = raw.get("tts", {}) or {}
        patch = body["tts"] or {}
        ALLOWED = {"enabled", "ack_enabled", "ack_mode", "ack_model", "voice_language", "voices_dir"}
        clean = {k: v for k, v in patch.items() if k in ALLOWED}
        raw["tts"] = {**existing, **clean}
    if "mcp" in body:
        existing = raw.get("mcp", {}) or {}
        patch = body["mcp"] or {}
        merged_mcp: dict[str, Any] = {**existing}
        if "servers" in patch:
            merged_servers = {**(existing.get("servers") or {})}
            for sname, sdata in (patch["servers"] or {}).items():
                if sdata is None:
                    merged_servers.pop(sname, None)
                else:
                    merged_servers[sname] = {
                        **(merged_servers.get(sname) or {}),
                        **sdata,
                    }
            merged_mcp["servers"] = merged_servers
        for scalar_key in ("server_enabled", "server_port", "server_auth_token"):
            if scalar_key in patch:
                merged_mcp[scalar_key] = patch[scalar_key]
        if "server_expose" in patch:
            merged_mcp["server_expose"] = patch["server_expose"]
        raw["mcp"] = merged_mcp
    new_cfg = NexusConfig(**raw)
    save_cfg(new_cfg)
    _rebuild_registry(new_cfg, app_state, a)
    return _redact_cfg(new_cfg)
