"""Runtime-discovered context windows for local LLM servers.

LM Studio's native REST API (``GET {base_url without /v1}/api/v0/models``)
reports each model's ``max_context_length`` and, when the model is loaded,
``loaded_context_length`` — the window it is actually serving. None of that
reaches the OpenAI-compat ``/v1/models`` endpoint, so models that aren't in
``KNOWN_WINDOWS`` or a family match fall through to ``DEFAULT_FALLBACK_WINDOW``
(32K) and, after tools overhead + output headroom, budget on a ~10K usable
slice. That is how a 256K nemotron thrashed on hard_trim-everything
compaction (2026-11-21, session ``b27a048e``).

Contract:

* :func:`refresh` fetches and caches; :func:`lookup` is a **pure sync cache
  read** used by ``overflow.known_context_window`` — the deep-loop callers
  never do I/O.
* Every failure is soft: LM Studio being off must not break anything, and
  repeated failures only log once (warning on the first, debug after) so a
  stopped LM Studio doesn't spam the daemon log every cycle.
* Keys are bare model names (``nvidia-nemotron-...``) lowercased — the same
  normalization ``known_context_window`` applies, and what the adapter's
  partition fallback routes on. Two providers serving same-named models
  would collide (last refresh wins); single local instance is the real shape.
* No config write-back and no disk cache: startup discovers in milliseconds
  and configured ``[[models]].context_window`` still wins (checked first by
  ``effective_context_window``).

Precedence in ``known_context_window``: curated exact/substring entries →
discovered live values → family guesses → 0. A live server value beats a
family heuristic; the family table remains the offline fallback.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# How often the lifespan refresh task re-fetches. A model reloaded in LM
# Studio with a different context (e.g. 8K default → 256K) must land here
# within minutes, not on daemon restart.
TTL_SECONDS = 300

_HTTP_TIMEOUT_SECONDS = 5.0

# bare model name (lowercased) → tokens
_CACHE: dict[str, int] = {}

# Failure-log de-duplication: warn once per failing streak, debug after.
_erroring = False


def lookup(model: str) -> int:
    """Discovered window for ``model`` (registry id or bare name), 0 if none."""
    if not model:
        return 0
    name = model.split("/")[-1].strip().lower()
    return _CACHE.get(name, 0)


def _windows_from_payload(payload: Any) -> dict[str, int]:
    """Parse LM Studio ``/api/v0/models`` JSON → {bare model name: tokens}.

    ``loaded_context_length`` is the truth for serving (LM Studio can load a
    model at a smaller context than its architectural max), ``max_context_length``
    covers not-loaded models. Embeddings models carry context too but are
    irrelevant to chat budgeting — skipped.
    """
    out: dict[str, int] = {}
    entries = payload.get("data") if isinstance(payload, dict) else None
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "embeddings":
            continue
        mid = str(entry.get("id") or "").strip().lower()
        if not mid:
            continue
        max_len = int(entry.get("max_context_length") or 0)
        loaded = int(entry.get("loaded_context_length") or 0)
        window = min(max_len, loaded) if (max_len and loaded) else (loaded or max_len)
        if window > 0:
            out[mid] = int(window)
    return out


def _api_root(base_url: str) -> str:
    """``http://host:1235/v1`` → ``http://host:1235`` (native API lives at root)."""
    root = base_url.strip().rstrip("/")
    root = root.removesuffix("/v1")
    return root


async def refresh(provider_name: str, base_url: str) -> int:
    """Fetch and cache windows for one provider. Returns models cached.

    Never raises: transport/parse failures log (de-duplicated) and leave the
    existing cache intact — stale windows are better than none, and the
    periodic task retries.
    """
    global _erroring
    import httpx

    url = f"{_api_root(base_url)}/api/v0/models"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — soft contract: never raise to the caller
        if not _erroring:
            log.warning(
                "[provider] %s: context-window discovery failed (%s): %s",
                provider_name, url, exc,
            )
        else:
            log.debug(
                "[provider] %s: context-window discovery still failing: %s",
                provider_name, exc,
            )
        _erroring = True
        return 0
    windows = _windows_from_payload(payload)
    if windows:
        _CACHE.update(windows)
        log.info(
            "[provider] %s: discovered %d context window(s) from %s",
            provider_name, len(windows), url,
        )
    if _erroring:
        _erroring = False
        log.info("[provider] %s: context-window discovery recovered", provider_name)
    return len(windows)


def reset() -> None:
    """Test hook: drop the cache and the failure streak."""
    global _erroring
    _CACHE.clear()
    _erroring = False
