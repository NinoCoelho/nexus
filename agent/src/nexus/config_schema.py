"""Nexus config Pydantic schema models and default config.

Extracted from config_file.py to keep that module under 300 LOC.
All symbols are re-exported from config_file for backward compatibility.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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
