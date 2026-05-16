"""Nexus TOML config file management.

Config stored plaintext at ~/.nexus/config.toml — no secret manager; keep this file private.
Schema models live in config_schema.py and are re-exported here for backward compatibility.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import tomllib
import tomli_w

# Re-export all schema symbols so existing imports from config_file keep working.
from .config_schema import (  # noqa: F401
    Tier,
    AuthKind,
    ModelEntry,
    ProviderConfig,
    AgentConfig,
    GraphRAGEmbeddingConfig,
    GraphRAGExtractionConfig,
    GraphRAGOntologyConfig,
    GraphRAGConfig,
    SearchProviderEntry,
    SearchConfig,
    ScrapeConfig,
    RemoteTranscriptionConfig,
    TranscriptionConfig,
    TTSConfig,
    VaultHistoryConfig,
    VaultConfig,
    UIConfig,
    LocationConfig,
    DreamConfig,
    NexusAccountConfig,
    McpServerEntry,
    McpConfig,
    NexusConfig,
    default_config,
)

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".nexus" / "config.toml"


def _provider_to_dict(v: ProviderConfig) -> dict[str, Any]:
    """Serialize a ProviderConfig, omitting wizard fields at their defaults.

    Only emit ``catalog_id`` / ``runtime_kind`` / ``auth_kind`` / IAM /
    OAuth fields when they carry non-default values, so existing configs
    untouched by the wizard round-trip identically.
    """
    out: dict[str, Any] = {
        "base_url": v.base_url,
        "api_key_env": v.api_key_env,
        "use_inline_key": v.use_inline_key,
        "type": v.type,
    }
    if v.credential_ref:
        out["credential_ref"] = v.credential_ref
    if v.catalog_id:
        out["catalog_id"] = v.catalog_id
    if v.runtime_kind and v.runtime_kind != v.type:
        out["runtime_kind"] = v.runtime_kind
    if v.auth_kind != "api":
        out["auth_kind"] = v.auth_kind
    if v.oauth_token_ref:
        out["oauth_token_ref"] = v.oauth_token_ref
    if v.iam_profile:
        out["iam_profile"] = v.iam_profile
    if v.iam_region:
        out["iam_region"] = v.iam_region
    if v.iam_extra:
        out["iam_extra"] = dict(v.iam_extra)
    return out


def _cfg_to_dict(cfg: NexusConfig) -> dict[str, Any]:
    d: dict[str, Any] = {
        "agent": cfg.agent.model_dump(),
        "providers": {
            k: _provider_to_dict(v)
            for k, v in cfg.providers.items()
        },
        "models": [],
        "graphrag": {
            "enabled": cfg.graphrag.enabled,
            "embedding_model_id": cfg.graphrag.embedding_model_id,
            "extraction_model_id": cfg.graphrag.extraction_model_id,
            "max_hops": cfg.graphrag.max_hops,
            "context_budget": cfg.graphrag.context_budget,
            "top_k": cfg.graphrag.top_k,
            "chunk_size": cfg.graphrag.chunk_size,
            "embeddings": {
                "provider": cfg.graphrag.embeddings.provider,
                "model": cfg.graphrag.embeddings.model,
                "base_url": cfg.graphrag.embeddings.base_url,
                "key_env": cfg.graphrag.embeddings.key_env,
                "dimensions": cfg.graphrag.embeddings.dimensions,
            },
            "extraction": {
                "model": cfg.graphrag.extraction.model or "",
                "provider": cfg.graphrag.extraction.provider,
                "base_url": cfg.graphrag.extraction.base_url,
                "key_env": cfg.graphrag.extraction.key_env,
                "max_gleanings": cfg.graphrag.extraction.max_gleanings,
            },
            "ontology": {
                "entity_types": cfg.graphrag.ontology.entity_types,
                "core_relations": cfg.graphrag.ontology.core_relations,
                "allow_custom_relations": cfg.graphrag.ontology.allow_custom_relations,
            },
        },
        "search": {
            "enabled": cfg.search.enabled,
            "strategy": cfg.search.strategy,
            "providers": [
                {
                    "type": p.type,
                    "key_env": p.key_env,
                    "timeout": p.timeout,
                }
                for p in cfg.search.providers
            ],
        },
        "scrape": {
            "enabled": cfg.scrape.enabled,
            "mode": cfg.scrape.mode,
            "headless": cfg.scrape.headless,
            "timeout": cfg.scrape.timeout,
            "max_content_bytes": cfg.scrape.max_content_bytes,
        },
        "transcription": {
            "mode": cfg.transcription.mode,
            "model": cfg.transcription.model,
            "language": cfg.transcription.language or "",
            "device": cfg.transcription.device,
            "compute_type": cfg.transcription.compute_type,
            "remote": {
                "base_url": cfg.transcription.remote.base_url,
                "api_key_env": cfg.transcription.remote.api_key_env,
                "model": cfg.transcription.remote.model,
            },
        },
        "tts": {
            "enabled": cfg.tts.enabled,
            "ack_enabled": cfg.tts.ack_enabled,
            "ack_mode": cfg.tts.ack_mode,
            "voices_dir": cfg.tts.voices_dir,
        },
        "vault": {
            "history": {
                "enabled": cfg.vault.history.enabled,
            },
        },
        "ui": {
            "language": cfg.ui.language,
        },
        "nexus_account": {
            "base_url": cfg.nexus_account.base_url,
            "gateway_url": cfg.nexus_account.gateway_url,
            "poll_seconds": cfg.nexus_account.poll_seconds,
            "auto_upgrade_default": cfg.nexus_account.auto_upgrade_default,
        },
        "location": {
            "city": cfg.location.city,
            "region": cfg.location.region,
            "country": cfg.location.country,
            "timezone": cfg.location.timezone,
            "lat": cfg.location.lat,
            "lon": cfg.location.lon,
            "disabled": cfg.location.disabled,
        },
        "dream": cfg.dream.model_dump(),
        "mcp": {
            "servers": {
                name: {
                    "transport": entry.transport,
                    "command": entry.command,
                    "env": entry.env,
                    "url": entry.url,
                    "headers": entry.headers,
                    "enabled": entry.enabled,
                }
                for name, entry in cfg.mcp.servers.items()
            },
            "server_enabled": cfg.mcp.server_enabled,
            "server_port": cfg.mcp.server_port,
            "server_expose": cfg.mcp.server_expose,
            "server_auth_token": cfg.mcp.server_auth_token,
        },
    }
    for m in cfg.models:
        md: dict[str, Any] = {
            "id": m.id,
            "provider": m.provider,
            "model_name": m.model_name,
            "tags": m.tags,
            "tier": m.tier,
            "notes": m.notes,
        }
        if m.context_window:
            md["context_window"] = m.context_window
        if m.max_output_tokens:
            md["max_output_tokens"] = m.max_output_tokens
        if m.is_embedding_capable:
            md["is_embedding_capable"] = True
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


def _migrate_legacy_embedder(graphrag_raw: dict[str, Any]) -> None:
    """Auto-upgrade users pinned to the old English-only embedder.

    Why: the builtin embedder switched from all-MiniLM-L6-v2 (English-only)
    to paraphrase-multilingual-MiniLM-L12-v2 (multilingual, same 384 dim).
    Configs written by previous Nexus versions still carry the old model
    name as if it were a deliberate pin; rewriting it transparently keeps
    Portuguese vault content from being silently mis-embedded after upgrade.
    A user who explicitly pinned a different model is left untouched.
    """
    from .agent.builtin_embedder import BUILTIN_MODEL, LEGACY_MODELS

    emb = graphrag_raw.get("embeddings")
    if not isinstance(emb, dict):
        return
    current = emb.get("model", "")
    if current in LEGACY_MODELS:
        emb["model"] = BUILTIN_MODEL
        log.info(
            "[config] migrated graphrag.embeddings.model: %s -> %s",
            current, BUILTIN_MODEL,
        )


def _migrate_provider_in_memory(name: str, p: ProviderConfig) -> None:
    """Populate the wizard-era fields on a legacy ProviderConfig.

    Runs every time a config is parsed; idempotent — only fills in
    fields that are still at their default. No file write is triggered;
    the migrated values are persisted on the next user-driven save.

    Why: introducing ``runtime_kind`` / ``auth_kind`` / ``catalog_id`` on
    ProviderConfig means existing configs (which don't carry those keys)
    would otherwise show ``runtime_kind=""`` and ``auth_kind="api"`` — fine
    for OpenAI/Anthropic, but wrong for ollama (anonymous) and gives the
    wizard no way to recognize a row as an existing catalog provider.
    """
    if not p.runtime_kind:
        p.runtime_kind = p.type or "openai_compat"
    # Anonymous for Ollama; everything else defaults to api unless the
    # user has stored an OAuth bundle (oauth_token_ref) themselves.
    if p.auth_kind == "api" and p.runtime_kind == "ollama":
        p.auth_kind = "anonymous"
    if p.catalog_id is None:
        # Only adopt a catalog_id when the provider name matches a
        # known catalog entry — otherwise we'd label a custom provider
        # named e.g. "openai-test" as the official "openai" catalog row.
        try:
            from .providers import find as _find_catalog_entry

            if _find_catalog_entry(name) is not None:
                p.catalog_id = name
        except Exception:
            # Catalog load failure must not break config parsing.
            pass


def _tier_from_legacy_strengths(s: dict[str, Any]) -> Tier:
    reasoning = int(s.get("reasoning", 5) or 5)
    if reasoning <= 4:
        return "fast"
    if reasoning >= 8:
        return "heavy"
    return "balanced"


def _parse(raw: dict[str, Any]) -> NexusConfig:
    agent_raw = dict(raw.get("agent", {}))
    # Drop legacy fields that no longer exist on AgentConfig so older
    # config.toml files keep loading.
    agent_raw.pop("routing_mode", None)
    agent = AgentConfig(**agent_raw)
    providers: dict[str, ProviderConfig] = {}
    for name, pdata in raw.get("providers", {}).items():
        pdata = dict(pdata)
        if "type" not in pdata and name in ("anthropic", "ollama"):
            pdata["type"] = name
        provider = ProviderConfig(**pdata)
        _migrate_provider_in_memory(name, provider)
        providers[name] = provider
    models: list[ModelEntry] = []
    for mdata in raw.get("models", []):
        mdata = dict(mdata)
        legacy_strengths = mdata.pop("strengths", None)
        if "tier" not in mdata and isinstance(legacy_strengths, dict):
            mdata["tier"] = _tier_from_legacy_strengths(legacy_strengths)
        mdata.setdefault("notes", "")
        models.append(ModelEntry(**mdata))
    graphrag_raw = dict(raw.get("graphrag", {}))
    _migrate_legacy_embedder(graphrag_raw)
    graphrag = GraphRAGConfig(**graphrag_raw)
    search = SearchConfig(**raw.get("search", {}))
    scrape = ScrapeConfig(**raw.get("scrape", {}))
    t_raw = dict(raw.get("transcription", {}))
    if isinstance(t_raw.get("language"), str) and not t_raw["language"].strip():
        t_raw["language"] = None
    transcription = TranscriptionConfig(**t_raw)
    tts_raw = dict(raw.get("tts", {}))
    # Older configs had per-engine sub-blocks, an `engine` field, per-kind
    # ack toggles, threshold ints, voice/speed/language overrides and an
    # `ack_model` field. Drop them silently — TTSConfig is now intentionally
    # minimal (enabled + ack_enabled + voices_dir).
    legacy_piper = tts_raw.pop("piper", None)
    if isinstance(legacy_piper, dict) and "voices_dir" not in tts_raw:
        vd = legacy_piper.get("voices_dir")
        if isinstance(vd, str):
            tts_raw["voices_dir"] = vd
    for legacy_key in (
        "openai", "elevenlabs", "engine",
        "voice", "speed", "language", "auto_detect_language",
        "ack_start_enabled", "ack_progress_enabled", "ack_complete_enabled",
        "completion_ack_cross_session", "ack_model",
        "long_process_threshold_s", "long_process_repeat_s",
        "completion_ack_threshold_s",
    ):
        tts_raw.pop(legacy_key, None)
    tts = TTSConfig(**tts_raw)
    vault_raw = dict(raw.get("vault", {}))
    history_raw = dict(vault_raw.get("history", {}))
    vault = VaultConfig(history=VaultHistoryConfig(**history_raw))
    ui_raw = dict(raw.get("ui", {}))
    # Older configs without [ui] fall back to defaults; an unknown language
    # value (e.g. user typed "fr") is coerced to "en" rather than raising.
    if ui_raw.get("language") not in ("en", "pt-BR"):
        ui_raw.pop("language", None)
    ui = UIConfig(**ui_raw)
    nexus_account = NexusAccountConfig(**dict(raw.get("nexus_account", {})))
    location = LocationConfig(**dict(raw.get("location", {})))
    dream = DreamConfig(**dict(raw.get("dream", {})))
    mcp_raw = dict(raw.get("mcp", {}))
    mcp_servers: dict[str, McpServerEntry] = {}
    for sname, sdata in mcp_raw.get("servers", {}).items():
        sdata = dict(sdata)
        sdata.setdefault("enabled", True)
        mcp_servers[sname] = McpServerEntry(**sdata)
    mcp = McpConfig(servers=mcp_servers)
    return NexusConfig(
        agent=agent, providers=providers, models=models,
        graphrag=graphrag, search=search, scrape=scrape,
        transcription=transcription, tts=tts, vault=vault, ui=ui,
        nexus_account=nexus_account, location=location, dream=dream,
        mcp=mcp,
    )


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
                tier="balanced",
            ),
        )
        cfg.agent.default_model = "_env/default"
    return cfg
