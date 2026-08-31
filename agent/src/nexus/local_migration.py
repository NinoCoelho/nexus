"""One-time startup migration that cleans stale nexus-llm account artifacts.

The hosted subscription (nexus-model.us) was shut down; the app is fully
local now. On startup this sweeps whatever a previous install may have
left behind:

* ``[nexus_account]`` section + ``[server].multi_user`` in config.toml
* hosted ``nexus`` provider entries and their ``nexus`` / ``nexus-vision``
  models (plus dangling model references to the removed provider)
* ``~/.nexus/account.json``, ``feature_cache.json``, ``.feature_cache_key``
* ``nexus_api_key`` in secrets.toml (``broker_api_key`` and everything
  else is kept)

Idempotent — a clean install or an already-migrated one is a no-op.
``run()`` returns a summary dict; ``config_changed`` tells the caller the
in-memory config/registry should be rebuilt.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

log = logging.getLogger(__name__)

_REMOVED_MODEL_IDS = {"nexus", "nexus-vision"}


def _root() -> Path:
    return Path.home() / ".nexus"


def _migrate_config(path: Path) -> bool:
    if not path.exists():
        return False
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    changed = False

    if "nexus_account" in raw:
        raw.pop("nexus_account", None)
        changed = True

    server = raw.get("server")
    if isinstance(server, dict) and "multi_user" in server:
        server.pop("multi_user", None)
        if not server:
            raw.pop("server", None)
        changed = True

    removed_providers: set[str] = set()
    providers = raw.get("providers")
    if isinstance(providers, dict):
        for name in list(providers):
            p = providers.get(name) or {}
            if (
                name == "nexus"
                or p.get("runtime_kind") == "nexus"
                or p.get("type") == "nexus"
                or p.get("catalog_id") == "nexus"
            ):
                removed_providers.add(name)
                providers.pop(name, None)
                changed = True

    models = raw.get("models")
    if isinstance(models, list):
        kept = []
        for m in models:
            if not isinstance(m, dict):
                kept.append(m)
                continue
            if (
                m.get("id") in _REMOVED_MODEL_IDS
                or m.get("model_name") in _REMOVED_MODEL_IDS
                or m.get("provider") in removed_providers
            ):
                changed = True
                continue
            kept.append(m)
        raw["models"] = kept

    removed_ids = _REMOVED_MODEL_IDS | {
        m.get("id")
        for m in (models if isinstance(models, list) else [])
        if isinstance(m, dict) and m.get("provider") in removed_providers
    }
    agent = raw.get("agent")
    if isinstance(agent, dict):
        for key in ("default_model", "last_used_model", "vision_model"):
            if agent.get(key) in removed_ids:
                agent[key] = ""
                changed = True

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            tomli_w.dump(raw, f)
    return changed


def _delete_file(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        log.warning("[local_migration] failed to delete %s", path, exc_info=True)
        return False


def _migrate_secrets() -> bool:
    from . import secrets as _secrets

    if _secrets.get("nexus_api_key") is None:
        return False
    _secrets.delete("nexus_api_key")
    return True


def run() -> dict[str, Any]:
    root = _root()
    changed_config = _migrate_config(root / "config.toml")

    deleted: list[str] = []
    for name in ("account.json", "feature_cache.json", ".feature_cache_key"):
        if _delete_file(root / name):
            deleted.append(name)

    removed_secret = _migrate_secrets()

    actions: list[str] = []
    if changed_config:
        actions.append("config cleaned")
    if deleted:
        actions.append(f"deleted {', '.join(deleted)}")
    if removed_secret:
        actions.append("removed nexus_api_key")
    if actions:
        log.info("[local_migration] %s", "; ".join(actions))
    else:
        log.info("[local_migration] nothing to clean")

    return {
        "config_changed": changed_config,
        "deleted_files": deleted,
        "removed_secret": removed_secret,
    }
