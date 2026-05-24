from __future__ import annotations

import threading

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
}

_cache_lock = threading.Lock()
_cached_features: set[str] | None = None


def set_features(features: set[str] | None) -> bool:
    global _cached_features
    with _cache_lock:
        if features is None:
            changed = _cached_features is not None
            _cached_features = None
            return changed
        changed = _cached_features != features
        _cached_features = features
        return changed


def get_features() -> set[str]:
    with _cache_lock:
        if _cached_features is None:
            return set(ALL_FEATURES)
        return set(_cached_features)


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
