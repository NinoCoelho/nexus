"""Nexus server entry point."""

from __future__ import annotations

import logging
from pathlib import Path

import uvicorn
from loom.home import AgentHome
from loom.permissions import AgentPermissions

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, PORT, SKILLS_DIR
from .config_file import load as load_config, apply_env_overlay
from .agent.llm import OpenAIProvider, StaticBearerAuth
from .agent.loop import Agent
from .agent.registry import build_registry
from .redact import install_redaction
from .server.app import create_app
from .skills.registry import SkillRegistry


_NEXUS_USER_DEFAULT = """\
<!-- Stable facts only: name, timezone, tone, recurring context. -->
<!-- Free-form notes about the user belong in vault/me.md, not here. -->

(empty — the agent will fill this in as it learns about you)
"""


def _build_agent_home() -> tuple[AgentHome, AgentPermissions]:
    """Initialize ~/.nexus/USER.md (Loom AgentHome) without touching the
    other Loom-managed paths Nexus already manages on its own (skills/,
    vault/, memory/). Defaults: only USER.md is writable by the agent.
    """
    home_path = Path("~/.nexus").expanduser()
    home_path.mkdir(parents=True, exist_ok=True)
    home = AgentHome(home_path, name="nexus")
    if not home.user_path.exists():
        home.write_user(_NEXUS_USER_DEFAULT)
    return home, AgentPermissions()

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
            # Find a fallback whose provider is actually functional
            # (has valid auth / is reachable).  Prefer the first available
            # that is not a broken key-based provider.
            fallback_id = available[0]
            for candidate in available:
                # The registry only registers providers that passed validation,
                # so any model in available_model_ids() should be functional.
                fallback_id = candidate
                break
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
            default_provider = OpenAIProvider(
                base_url=LLM_BASE_URL,
                auth=StaticBearerAuth(LLM_API_KEY),
                model=LLM_MODEL,
            )
            provider_registry = None

    home, permissions = _build_agent_home()

    agent = Agent(
        provider=default_provider,
        registry=registry,
        provider_registry=provider_registry,
        nexus_cfg=cfg,
        home=home,
        permissions=permissions,
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
