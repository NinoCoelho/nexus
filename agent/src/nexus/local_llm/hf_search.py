"""HuggingFace Hub search wrappers for GGUF model discovery.

All network calls are deferred inside functions so that importing this module
does not require ``huggingface_hub`` to be installed. Functions raise
``HfSearchError`` on any network or API failure.
"""

from __future__ import annotations

import re
from typing import Any


class HfSearchError(Exception):
    """Raised when a HuggingFace Hub API call fails."""


def search_gguf_repos(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search HuggingFace Hub for GGUF model repositories.

    Args:
        query: Free-text search string.
        limit: Maximum number of results to return.

    Returns:
        List of dicts with keys: id, downloads, likes, tags.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise HfSearchError(
            "huggingface_hub not installed. Run `uv sync` (or "
            "`uv tool install --reinstall --editable ./agent`)."
        ) from exc
    try:
        api = HfApi()
        # huggingface_hub 1.x dropped the ``direction`` keyword and made
        # ``sort="downloads"`` descending by default. We try the modern
        # signature first and fall back for older versions still in the wild.
        kwargs: dict[str, Any] = {
            "search": query,
            "filter": "gguf",
            "limit": limit,
            "sort": "downloads",
        }
        try:
            models = api.list_models(**kwargs)
        except TypeError:
            models = api.list_models(**kwargs, direction=-1)
        results = []
        for m in models:
            results.append({
                "id": m.id,
                "downloads": getattr(m, "downloads", 0) or 0,
                "likes": getattr(m, "likes", 0) or 0,
                "tags": list(getattr(m, "tags", []) or []),
            })
        return results
    except HfSearchError:
        raise
    except Exception as exc:
        raise HfSearchError(f"search_gguf_repos failed: {exc}") from exc


def list_repo_ggufs(repo_id: str) -> list[dict[str, Any]]:
    """List GGUF files available in a HuggingFace repository.

    Args:
        repo_id: Repository identifier (e.g. ``"owner/model-name"``).

    Returns:
        List of dicts with keys: filename, size_bytes, quant_label.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise HfSearchError("huggingface_hub not installed.") from exc
    try:
        api = HfApi()
        all_files = list(api.list_repo_files(repo_id))
        gguf_files = [f for f in all_files if f.endswith(".gguf")]
        if not gguf_files:
            return []

        paths_info = list(api.get_paths_info(repo_id, paths=gguf_files))
        size_map: dict[str, int] = {}
        for info in paths_info:
            size = getattr(info, "size", None)
            if size is not None:
                size_map[info.path] = int(size)

        results = []
        for filename in gguf_files:
            size_bytes = size_map.get(filename, 0)
            quant_label = _extract_quant_label(filename)
            results.append({
                "filename": filename,
                "size_bytes": size_bytes,
                "quant_label": quant_label,
            })
        return results
    except Exception as exc:
        raise HfSearchError(f"list_repo_ggufs failed: {exc}") from exc


def repo_card(repo_id: str) -> dict[str, Any]:
    """Fetch metadata for a HuggingFace repository.

    Args:
        repo_id: Repository identifier (e.g. ``"owner/model-name"``).

    Returns:
        Dict with keys: id, downloads, likes, tags, description.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise HfSearchError("huggingface_hub not installed.") from exc
    try:
        api = HfApi()
        info = api.model_info(repo_id)
        description = ""
        card_data = getattr(info, "cardData", None)
        if card_data and isinstance(card_data, dict):
            description = str(card_data.get("description", ""))
        return {
            "id": info.id,
            "downloads": getattr(info, "downloads", 0) or 0,
            "likes": getattr(info, "likes", 0) or 0,
            "tags": list(getattr(info, "tags", []) or []),
            "description": description,
        }
    except Exception as exc:
        raise HfSearchError(f"repo_card failed: {exc}") from exc


_QUANT_RE = re.compile(r"Q\d+(?:_[A-Z0-9]+)*")


def _extract_quant_label(filename: str) -> str:
    """Extract quantisation label from a GGUF filename.

    Examples::

        "model-Q4_K_M.gguf"  → "Q4_K_M"
        "model-Q8_0.gguf"    → "Q8_0"
        "model.gguf"         → "Unknown"
    """
    match = _QUANT_RE.search(filename)
    return match.group(0) if match else "Unknown"
