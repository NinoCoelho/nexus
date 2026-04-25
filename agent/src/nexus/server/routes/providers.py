"""Routes for provider management: /providers, /providers/{name}/models, /providers/{name}/key."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_agent, get_app_state
from .config import _rebuild_registry

router = APIRouter()


@router.get("/providers")
async def list_providers(app_state: dict[str, Any] = Depends(get_app_state)) -> list[dict[str, Any]]:
    from ...secrets import get as secrets_get
    cfg = app_state["cfg"]
    if not cfg:
        return []
    result = []
    for name, p in cfg.providers.items():
        key_source: str | None = None
        if p.type == "ollama":
            key_source = "anonymous"
        elif p.use_inline_key and secrets_get(name):
            key_source = "inline"
        elif p.api_key_env and os.environ.get(p.api_key_env):
            key_source = "env"
        result.append({
            "name": name,
            "base_url": p.base_url,
            "has_key": key_source is not None,
            "key_source": key_source,
            "key_env": p.api_key_env,
            "type": p.type,
        })
    return result


@router.get("/providers/{name}/models")
async def list_provider_models(
    name: str,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict[str, Any]:
    import httpx as _httpx
    from ...secrets import get as secrets_get

    cfg = app_state["cfg"]
    if not cfg or name not in cfg.providers:
        return {"models": [], "ok": False, "error": f"provider {name!r} not found"}

    p = cfg.providers[name]
    provider_type = p.type or ("anthropic" if name == "anthropic" else "openai_compat")

    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            if provider_type == "ollama":
                base = (p.base_url or "http://localhost:11434").rstrip("/")
                # Try /api/tags first (native Ollama endpoint)
                try:
                    r = await client.get(f"{base}/api/tags")
                    if r.status_code == 200:
                        data = r.json()
                        models = [m["name"] for m in data.get("models", [])]
                        return {"models": models, "ok": True, "error": None}
                    elif r.status_code == 404:
                        # Fall back to OpenAI-compat /v1/models
                        r2 = await client.get(f"{base}/v1/models")
                        if r2.status_code == 200:
                            data2 = r2.json()
                            models = [m["id"] for m in data2.get("data", [])]
                            return {"models": models, "ok": True, "error": None}
                        else:
                            return {"models": [], "ok": False, "error": f"HTTP {r2.status_code} from {base}/v1/models"}
                    else:
                        return {"models": [], "ok": False, "error": f"HTTP {r.status_code} from {base}/api/tags"}
                except _httpx.ConnectError as exc:
                    return {"models": [], "ok": False, "error": f"connection refused — is Ollama running? ({exc})"}

            elif provider_type == "anthropic":
                # Resolve key
                api_key = ""
                if p.use_inline_key:
                    api_key = secrets_get(name) or ""
                if not api_key and p.api_key_env:
                    api_key = os.environ.get(p.api_key_env, "")
                if not api_key:
                    return {"models": [], "ok": False, "error": "no API key configured for anthropic — set ANTHROPIC_API_KEY or use nexus providers set-key"}
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
                if r.status_code != 200:
                    return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                data = r.json()
                models = [m["id"] for m in data.get("data", [])]
                return {"models": models, "ok": True, "error": None}

            else:
                # openai_compat
                if not p.base_url:
                    return {"models": [], "ok": False, "error": "base_url not configured for this provider"}
                api_key = ""
                if p.use_inline_key:
                    api_key = secrets_get(name) or ""
                if not api_key and p.api_key_env:
                    api_key = os.environ.get(p.api_key_env, "")
                if not api_key:
                    return {"models": [], "ok": False, "error": f"no API key configured — set {p.api_key_env or 'an API key'} or use nexus providers set-key"}
                headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
                base = p.base_url.rstrip("/")
                r = await client.get(f"{base}/models", headers=headers)
                if r.status_code != 200:
                    return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                data = r.json()
                models = [m["id"] for m in data.get("data", [])]
                return {"models": models, "ok": True, "error": None}

    except _httpx.TimeoutException:
        return {"models": [], "ok": False, "error": "request timed out (5s)"}
    except Exception as exc:
        return {"models": [], "ok": False, "error": str(exc)}


@router.post("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
async def set_provider_key(
    name: str,
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
) -> None:
    from ...config_file import load as load_cfg, save as save_cfg
    from ... import secrets as _secrets
    api_key = body.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="api_key required")
    cfg = app_state["cfg"] or load_cfg()
    if name not in cfg.providers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
    _secrets.set(name, api_key)
    cfg.providers[name].use_inline_key = True
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)


@router.delete("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
async def clear_provider_key(
    name: str,
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
) -> None:
    from ...config_file import load as load_cfg, save as save_cfg
    from ... import secrets as _secrets
    cfg = app_state["cfg"] or load_cfg()
    if name not in cfg.providers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
    _secrets.delete(name)
    cfg.providers[name].use_inline_key = False
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)
