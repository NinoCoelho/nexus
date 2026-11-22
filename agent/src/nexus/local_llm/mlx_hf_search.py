"""Hugging Face Hub search for MLX model repositories.

Mirrors :mod:`nexus.local_llm.hf_search` (same deferred-import discipline, same
``HfSearchError``) but targets MLX repos instead of GGUF files, and adds a
pre-download inspection step that the GGUF path doesn't need.

Two searches are merged, because neither alone is sufficient:

* ``author="mlx-community"`` — the big curated namespace most users want.
* ``library="mlx"`` — individuals and labs publishing MLX weights under their
  own name, which an author filter would miss entirely.

:func:`repo_details` fetches just ``config.json`` (a few KB) so the picker can
show quantization, context window and a fits-in-memory verdict **before** the
user commits to a 20 GB download. That is the whole point of having it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .hf_search import HfSearchError

log = logging.getLogger(__name__)

__all__ = ["search_mlx_repos", "repo_details", "HfSearchError"]

_MLX_AUTHOR = "mlx-community"


def _api() -> Any:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise HfSearchError(
            "huggingface_hub not installed. Run `uv sync` (or "
            "`uv tool install --reinstall --editable ./agent`)."
        ) from exc
    return HfApi()


def _list_models(api: Any, **kwargs: Any) -> list[Any]:
    """``api.list_models`` across huggingface_hub versions.

    1.x dropped ``direction`` and made ``sort="downloads"`` descending by
    default; older versions need the explicit form. Same dance as
    ``hf_search.search_gguf_repos``.
    """
    try:
        return list(api.list_models(**kwargs))
    except TypeError:
        legacy = dict(kwargs)
        legacy.pop("sort", None)
        try:
            return list(api.list_models(**legacy))
        except Exception:
            return []
    except Exception:
        return []


def search_mlx_repos(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search for MLX repos, newest/most-downloaded first.

    Returns dicts with ``id``, ``downloads``, ``likes``, ``tags`` — the same
    shape ``search_gguf_repos`` returns, so the UI's result list is reusable.
    Results are de-duplicated by repo id across both passes.
    """
    api = _api()
    seen: dict[str, dict[str, Any]] = {}

    passes: list[dict[str, Any]] = [
        {"search": query, "author": _MLX_AUTHOR, "limit": limit, "sort": "downloads"},
        {"search": query, "library": "mlx", "limit": limit, "sort": "downloads"},
    ]
    for kwargs in passes:
        for model in _list_models(api, **kwargs):
            repo_id = getattr(model, "id", None) or getattr(model, "modelId", None)
            if not repo_id or repo_id in seen:
                continue
            seen[repo_id] = {
                "id": repo_id,
                "downloads": int(getattr(model, "downloads", 0) or 0),
                "likes": int(getattr(model, "likes", 0) or 0),
                "tags": list(getattr(model, "tags", []) or []),
            }

    if not seen:
        return []

    out = sorted(seen.values(), key=lambda r: r["downloads"], reverse=True)
    return out[:limit]


def repo_details(repo_id: str, *, context_tokens: int = 0) -> dict[str, Any]:
    """Inspect a repo before downloading it.

    Downloads only ``config.json``. Returns quantization bits, the context
    window, an estimated resident size and — when the host is Apple Silicon — a
    fits/doesn't-fit verdict with real numbers.

    ``size_bytes`` comes from the repo file listing when available, since the
    weights are not downloaded yet and cannot be measured locally.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise HfSearchError("huggingface_hub not installed") from exc

    cfg: dict[str, Any] = {}
    try:
        path = hf_hub_download(repo_id=repo_id, filename="config.json")
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            cfg = loaded
    except Exception as exc:
        raise HfSearchError(f"could not read config.json for {repo_id}: {exc}") from exc

    size_bytes = _repo_weight_bytes(repo_id)

    quant = cfg.get("quantization")
    bits = 0
    if isinstance(quant, dict) and isinstance(quant.get("bits"), (int, float)):
        bits = int(quant["bits"])

    window = _config_window(cfg)
    is_vlm = isinstance(cfg.get("vision_config"), dict)

    # Reuse the same estimator the activate route gates on, so the number the
    # user sees before downloading matches the one that later accepts or
    # refuses the model.
    from ..agent.llm.mlx_support import fits_in_unified_memory, kv_bytes_per_token

    tokens = context_tokens or window or 8192
    kv_bytes = kv_bytes_per_token(cfg) * tokens
    need_gb = round((size_bytes + kv_bytes) / 1_000_000_000 + 1.5, 2) if size_bytes else 0.0

    verdict: dict[str, Any] = {"fits": None, "reason": "unknown"}
    if need_gb:
        try:
            from .hardware import probe

            ok, reason = fits_in_unified_memory(need_gb, probe())
            verdict = {"fits": ok, "reason": reason}
        except Exception:  # pragma: no cover - sizing is advisory
            log.debug("[mlx] hardware probe failed", exc_info=True)

    return {
        "repo_id": repo_id,
        "quantization_bits": bits,
        "context_window": window,
        "is_vlm": is_vlm,
        "architectures": [a for a in (cfg.get("architectures") or []) if isinstance(a, str)],
        "model_type": cfg.get("model_type") if isinstance(cfg.get("model_type"), str) else "",
        "size_bytes": size_bytes,
        "size_gb": round(size_bytes / 1_000_000_000, 2) if size_bytes else 0.0,
        "estimated_resident_gb": need_gb,
        "fits": verdict,
    }


def _config_window(cfg: dict[str, Any]) -> int:
    for node in (cfg, cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else {}):
        if not isinstance(node, dict):
            continue
        value = node.get("max_position_embeddings")
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


def _repo_weight_bytes(repo_id: str) -> int:
    """Total ``*.safetensors`` size from the repo listing, or 0 if unavailable."""
    try:
        api = _api()
        info = api.model_info(repo_id, files_metadata=True)
    except Exception:
        log.debug("[mlx] could not list files for %s", repo_id, exc_info=True)
        return 0
    total = 0
    for sibling in getattr(info, "siblings", None) or []:
        name = getattr(sibling, "rfilename", "") or ""
        if not name.endswith(".safetensors"):
            continue
        if name.startswith("original/"):
            continue
        size = getattr(sibling, "size", None)
        if isinstance(size, (int, float)) and size > 0:
            total += int(size)
    return total
