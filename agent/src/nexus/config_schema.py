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
    # Total context window in tokens (input + output). Used for pre-flight
    # overflow detection so the agent can refuse a turn whose history won't
    # fit instead of letting the upstream return an empty 200. For local
    # GGUF models also passed to llama-server as ``--ctx-size`` at start
    # (Stop/Start to apply changes). 0 = unknown / use server default.
    context_window: int = 0
    # Per-model output cap forwarded as ``max_tokens`` on the LLM request.
    # 0 = fall through to ``AgentConfig.default_max_output_tokens``.
    max_output_tokens: int = 0
    # User-asserted: this model serves embeddings (e.g. all-MiniLM, bge).
    # Gates which models the UI offers for the embedding role; chat-only
    # models (gpt-4o, glm-4.7, etc.) should leave this false.
    is_embedding_capable: bool = False


class ProviderConfig(BaseModel):
    base_url: str = ""
    api_key_env: str = ""
    use_inline_key: bool = False
    # Name of an entry in the credential store (~/.nexus/secrets.toml) to use
    # for this provider's API key. When set, takes precedence over the legacy
    # ``use_inline_key`` and ``api_key_env`` paths — the resolver consults
    # ``secrets.resolve(credential_ref)`` (env-first, store fallback). Older
    # configs without this field keep working unchanged.
    credential_ref: str | None = None
    type: str = "openai_compat"  # "openai_compat" | "anthropic" | "ollama"


class AgentConfig(BaseModel):
    default_model: str = ""
    last_used_model: str = ""
    max_iterations: int = 16
    # Sampling temperature. 0.0 keeps tool-calling deterministic — bumping it
    # introduces creativity at the cost of brittle JSON args, so most users
    # should leave it at 0.
    temperature: float = 0.0
    # OpenAI-compat sampling. Frequency penalty mitigates token-degeneracy
    # loops (e.g. deepseek-coder spitting "@@@@@…" with temp=0). Sent only
    # when non-zero so strict gateways aren't tripped by no-op fields.
    frequency_penalty: float = 0.3
    presence_penalty: float = 0.0
    # Streaming runaway-repetition guard. If the tail of the assistant's
    # generation is a single short pattern (≤8 chars) repeated for at least
    # this many characters, abort the stream with finish_reason=stop. Set 0
    # to disable.
    anti_repeat_threshold: int = 200
    # Global per-call output cap forwarded as ``max_tokens`` to the LLM.
    # 0 = unset (OpenAI-compat omits the field; Anthropic falls back to its
    # legacy 4096 since its API requires the field). Per-model overrides on
    # ``ModelEntry.max_output_tokens`` win when > 0.
    default_max_output_tokens: int = 0


class GraphRAGEmbeddingConfig(BaseModel):
    provider: str = "builtin"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
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
    # Source of truth for the ontology lives at
    # ``~/.nexus/vault/_system/ontology/`` (entity_types.csv,
    # relations.csv, INSTRUCTIONS.md, meta.json) and is editable by the
    # user and the agent (via the ``ontology_manage`` tool). The fields
    # below are kept ONLY as the seed used the first time the vault
    # ontology folder is created. After seeding, ``OntologyStore`` is
    # the authoritative reader during ``graphrag_manager.initialize``.
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
    # Default to on: the .app bundle ships fastembed + spaCy pre-cached and
    # `uv sync` pulls them in for dev, so the builtin embedder + extractor
    # work offline with zero config. Keeping this False meant fresh installs
    # showed "GraphRAG not ready" on first launch even though everything
    # needed was already on disk.
    enabled: bool = True
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


class VaultHistoryConfig(BaseModel):
    # When enabled, every vault mutation (write/delete/move) produces a git
    # commit in a separate work-tree at ~/.nexus/.vault-history. Disabled by
    # default — most users won't need it. See vault_history.py.
    enabled: bool = False


class VaultConfig(BaseModel):
    history: VaultHistoryConfig = Field(default_factory=VaultHistoryConfig)


class NexusConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    models: list[ModelEntry] = Field(default_factory=list)
    graphrag: GraphRAGConfig = Field(default_factory=GraphRAGConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    scrape: ScrapeConfig = Field(default_factory=ScrapeConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    vault: VaultConfig = Field(default_factory=VaultConfig)


# Fresh install starts with providers configured but NO models.
# Users discover and add models through the UI (or CLI).
_DEFAULT_CONFIG = NexusConfig(
    agent=AgentConfig(
        default_model="",
        last_used_model="",
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
