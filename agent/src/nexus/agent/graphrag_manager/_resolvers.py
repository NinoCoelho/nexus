"""Embedder and extraction-LLM resolver helpers for GraphRAG initialization."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class GraphRAGConfigError(RuntimeError):
    """Raised when a configured GraphRAG model can't be resolved.

    Surfaced to the UI via ``/graph/knowledge/health`` and 503 on reindex.
    Indexing must not silently fall back to a different model — the user
    must see the error and fix the config.
    """


def resolve_embedder(cfg: Any, graphrag_cfg: Any) -> Any:
    """Resolve the embedding provider.

    With a model selected via ``embedding_model_id`` we honor it strictly —
    no silent fallback to the builtin fastembed runner if registry lookup
    fails. Only when ``embedding_model_id`` is empty do we use the builtin.
    """
    from loom.store.embeddings import OllamaEmbeddingProvider, OpenAIEmbeddingProvider

    model_id = getattr(graphrag_cfg, "embedding_model_id", "")
    emb_cfg = graphrag_cfg.embeddings

    if model_id:
        try:
            from nexus.agent.registry import build_registry
            registry = build_registry(cfg)
            provider, upstream = registry.get_for_model(model_id)
        except Exception as exc:
            raise GraphRAGConfigError(
                f"embedding model {model_id!r} could not be resolved from the model "
                f"registry: {exc}. Check that the model is registered in config "
                f"and that its provider is reachable."
            ) from exc

        p_cfg = _get_provider_config(cfg, model_id)
        p_type = p_cfg.type if p_cfg else "openai_compat"
        dim = emb_cfg.dimensions

        if p_type == "ollama":
            return OllamaEmbeddingProvider(
                model=upstream or model_id,
                base_url=p_cfg.base_url if p_cfg else "http://localhost:11434",
                dim=dim,
            )
        # OpenAIEmbeddingProvider POSTs to ``{base_url}/embeddings``. Local
        # llama-server exposes the OpenAI-compat route at ``/v1/embeddings`` —
        # hitting plain ``/embeddings`` returns its native list-of-floats
        # response which the OpenAI parser then crashes on with "list indices
        # must be integers". Normalize by appending /v1 when the configured
        # base_url doesn't already include a versioned path.
        emb_base = (p_cfg.base_url if p_cfg else "").rstrip("/")
        if emb_base and not emb_base.endswith("/v1"):
            emb_base = f"{emb_base}/v1"
        return OpenAIEmbeddingProvider(
            model=upstream or model_id,
            base_url=emb_base,
            key_env=p_cfg.api_key_env if p_cfg else "",
            dim=dim,
        )

    return _builtin_embedder()


def _builtin_embedder() -> Any:
    from nexus.agent.builtin_embedder import get_builtin_embedder
    return get_builtin_embedder()


def _get_provider_config(cfg: Any, model_id: str) -> Any:
    """Look up the ProviderConfig for a model's provider."""
    for m in cfg.models:
        if m.id == model_id:
            return cfg.providers.get(m.provider)
    return None


def resolve_extraction_llm(cfg: Any, graphrag_cfg: Any) -> Any | None:
    extraction_model_id = getattr(graphrag_cfg, "extraction_model_id", "")
    extraction_model = extraction_model_id or getattr(graphrag_cfg.extraction, "model", "")
    if not extraction_model:
        log.info("[graphrag] no extraction model configured — using builtin extractor (spaCy + fastembed)")
        from nexus.agent.builtin_extractor import get_builtin_extractor
        return get_builtin_extractor()

    # First try: match against configured models/providers
    try:
        from nexus.agent.registry import build_registry
        registry = build_registry(cfg)
        provider, upstream_name = registry.get_for_model(extraction_model)
        from nexus.agent._loom_bridge import LoomProviderAdapter
        return LoomProviderAdapter(
            provider, provider_registry=registry, default_model=extraction_model
        )
    except Exception:
        log.info("[graphrag] extraction model %s not found in registry", extraction_model)

    # No fallback to Ollama — if the model isn't explicitly configured, skip extraction
    log.warning(
        "[graphrag] extraction model %r not resolvable — skipping entity extraction. "
        "Configure extraction_model_id under [graphrag] to enable it.",
        extraction_model,
    )
    return None
