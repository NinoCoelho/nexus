from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

ALL_FEATURES = frozenset({
    "chat",
    "local_models",
    "cloud_models",
    "kanban",
    "calendar",
    "workflow",
    "knowledge",
    "dream",
    "heartbeat",
    "multi_user",
    "database",
    "projects",
})

FEATURE_TOOLS: dict[str, list[str]] = {
    "kanban": ["kanban_manage", "kanban_query", "show_kanban"],
    "calendar": ["calendar_manage"],
    "knowledge": ["vault_semantic_search", "ontology_manage"],
    "heartbeat": ["manage_heartbeat", "dispatch_card"],
    "database": ["datatable_manage", "dashboard_manage", "vault_csv", "visualize_table", "show_data_table", "show_dashboard_widget"],
}

FEATURE_ROUTES: dict[str, list[str]] = {
    "kanban": ["/vault/kanban"],
    "calendar": ["/vault/calendar"],
    "workflow": ["/workflows", "/workflow/trigger"],
    "knowledge": ["/graph", "/graphrag"],
    "dream": ["/dream"],
    "heartbeat": ["/heartbeat"],
    "multi_user": ["/auth/setup", "/auth/register", "/auth/invites", "/admin", "/share"],
    "database": ["/vault/datatable", "/vault/dashboard"],
    "projects": ["/projects"],
}

_cache_lock = threading.Lock()
_cached_features: set[str] | None = None

_CACHE_PATH = Path.home() / ".nexus" / "feature_cache.json"
_HMAC_KEY = b"nexus-features-cache-v1-vE7kQ2zN9pXmR4wL"


def _sign_payload(data: bytes) -> str:
    return hmac.new(_HMAC_KEY, data, hashlib.sha256).hexdigest()


def _save_cache(features: set[str]) -> None:
    payload = {
        "features": sorted(features),
        "nonce": os.urandom(16).hex(),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = _sign_payload(raw)
    envelope = {"data": raw.decode(), "sig": sig}
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(envelope, separators=(",", ":")))
        tmp.replace(_CACHE_PATH)
    except Exception:
        log.debug("[features] cache write failed", exc_info=True)


def _load_cache() -> set[str] | None:
    try:
        if not _CACHE_PATH.exists():
            return None
        envelope = json.loads(_CACHE_PATH.read_text())
        raw = envelope.get("data", "")
        sig = envelope.get("sig", "")
        expected = _sign_payload(raw.encode())
        if not hmac.compare_digest(sig, expected):
            log.warning("[features] cache signature mismatch — ignoring stale cache")
            return None
        payload = json.loads(raw)
        features = payload.get("features")
        if not isinstance(features, list):
            return None
        return set(features)
    except Exception:
        log.debug("[features] cache read failed", exc_info=True)
        return None


def _clear_cache() -> None:
    try:
        if _CACHE_PATH.exists():
            _CACHE_PATH.unlink()
    except Exception:
        pass


def set_features(features: set[str] | None) -> bool:
    global _cached_features
    import logging as _log
    _logger = _log.getLogger(__name__)
    with _cache_lock:
        if features is None:
            changed = _cached_features is not None
            _cached_features = None
            _clear_cache()
            _logger.info("[features] cleared")
            return changed
        changed = _cached_features != features
        _cached_features = features
        _save_cache(features)
        _logger.info("[features] set: %s (changed=%s)", sorted(features), changed)
        return changed


def get_features() -> set[str]:
    global _cached_features
    with _cache_lock:
        if _cached_features is not None:
            return set(_cached_features)
        cached = _load_cache()
        if cached is not None:
            _cached_features = cached
            return set(cached)
        return set(ALL_FEATURES)


def is_enabled(feature: str) -> bool:
    return feature in get_features()


def tools_for_features(features: set[str]) -> set[str]:
    tools: set[str] = set()
    for feat, tool_names in FEATURE_TOOLS.items():
        if feat in features:
            tools.update(tool_names)
    return tools


def feature_for_route(path: str) -> str | None:
    for feat, prefixes in FEATURE_ROUTES.items():
        for prefix in prefixes:
            if path.startswith(prefix):
                return feat
    return None
