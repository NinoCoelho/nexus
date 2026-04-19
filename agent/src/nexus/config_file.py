"""Nexus TOML config file management.

Config stored plaintext at ~/.nexus/config.toml — no secret manager; keep this file private.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import tomllib
import tomli_w
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".nexus" / "config.toml"


class ModelStrengths(BaseModel):
    speed: int = 5
    cost: int = 5
    reasoning: int = 5
    coding: int = 5


class ModelEntry(BaseModel):
    id: str
    provider: str
    model_name: str
    tags: list[str] = Field(default_factory=list)
    strengths: ModelStrengths = Field(default_factory=ModelStrengths)


class ProviderConfig(BaseModel):
    base_url: str = ""
    api_key_env: str = ""
    use_inline_key: bool = False
    type: str = "openai_compat"  # "openai_compat" | "anthropic" | "ollama"


class AgentConfig(BaseModel):
    default_model: str = "openai/gpt-4o-mini"
    routing_mode: str = "fixed"
    max_iterations: int = 16


class NexusConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: list[ModelEntry] = Field(default_factory=list)


_DEFAULT_CONFIG = NexusConfig(
    agent=AgentConfig(
        default_model="openai/gpt-4o-mini",
        routing_mode="fixed",
        max_iterations=16,
    ),
    providers={
        "openai": ProviderConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            type="openai_compat",
        ),
        "anthropic": ProviderConfig(
            api_key_env="ANTHROPIC_API_KEY",
            type="anthropic",
        ),
        "ollama": ProviderConfig(
            base_url="http://localhost:11434",
            api_key_env="",
            type="ollama",
        ),
    },
    models=[
        ModelEntry(
            id="openai/gpt-4o-mini",
            provider="openai",
            model_name="gpt-4o-mini",
            tags=["fast", "cheap"],
            strengths=ModelStrengths(speed=9, cost=9, reasoning=5, coding=6),
        ),
        ModelEntry(
            id="anthropic/claude-sonnet-4-6",
            provider="anthropic",
            model_name="claude-sonnet-4-6",
            tags=["balanced"],
            strengths=ModelStrengths(speed=7, cost=6, reasoning=9, coding=9),
        ),
        ModelEntry(
            id="anthropic/claude-opus-4-7",
            provider="anthropic",
            model_name="claude-opus-4-7",
            tags=["deep"],
            strengths=ModelStrengths(speed=5, cost=4, reasoning=10, coding=9),
        ),
    ],
)


def default_config() -> NexusConfig:
    return _DEFAULT_CONFIG.model_copy(deep=True)


def _cfg_to_dict(cfg: NexusConfig) -> dict[str, Any]:
    d: dict[str, Any] = {
        "agent": cfg.agent.model_dump(),
        "providers": {
            k: {
                "base_url": v.base_url,
                "api_key_env": v.api_key_env,
                "use_inline_key": v.use_inline_key,
                "type": v.type,
            }
            for k, v in cfg.providers.items()
        },
        "models": [],
    }
    for m in cfg.models:
        md: dict[str, Any] = {
            "id": m.id,
            "provider": m.provider,
            "model_name": m.model_name,
            "tags": m.tags,
            "strengths": m.strengths.model_dump(),
        }
        d["models"].append(md)
    return d


def load() -> NexusConfig:
    if not CONFIG_PATH.exists():
        cfg = default_config()
        save(cfg)
        return cfg
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    return _parse(raw)


def save(cfg: NexusConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _cfg_to_dict(cfg)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(data, f)


def _parse(raw: dict[str, Any]) -> NexusConfig:
    agent = AgentConfig(**raw.get("agent", {}))
    providers: dict[str, ProviderConfig] = {}
    for name, pdata in raw.get("providers", {}).items():
        # Legacy configs won't have use_inline_key or type — defaults apply
        # Legacy name-based type detection: provider named "anthropic"/"ollama" defaults to that type
        if "type" not in pdata and name in ("anthropic", "ollama"):
            pdata = dict(pdata)
            pdata["type"] = name
        providers[name] = ProviderConfig(**pdata)
    models: list[ModelEntry] = []
    for mdata in raw.get("models", []):
        strengths = ModelStrengths(**mdata.pop("strengths", {}))
        models.append(ModelEntry(**mdata, strengths=strengths))
    return NexusConfig(agent=agent, providers=providers, models=models)


def apply_env_overlay(cfg: NexusConfig) -> NexusConfig:
    """If legacy NEXUS_LLM_* vars are set, synthesize ephemeral _env provider+model."""
    base_url = os.environ.get("NEXUS_LLM_BASE_URL", "")
    api_key = os.environ.get("NEXUS_LLM_API_KEY", "")
    model = os.environ.get("NEXUS_LLM_MODEL", "")
    if base_url and api_key and model:
        log.info("[config] NEXUS_LLM_* env overlay active — using _env provider with model %s", model)
        os.environ["_NEXUS_ENV_KEY"] = api_key
        cfg = cfg.model_copy(deep=True)
        cfg.providers["_env"] = ProviderConfig(base_url=base_url, api_key_env="_NEXUS_ENV_KEY")
        cfg.models.insert(
            0,
            ModelEntry(
                id="_env/default",
                provider="_env",
                model_name=model,
                tags=["env"],
                strengths=ModelStrengths(speed=5, cost=5, reasoning=5, coding=5),
            ),
        )
        cfg.agent.default_model = "_env/default"
    return cfg
