"""Nexus TOML config file management.

Config stored plaintext at ~/.nexus/config.toml — no secret manager; keep this file private.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

import tomllib
import tomli_w
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".nexus" / "config.toml"

Tier = Literal["fast", "balanced", "heavy"]


class ModelEntry(BaseModel):
    id: str
    provider: str
    model_name: str
    tags: list[str] = Field(default_factory=list)
    tier: Tier = "balanced"
    notes: str = ""


class ProviderConfig(BaseModel):
    base_url: str = ""
    api_key_env: str = ""
    use_inline_key: bool = False
    type: str = "openai_compat"  # "openai_compat" | "anthropic" | "ollama"


class AgentConfig(BaseModel):
    default_model: str = ""
    last_used_model: str = ""
    routing_mode: Literal["fixed", "auto"] = "fixed"
    max_iterations: int = 16


class GraphRAGEmbeddingConfig(BaseModel):
    provider: str = "builtin"
    model: str = "BAAI/bge-small-en-v1.5"
    base_url: str = ""
    key_env: str = ""
    dimensions: int = 384


class GraphRAGExtractionConfig(BaseModel):
    model: str | None = None
    provider: str = "ollama"
    base_url: str = "http://localhost:11434/v1"
    key_env: str = ""
    max_gleanings: int = 1


class GraphRAGOntologyConfig(BaseModel):
    entity_types: list[str] = Field(default_factory=lambda: [
        "person", "organization", "project", "note", "concept",
        "technology", "decision", "event", "source", "task",
    ])
    core_relations: list[str] = Field(default_factory=lambda: [
        "mentions", "about", "uses", "depends_on", "part_of",
        "belongs_to", "authored_by", "cites", "derived_from",
        "supports", "contradicts", "decided_in", "related_to",
    ])
    allow_custom_relations: bool = True


class GraphRAGConfig(BaseModel):
    enabled: bool = False
    embedding_model_id: str = ""
    extraction_model_id: str = ""
    embeddings: GraphRAGEmbeddingConfig = Field(default_factory=GraphRAGEmbeddingConfig)
    extraction: GraphRAGExtractionConfig = Field(default_factory=GraphRAGExtractionConfig)
    ontology: GraphRAGOntologyConfig = Field(default_factory=GraphRAGOntologyConfig)
    max_hops: int = 2
    context_budget: int = 3000
    top_k: int = 10
    chunk_size: int = 1000


class SearchProviderEntry(BaseModel):
    type: str = "ddgs"
    key_env: str = ""
    timeout: float = 10.0


class SearchConfig(BaseModel):
    enabled: bool = True
    strategy: str = "concurrent"
    providers: list[SearchProviderEntry] = Field(
        default_factory=lambda: [SearchProviderEntry()]
    )


class ScrapeConfig(BaseModel):
    enabled: bool = True
    mode: str = "auto"
    headless: bool = True
    timeout: int = 30
    max_content_bytes: int = 102400


class RemoteTranscriptionConfig(BaseModel):
    base_url: str = ""
    api_key_env: str = ""
    model: str = "whisper-1"


class TranscriptionConfig(BaseModel):
    mode: Literal["local", "remote"] = "local"
    model: str = "base"  # faster-whisper size: tiny/base/small/medium/large-v3
    language: str | None = None
    device: Literal["cpu", "cuda", "auto"] = "auto"
    compute_type: str = "int8"
    remote: RemoteTranscriptionConfig = Field(default_factory=RemoteTranscriptionConfig)


class NexusConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: list[ModelEntry] = Field(default_factory=list)
    graphrag: GraphRAGConfig = Field(default_factory=GraphRAGConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    scrape: ScrapeConfig = Field(default_factory=ScrapeConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)


# Fresh install starts with providers configured but NO models.
# Users discover and add models through the UI (or CLI).
_DEFAULT_CONFIG = NexusConfig(
    agent=AgentConfig(
        default_model="",
        last_used_model="",
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
    models=[],
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
                "model": cfg.graphrag.extraction.model,
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


def _tier_from_legacy_strengths(s: dict[str, Any]) -> Tier:
    reasoning = int(s.get("reasoning", 5) or 5)
    if reasoning <= 4:
        return "fast"
    if reasoning >= 8:
        return "heavy"
    return "balanced"


def _parse(raw: dict[str, Any]) -> NexusConfig:
    agent = AgentConfig(**raw.get("agent", {}))
    providers: dict[str, ProviderConfig] = {}
    for name, pdata in raw.get("providers", {}).items():
        if "type" not in pdata and name in ("anthropic", "ollama"):
            pdata = dict(pdata)
            pdata["type"] = name
        providers[name] = ProviderConfig(**pdata)
    models: list[ModelEntry] = []
    for mdata in raw.get("models", []):
        mdata = dict(mdata)
        legacy_strengths = mdata.pop("strengths", None)
        if "tier" not in mdata and isinstance(legacy_strengths, dict):
            mdata["tier"] = _tier_from_legacy_strengths(legacy_strengths)
        mdata.setdefault("notes", "")
        models.append(ModelEntry(**mdata))
    graphrag = GraphRAGConfig(**raw.get("graphrag", {}))
    search = SearchConfig(**raw.get("search", {}))
    scrape = ScrapeConfig(**raw.get("scrape", {}))
    t_raw = dict(raw.get("transcription", {}))
    if isinstance(t_raw.get("language"), str) and not t_raw["language"].strip():
        t_raw["language"] = None
    transcription = TranscriptionConfig(**t_raw)
    return NexusConfig(
        agent=agent, providers=providers, models=models,
        graphrag=graphrag, search=search, scrape=scrape,
        transcription=transcription,
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
