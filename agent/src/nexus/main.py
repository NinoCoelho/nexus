"""Nexus server entry point."""

from __future__ import annotations

import atexit
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

_PORT_FILE = Path.home() / ".nexus" / "port"


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
        vision_id = (getattr(cfg, "agent", None) and cfg.agent.vision_model) or ""
        exclude = {vision_id} if vision_id else None
        available = provider_registry.available_model_ids(exclude=exclude)
        if available:
            # Prefer Nexus-tier models when present (nexus > demo), since
            # signed-in users always have one of those registered. Falls
            # through to the first BYO-model otherwise.
            preference = ("nexus", "demo")
            fallback_id = next(
                (mid for mid in preference if mid in available),
                available[0],
            )
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


def _write_port_file() -> None:
    _PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PORT_FILE.write_text(str(PORT))
    atexit.register(lambda: _PORT_FILE.unlink(missing_ok=True))


def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "dream":
        _run_dream()
        return
    _write_port_file()
    uvicorn.run("nexus.main:app", host="127.0.0.1", port=PORT, reload=False)


def _run_dream() -> None:
    import asyncio
    from .dream.engine import run_dream, close_store

    cfg = apply_env_overlay(load_config())
    dream_cfg = getattr(cfg, "dream", None)
    if dream_cfg is None or not dream_cfg.enabled:
        print("Dreaming is disabled. Enable it in ~/.nexus/config.toml with [dream] enabled = true")
        return

    provider, upstream_model = _resolve_dream_provider(cfg)

    result = asyncio.run(run_dream(provider=provider, model_id=upstream_model, cfg=cfg))
    if result.error:
        print(f"Dream run #{result.run_id} failed: {result.error}")
    else:
        c = result.consolidation
        print(f"Dream run #{result.run_id} completed (depth={result.depth}, duration={result.duration_ms / 1000:.1f}s)")
        if c:
            print(f"  Consolidation: {c.merges} merges, {c.updates} updates, {c.deletes} deletes")
    close_store()


def _resolve_dream_provider(cfg):
    from .agent.registry import build_registry
    from .agent.llm import OpenAIProvider, StaticBearerAuth
    from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

    registry = build_registry(cfg)
    try:
        provider, upstream = registry.get_for_model(cfg.agent.default_model)
        return provider, upstream
    except KeyError:
        available = registry.available_model_ids()
        if available:
            provider, upstream = registry.get_for_model(available[0])
            return provider, upstream
    return OpenAIProvider(
        base_url=LLM_BASE_URL,
        auth=StaticBearerAuth(LLM_API_KEY),
        model=LLM_MODEL,
    ), None


if __name__ == "__main__":
    main()
