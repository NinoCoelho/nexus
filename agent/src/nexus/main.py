"""Nexus server entry point."""

from __future__ import annotations

import logging

import uvicorn

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, PORT, SKILLS_DIR
from .config_file import load as load_config, apply_env_overlay
from .agent.llm import OpenAIProvider
from .agent.loop import Agent
from .agent.registry import build_registry
from .server.app import create_app
from .skills.registry import SkillRegistry

log = logging.getLogger(__name__)


def build_app():
    cfg = apply_env_overlay(load_config())

    registry = SkillRegistry(SKILLS_DIR)
    provider_registry = build_registry(cfg)

    # Back-compat: if no provider registry models available, fall back to env-var provider
    try:
        default_provider, default_model_name = provider_registry.get_for_model(cfg.agent.default_model)
    except KeyError:
        log.warning("Default model %r not in registry — falling back to env-var provider", cfg.agent.default_model)
        default_provider = OpenAIProvider(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, model=LLM_MODEL)
        default_model_name = None
        provider_registry = None

    agent = Agent(
        provider=default_provider,
        registry=registry,
        provider_registry=provider_registry,
        nexus_cfg=cfg,
    )
    return create_app(agent=agent, registry=registry, nexus_cfg=cfg, provider_registry=provider_registry)


app = build_app()


def main() -> None:
    uvicorn.run("nexus.main:app", host="127.0.0.1", port=PORT, reload=False)


if __name__ == "__main__":
    main()
