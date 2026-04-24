"""Nexus server entry point."""

from __future__ import annotations

import logging

import uvicorn

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, PORT, SKILLS_DIR
from .config_file import load as load_config, apply_env_overlay
from .agent.llm import OpenAIProvider
from .agent.loop import Agent
from .agent.registry import build_registry
from .redact import install_redaction
from .server.app import create_app
from .skills.registry import SkillRegistry

log = logging.getLogger(__name__)


def build_app():
    # Plant the redacting log formatter before anything else runs — secrets in
    # config, URLs, or provider errors must be masked before they hit stderr.
    # Covers root + uvicorn loggers; idempotent on subsequent build_app calls.
    install_redaction(extra_loggers=("httpx", "fastapi"))

    cfg = apply_env_overlay(load_config())

    registry = SkillRegistry(SKILLS_DIR)
    provider_registry = build_registry(cfg)

    # Resolve the default provider for the Agent's constructor. Preference:
    #   1. The configured default_model, if it's registered.
    #   2. The first available model (any other chat model that registered OK).
    #   3. The legacy env-var OpenAIProvider.
    # Keep ``provider_registry`` intact in cases 1 and 2 so ``/routing``,
    # the Settings drawer, and auto-routing still see every model the user
    # configured — previously a stale ``default_model`` pointing at a
    # deleted/unregistered model silently nuked the entire registry.
    default_provider: OpenAIProvider | object
    try:
        default_provider, _ = provider_registry.get_for_model(cfg.agent.default_model)
    except KeyError:
        available = provider_registry.available_model_ids()
        if available:
            fallback_id = available[0]
            log.warning(
                "Default model %r not registered — using %r as the Agent's fallback provider. "
                "Fix by updating agent.default_model in ~/.nexus/config.toml or picking one in Settings.",
                cfg.agent.default_model, fallback_id,
            )
            default_provider, _ = provider_registry.get_for_model(fallback_id)
        else:
            log.warning(
                "Default model %r not in registry and no other models available — "
                "falling back to env-var provider.",
                cfg.agent.default_model,
            )
            default_provider = OpenAIProvider(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, model=LLM_MODEL)
            provider_registry = None

    agent = Agent(
        provider=default_provider,
        registry=registry,
        provider_registry=provider_registry,
        nexus_cfg=cfg,
    )

    graphrag_cfg = cfg.graphrag if cfg.graphrag.enabled else None

    return create_app(
        agent=agent,
        registry=registry,
        nexus_cfg=cfg,
        provider_registry=provider_registry,
        graphrag_cfg=graphrag_cfg,
    )


app = build_app()


def main() -> None:
    uvicorn.run("nexus.main:app", host="127.0.0.1", port=PORT, reload=False)


if __name__ == "__main__":
    main()
