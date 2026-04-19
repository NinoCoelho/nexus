"""FastAPI application factory for Nexus."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from ..agent.llm import LLMTransportError, MalformedOutputError
from ..agent.loop import Agent
from ..skills.registry import SkillRegistry
from .schemas import ChatReply, ChatRequest, Health, SkillDetail, SkillInfo
from .session import SessionStore

log = logging.getLogger(__name__)


def create_app(
    *,
    agent: Agent,
    registry: SkillRegistry,
    sessions: SessionStore | None = None,
    nexus_cfg: Any | None = None,
    provider_registry: Any | None = None,
) -> FastAPI:
    sessions = sessions or SessionStore()
    _state = {"cfg": nexus_cfg, "prov_reg": provider_registry}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await agent.aclose()

    app = FastAPI(title="nexus", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost:\d+",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_agent() -> Agent:
        return agent

    def get_sessions() -> SessionStore:
        return sessions

    # ── existing routes ────────────────────────────────────────────────────────

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health()

    @app.get("/skills", response_model=list[SkillInfo])
    async def list_skills() -> list[SkillInfo]:
        return [
            SkillInfo(name=s.name, description=s.description, trust=s.trust)
            for s in registry.list()
        ]

    @app.get("/skills/{name}", response_model=SkillDetail)
    async def get_skill(name: str) -> SkillDetail:
        try:
            s = registry.get(name)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no such skill: {name!r}")
        return SkillDetail(name=s.name, description=s.description, trust=s.trust, body=s.body)

    @app.post("/chat", response_model=ChatReply)
    async def chat(
        req: ChatRequest,
        a: Agent = Depends(get_agent),
        store: SessionStore = Depends(get_sessions),
    ) -> ChatReply:
        session = store.get_or_create(req.session_id, context=req.context)
        try:
            turn = await a.run_turn(
                req.message,
                history=session.history,
                context=session.context,
            )
        except LLMTransportError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        except MalformedOutputError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        store.replace_history(session.id, turn.messages)
        return ChatReply(
            session_id=session.id,
            reply=turn.reply,
            trace=turn.trace,
            skills_touched=turn.skills_touched,
            iterations=turn.iterations,
        )

    @app.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def reset_session(
        session_id: str,
        store: SessionStore = Depends(get_sessions),
    ) -> None:
        store.reset(session_id)

    # ── config routes ──────────────────────────────────────────────────────────

    def _redact_cfg(cfg: Any) -> dict[str, Any]:
        if cfg is None:
            return {}
        import os
        from ..secrets import get as secrets_get
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
        return out

    def _rebuild_registry(cfg: Any) -> None:
        from ..agent.registry import build_registry
        new_reg = build_registry(cfg)
        _state["prov_reg"] = new_reg
        agent._provider_registry = new_reg
        agent._nexus_cfg = cfg
        _state["cfg"] = cfg

    @app.get("/config")
    async def get_config() -> dict[str, Any]:
        return _redact_cfg(_state["cfg"])

    @app.patch("/config")
    async def patch_config(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg, NexusConfig
        cfg = _state["cfg"] or load_cfg()
        raw = cfg.model_dump()
        # Deep merge top-level keys
        for key in ("agent", "providers"):
            if key in body:
                raw[key].update(body[key])
        if "models" in body:
            raw["models"] = body["models"]
        new_cfg = NexusConfig(**raw)
        save_cfg(new_cfg)
        _rebuild_registry(new_cfg)
        return _redact_cfg(new_cfg)

    @app.get("/providers")
    async def list_providers() -> list[dict[str, Any]]:
        import os
        from ..secrets import get as secrets_get
        cfg = _state["cfg"]
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

    @app.get("/providers/{name}/models")
    async def list_provider_models(name: str) -> dict[str, Any]:
        import os
        import httpx as _httpx
        from ..secrets import get as secrets_get

        cfg = _state["cfg"]
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

    @app.post("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
    async def set_provider_key(name: str, body: dict[str, Any]) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        from .. import secrets as _secrets
        api_key = body.get("api_key", "")
        if not api_key:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="api_key required")
        cfg = _state["cfg"] or load_cfg()
        if name not in cfg.providers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
        _secrets.set(name, api_key)
        cfg.providers[name].use_inline_key = True
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.delete("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
    async def clear_provider_key(name: str) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        from .. import secrets as _secrets
        cfg = _state["cfg"] or load_cfg()
        if name not in cfg.providers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"provider {name!r} not found")
        _secrets.delete(name)
        cfg.providers[name].use_inline_key = False
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.get("/models")
    async def list_models() -> list[dict[str, Any]]:
        cfg = _state["cfg"]
        if not cfg:
            return []
        return [m.model_dump() for m in cfg.models]

    @app.post("/models", status_code=status.HTTP_201_CREATED)
    async def add_model(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg, ModelEntry, ModelStrengths
        cfg = _state["cfg"] or load_cfg()
        strengths_data = body.pop("strengths", {})
        strengths = ModelStrengths(**strengths_data)
        m = ModelEntry(**body, strengths=strengths)
        cfg.models.append(m)
        save_cfg(cfg)
        _rebuild_registry(cfg)
        return m.model_dump()

    @app.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_model(model_id: str) -> None:
        from ..config_file import load as load_cfg, save as save_cfg
        cfg = _state["cfg"] or load_cfg()
        cfg.models = [m for m in cfg.models if m.id != model_id]
        save_cfg(cfg)
        _rebuild_registry(cfg)

    @app.get("/routing")
    async def get_routing() -> dict[str, Any]:
        cfg = _state["cfg"]
        pr = _state["prov_reg"]
        available = pr.available_model_ids() if pr else []
        if not cfg:
            return {"mode": "fixed", "default_model": None, "available_models": available}
        return {
            "mode": cfg.agent.routing_mode,
            "default_model": cfg.agent.default_model,
            "available_models": available,
        }

    @app.put("/routing")
    async def set_routing(body: dict[str, Any]) -> dict[str, Any]:
        from ..config_file import load as load_cfg, save as save_cfg
        cfg = _state["cfg"] or load_cfg()
        if "mode" in body:
            cfg.agent.routing_mode = body["mode"]
        if "default_model" in body:
            cfg.agent.default_model = body["default_model"]
        save_cfg(cfg)
        _rebuild_registry(cfg)
        return {"mode": cfg.agent.routing_mode, "default_model": cfg.agent.default_model}

    return app
