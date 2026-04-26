"""Provider registry — instantiates and holds LLM providers from config."""

from __future__ import annotations

import logging
import os

from .llm import AnthropicProvider, LLMProvider, OpenAIProvider
from ..config_file import NexusConfig
from .. import secrets as _secrets

log = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._model_map: dict[str, tuple[str, str]] = {}  # model_id -> (provider_name, model_name)

    def register_provider(self, name: str, provider: LLMProvider) -> None:
        self._providers[name] = provider

    def register_model(self, model_id: str, provider_name: str, model_name: str) -> None:
        self._model_map[model_id] = (provider_name, model_name)

    def get_for_model(self, model_id: str) -> tuple[LLMProvider, str]:
        if model_id not in self._model_map:
            raise KeyError(f"Unknown model id: {model_id!r}")
        provider_name, model_name = self._model_map[model_id]
        if provider_name not in self._providers:
            raise KeyError(f"Provider {provider_name!r} not available")
        return self._providers[provider_name], model_name

    def available_model_ids(self) -> list[str]:
        return [
            mid for mid, (pname, _) in self._model_map.items()
            if pname in self._providers
        ]

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()


def build_registry(cfg: NexusConfig) -> ProviderRegistry:
    reg = ProviderRegistry()

    # Sampling/anti-repeat knobs apply to every chat-path OpenAI-compat
    # provider — local models (deepseek-coder, llama-server) are the ones
    # that fall into token-degeneracy loops with temp=0.
    sampling_kwargs = {
        "frequency_penalty": cfg.agent.frequency_penalty,
        "presence_penalty": cfg.agent.presence_penalty,
        "anti_repeat_threshold": cfg.agent.anti_repeat_threshold,
    }

    for name, pcfg in cfg.providers.items():
        provider_type = getattr(pcfg, "type", None) or ("anthropic" if name == "anthropic" else "openai_compat")

        # Ollama is anonymous — register without any key
        if provider_type == "ollama":
            base = (pcfg.base_url or "http://localhost:11434").rstrip("/")
            provider = OpenAIProvider(
                base_url=f"{base}/v1", api_key="ollama", model="", **sampling_kwargs
            )
            reg.register_provider(name, provider)
            log.info("[provider] %s initialized (anonymous)", name)
            continue

        api_key = ""

        if pcfg.use_inline_key:
            api_key = _secrets.get(name) or ""
            if api_key:
                log.info("[provider] %s: key loaded (inline)", name)
            else:
                log.warning("[provider] %s: use_inline_key=True but no key in secrets — skipping", name)
                continue
        elif pcfg.api_key_env:
            api_key = os.environ.get(pcfg.api_key_env, "")
            if api_key:
                log.info("[provider] %s: key loaded (env: %s)", name, pcfg.api_key_env)
            else:
                log.warning("[provider] %s: env var %s not set — skipping", name, pcfg.api_key_env)
                continue
        elif provider_type == "openai_compat" and pcfg.base_url:
            # Local OpenAI-compatible servers (llama-server, vllm, lm-studio)
            # commonly don't require auth. Register anonymously so embedding
            # / extraction models served locally are usable.
            log.info("[provider] %s initialized (openai_compat, anonymous)", name)
        else:
            # No key configured — only valid for providers that don't need auth
            log.warning("[provider] %s: not configured — skipping", name)
            continue

        if provider_type == "anthropic":
            provider = AnthropicProvider(api_key=api_key, model="")
        elif pcfg.base_url:
            provider = OpenAIProvider(
                base_url=pcfg.base_url, api_key=api_key, model="", **sampling_kwargs
            )
        else:
            log.warning("[provider] %s: openai_compat requires base_url — skipping", name)
            continue

        reg.register_provider(name, provider)
        log.info("[provider] %s initialized", name)

    for model in cfg.models:
        if model.provider in {name for name, _ in _provider_names(reg)}:
            reg.register_model(model.id, model.provider, model.model_name)

    return reg


def _provider_names(reg: ProviderRegistry) -> list[tuple[str, LLMProvider]]:
    # Access internal dict for registration check
    return list(reg._providers.items())
