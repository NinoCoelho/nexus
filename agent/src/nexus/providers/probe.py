"""Lazy capability probe — detect whether a model really accepts images.

Design contract (do not regress):

* **Lazy** — a model is probed only when it is actually used: selected as
  the default chat model, or featured in a launched chat turn. Never at
  provider-discovery time, never in bulk over the discovered list.
* **Asynchronous** — probes run detached from any turn. The triggering
  turn keeps using the static resolution (catalog / family regex / manual
  tags); the probe result only benefits later turns.
* **Sequential** — one global :class:`asyncio.Lock`; never two provider
  calls in flight from this module.

Results persist on the ``[[models]]`` entry (``probed`` dict + ``probed_at``
— see :func:`catalog._user_config_capabilities`) so a model is probed once
per confident outcome. Only confident outcomes are cached: ``2xx`` →
capability present; ``4xx`` whose body clearly blames the image input →
capability absent. Anything else (auth, rate limit, 5xx, cold local model
load) leaves the cache untouched and counts toward an in-memory backoff so
a broken endpoint isn't hammered on every turn.

Manual ``tags`` always win: resolution unions both sources, so a probe
result can only *add* a capability the user didn't assert, never remove one
they did.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import time
from datetime import UTC, datetime

import httpx

from ..multimodal import image_rejected_by_upstream

log = logging.getLogger(__name__)

# Canonical 1×1 transparent PNG — small enough that even strict
# content-length guards accept it.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

PROBE_TIMEOUT_S = 60.0  # generous: cold llama.cpp models load on first hit
_BACKOFF_WINDOW_S = 3600.0
_BACKOFF_MAX_ATTEMPTS = 3


def _resolve_endpoint(model_id: str) -> tuple[str, dict[str, str], str] | None:
    """``(chat_completions_url, headers, model_name)`` for probing.

    Only OpenAI-compat endpoints (``openai_compat`` / ``ollama`` runtime
    kinds — the whole local-model world) are probeable. Native Anthropic /
    Bedrock providers return ``None``; the bundled catalog already covers
    their vision support. Returns ``None`` too when the entry/provider is
    missing or auth can't be resolved — probing must never guess.
    """
    import os as _os

    from .. import secrets as _secrets
    from ..config_file import load as load_config

    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001 — startup races / missing file
        return None

    entry = next((m for m in cfg.models if m.id == model_id), None)
    if entry is None:
        return None
    pcfg = cfg.providers.get(entry.provider)
    if pcfg is None:
        return None

    runtime_kind = (
        getattr(pcfg, "runtime_kind", "")
        or getattr(pcfg, "type", "")
        or "openai_compat"
    )
    if runtime_kind not in ("openai_compat", "ollama"):
        return None

    base_url = (pcfg.base_url or "").rstrip("/")
    if not base_url:
        return None
    if runtime_kind == "ollama" and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    api_key = ""
    cred_ref = getattr(pcfg, "credential_ref", None)
    if cred_ref:
        api_key = _secrets.resolve(cred_ref) or ""
    elif getattr(pcfg, "use_inline_key", False):
        api_key = _secrets.get(entry.provider) or ""
    elif getattr(pcfg, "api_key_env", ""):
        api_key = _os.environ.get(pcfg.api_key_env, "")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return f"{base_url}/chat/completions", headers, entry.model_name


async def probe_model(model_id: str) -> dict[str, bool] | None:
    """Send one minimal image-bearing chat call.

    Returns the confident decision (``{"vision": True|False}``) or ``None``
    when undetermined (non-probeable provider, auth failure, server error,
    timeout). Never raises.
    """
    resolved = _resolve_endpoint(model_id)
    if resolved is None:
        return None
    url, headers, model_name = resolved
    payload = {
        "model": model_name,
        "max_tokens": 5,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Reply with OK."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{_TINY_PNG_B64}"
                        },
                    },
                ],
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except Exception:  # noqa: BLE001 — transport errors are "unknown"
        log.info("probe: %s transport failure — treating as unknown", model_id)
        return None
    if resp.status_code == 200:
        return {"vision": True}
    if image_rejected_by_upstream(resp.status_code, resp.text):
        return {"vision": False}
    log.info(
        "probe: %s returned HTTP %s — undetermined, no cache write",
        model_id, resp.status_code,
    )
    return None


def _persist(model_id: str, tags: dict[str, bool]) -> None:
    """Merge ``tags`` into the ``[[models]]`` entry's probe cache.

    Load-fresh-modify-save (the routes' pattern) so a probe completing
    concurrently with a settings save can't stomp unrelated edits.
    """
    from ..config_file import load as load_config
    from ..config_file import save as save_cfg

    cfg = load_config()
    for entry in cfg.models:
        if entry.id == model_id:
            merged = dict(entry.probed or {})
            merged.update(tags)
            entry.probed = merged
            entry.probed_at = datetime.now(UTC).isoformat(
                timespec="seconds"
            )
            break
    else:
        return
    save_cfg(cfg)


class CapabilityProbeService:
    """Sequential, deduplicating probe queue.

    One drain task at a time pops the highest-priority queued model and
    probes it under the global lock. Media-carrying turns enqueue at high
    priority so the model the user is *actively* sending an image to jumps
    ahead of background default-selection probes.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._heap: list[tuple[int, int, str]] = []  # (prio, seq, model_id)
        self._queued: set[str] = set()
        self.probing: set[str] = set()  # exposed for UI/status
        self._seq = 0
        self._draining = False
        self._unknown: dict[str, list[float]] = {}

    # ── public API ───────────────────────────────────────────────────────

    def enqueue_if_uncached(
        self,
        model_id: str,
        *,
        priority: str = "normal",
        has_media: bool = False,
    ) -> bool:
        """Queue a probe unless the model is already decided / queued /
        probing / backed off. Empty ``model_id`` resolves to the configured
        default chat model. Returns True when a probe was queued."""
        model_id = self._effective_model_id(model_id)
        if not model_id:
            return False
        if self._already_decided(model_id):
            return False
        if model_id in self._queued or model_id in self.probing:
            return False
        if self._backed_off(model_id):
            return False
        prio = 0 if (has_media or priority == "high") else 1
        heapq.heappush(self._heap, (prio, self._seq, model_id))
        self._seq += 1
        self._queued.add(model_id)
        if not self._draining:
            self._draining = True
            asyncio.create_task(self._drain())
        return True

    def clear_backoff(self, model_id: str) -> None:
        """Drop the unknown-failure backoff for ``model_id`` — used by the
        user-initiated re-test endpoint, where explicit intent should win
        over the anti-hammer guard."""
        self._unknown.pop(model_id, None)

    def status(self) -> dict[str, list[str]]:
        return {
            "probing": sorted(self.probing),
            "queued": [mid for _, _, mid in sorted(self._heap)],
        }

    # ── internals ────────────────────────────────────────────────────────

    @staticmethod
    def _effective_model_id(model_id: str) -> str:
        model_id = (model_id or "").strip()
        if model_id:
            return model_id
        try:
            from ..config_file import load as load_config

            return (load_config().agent.default_model or "").strip()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _already_decided(model_id: str) -> bool:
        """Manual ``tags`` or a cached probe decision → nothing to learn."""
        try:
            from ..config_file import load as load_config

            cfg = load_config()
        except Exception:  # noqa: BLE001
            return True  # can't read config → don't probe
        for entry in cfg.models:
            if entry.id == model_id:
                if "vision" in (entry.tags or []):
                    return True
                return "vision" in (entry.probed or {})
        return True  # unknown id → nothing to probe against

    def _backed_off(self, model_id: str) -> bool:
        now = time.monotonic()
        recent = [
            t for t in self._unknown.get(model_id, [])
            if now - t < _BACKOFF_WINDOW_S
        ]
        self._unknown[model_id] = recent
        return len(recent) >= _BACKOFF_MAX_ATTEMPTS

    async def _drain(self) -> None:
        try:
            while self._heap:
                _, _, model_id = heapq.heappop(self._heap)
                self._queued.discard(model_id)
                async with self._lock:
                    self.probing.add(model_id)
                    try:
                        result = await probe_model(model_id)
                    except Exception:
                        log.warning(
                            "probe: %s crashed — undetermined",
                            model_id, exc_info=True,
                        )
                        result = None
                    finally:
                        self.probing.discard(model_id)
                if result is not None:
                    try:
                        _persist(model_id, result)
                        self._unknown.pop(model_id, None)
                        log.info(
                            "probe: %s vision=%s — cached",
                            model_id, result.get("vision"),
                        )
                    except Exception:
                        log.warning(
                            "probe: failed to persist %s", model_id,
                            exc_info=True,
                        )
                else:
                    self._unknown.setdefault(model_id, []).append(
                        time.monotonic()
                    )
        finally:
            self._draining = False


_service = CapabilityProbeService()


def get_probe_service() -> CapabilityProbeService:
    return _service


def reset_probe_service() -> None:
    """Test hook — swap in a fresh service (fresh queue/backoff state)."""
    global _service
    _service = CapabilityProbeService()
