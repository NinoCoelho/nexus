"""In-process embedder backed by ``fastembed``.

Ships as the default embedding + routing-classifier backend so Nexus works
out of the box without Ollama or a remote embeddings endpoint. The ONNX
model downloads on first use and is cached under ``~/.nexus/models/fastembed/``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BUILTIN_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
BUILTIN_DIM = 384
# all-MiniLM-L6-v2 is trained at max_seq_length=256 tokens. fastembed
# truncates anything longer, so callers don't need to pre-trim — but
# graphrag chunk_size should stay below ~1000 chars (~250 tokens) to keep
# embeddings semantically meaningful instead of clipped.
BUILTIN_MAX_TOKENS = 256

_instance: "BuiltinEmbedder | None" = None


class BuiltinEmbedder:
    """Async wrapper around a lazily-loaded :class:`fastembed.TextEmbedding`.

    Matches the ``loom.store.embeddings`` provider protocol: exposes an
    async ``embed(texts)`` coroutine and a ``dim`` attribute.
    """

    def __init__(self, model: str = BUILTIN_MODEL, dim: int = BUILTIN_DIM) -> None:
        self.model = model
        self.dim = dim
        self._impl: Any | None = None
        self._lock = asyncio.Lock()

    def _cache_dir(self) -> Path:
        base = os.environ.get("NEXUS_MODELS_DIR")
        root = Path(base) if base else Path.home() / ".nexus" / "models"
        d = root / "fastembed"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_sync(self) -> Any:
        from fastembed import TextEmbedding
        return TextEmbedding(model_name=self.model, cache_dir=str(self._cache_dir()))

    async def _ensure_loaded(self) -> Any:
        if self._impl is not None:
            return self._impl
        async with self._lock:
            if self._impl is not None:
                return self._impl
            log.info("[builtin-embedder] loading %s (first call may download model)", self.model)
            loop = asyncio.get_running_loop()
            self._impl = await loop.run_in_executor(None, self._load_sync)
            return self._impl

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        impl = await self._ensure_loaded()
        loop = asyncio.get_running_loop()

        def _run() -> list[list[float]]:
            return [list(map(float, v)) for v in impl.embed(texts)]

        return await loop.run_in_executor(None, _run)

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Synchronous variant for the router, which runs inside its own loop."""
        if not texts:
            return []
        if self._impl is None:
            self._impl = self._load_sync()
        return [list(map(float, v)) for v in self._impl.embed(texts)]


def get_builtin_embedder() -> BuiltinEmbedder:
    global _instance
    if _instance is None:
        _instance = BuiltinEmbedder()
    return _instance
