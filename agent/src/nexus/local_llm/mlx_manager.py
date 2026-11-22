"""Install / activate / remove MLX models and register them in config.

The llama.cpp sibling (:mod:`nexus.local_llm.manager`) supervises a
``llama-server`` subprocess per GGUF file. MLX has no subprocess: the model is
loaded **in-process** by :mod:`nexus.agent.llm.mlx_runtime`, so there is no
port, no PID and no health check — "activating" a model is purely a config
write plus a registry rebuild.

Two things here are load-bearing and easy to get wrong:

* **Providers are named ``mlx-<slug>``, never ``local-<slug>``.**
  ``manager.cleanup_stale_config()`` deletes every provider named ``local`` or
  prefixed ``local-`` that is absent from its ``_servers`` process table, and
  clears a ``local/``/``local-``-prefixed ``default_model``. An in-process
  provider is never in that table, so the ``local-`` prefix would mean the
  model silently vanishes from config on every boot. There is a regression test
  for this (``test_mlx_config_survives_cleanup.py``) — keep it passing.
* **Capabilities and the context window go into the ``[[models]]`` entry**,
  because that is the one place both resolution chains check *first*:
  ``_builder._model_context_window`` reads ``context_window`` before consulting
  ``effective_context_window`` (whose substring matching would otherwise
  resolve ``qwen3-30b-a3b-4bit`` to the generic ``qwen3`` window), and
  ``catalog._user_config_capabilities`` unions ``tags`` over everything else.
  This is why MLX models need no capability probe.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .manager import _load_config, _save_config
from .mlx_snapshot import MlxModelInfo, is_mlx_snapshot, read_snapshot, slug_for

log = logging.getLogger(__name__)

__all__ = [
    "MLX_MODELS_DIR",
    "mlx_models_dir",
    "provider_name",
    "model_id_for",
    "list_installed",
    "find_installed",
    "add_to_config",
    "remove_from_config",
    "cleanup_stale_config",
    "delete_installed",
    "active_model_ids",
    "slug_for",
]

#: Where downloaded snapshots live. One directory per model, named by slug.
MLX_MODELS_DIR = Path.home() / ".nexus" / "models" / "mlx"

_PROVIDER_PREFIX = "mlx-"


def mlx_models_dir() -> Path:
    """The snapshot root, created on demand.

    A function as well as the module constant so tests can monkeypatch
    ``MLX_MODELS_DIR`` and have callers pick it up.
    """
    MLX_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return MLX_MODELS_DIR


def provider_name(slug: str) -> str:
    """``"qwen3-4bit"`` → ``"mlx-qwen3-4bit"``. Never ``local-*`` — see module docs."""
    return f"{_PROVIDER_PREFIX}{slug}"


def model_id_for(slug: str) -> str:
    return f"{provider_name(slug)}/{slug}"


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def list_installed() -> list[MlxModelInfo]:
    """Every snapshot directory under :data:`MLX_MODELS_DIR`, newest first."""
    root = MLX_MODELS_DIR
    if not root.is_dir():
        return []
    out: list[MlxModelInfo] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not is_mlx_snapshot(child):
            continue
        try:
            out.append(read_snapshot(child))
        except Exception:  # pragma: no cover - a bad snapshot must not hide good ones
            log.warning("[mlx] could not read snapshot %s", child, exc_info=True)
    out.sort(key=lambda i: _mtime(i.local_dir), reverse=True)
    return out


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def find_installed(slug: str) -> MlxModelInfo | None:
    directory = MLX_MODELS_DIR / slug
    if not is_mlx_snapshot(directory):
        return None
    return read_snapshot(directory)


def active_model_ids() -> set[str]:
    """Model ids currently registered in config under an ``mlx-*`` provider."""
    _cfg_path, raw = _load_config()
    out: set[str] = set()
    for entry in raw.get("models", []) or []:
        provider = entry.get("provider")
        if isinstance(provider, str) and provider.startswith(_PROVIDER_PREFIX):
            model_id = entry.get("id")
            if isinstance(model_id, str):
                out.add(model_id)
    return out


# ---------------------------------------------------------------------------
# Config registration
# ---------------------------------------------------------------------------


def add_to_config(info: MlxModelInfo, *, set_default: bool | None = None) -> str:
    """Register ``info`` as ``providers.mlx-<slug>`` + a ``[[models]]`` entry.

    Preserves every other provider, model and setting. Idempotent: calling it
    twice refreshes the entry rather than duplicating it.

    ``set_default`` forces (``True``) or forbids (``False``) making this the
    agent's ``default_model``. The default (``None``) seeds it only when
    nothing valid is set — and never for a model that cannot do tool calls,
    since the agent loop is tool-driven and a tool-less chat model produces a
    chatbot, not an agent.

    Returns the model id (``"mlx-<slug>/<slug>"``).
    """
    cfg_path, raw = _load_config()
    slug = info.slug
    pname = provider_name(slug)
    model_id = model_id_for(slug)

    providers = raw.setdefault("providers", {})
    # No base_url: the provider is in-process. `model_path` is what
    # registry.build_registry reads to construct MlxProvider.
    providers[pname] = {
        "runtime_kind": "mlx",
        "model_path": str(info.local_dir),
        "api_key_env": "",
        "use_inline_key": False,
        "auth_kind": "anonymous",
    }

    models = list(raw.get("models", []))
    # Preserve a user-edited context window across re-activation.
    context_window = info.max_position_embeddings
    for m in models:
        if m.get("id") == model_id and isinstance(m.get("context_window"), int):
            if m["context_window"] > 0:
                context_window = m["context_window"]
            break
    models = [m for m in models if not (m.get("provider") == pname or m.get("id") == model_id)]

    tags = ["local", "offline", "mlx"]
    for capability in info.capabilities:
        if capability != "chat" and capability not in tags:
            tags.append(capability)

    entry: dict = {
        "id": model_id,
        "provider": pname,
        "model_name": slug,
        "tags": tags,
        "tier": "fast",
        "notes": "Local MLX model running in-process on Apple Silicon.",
    }
    if context_window > 0:
        entry["context_window"] = context_window
    models.append(entry)
    raw["models"] = models

    agent_cfg = raw.setdefault("agent", {})
    cur_default = agent_cfg.get("default_model", "")
    valid_ids = {m["id"] for m in models if isinstance(m.get("id"), str)}
    if set_default is True:
        agent_cfg["default_model"] = model_id
    elif set_default is None and info.supports_tools:
        if not cur_default or cur_default not in valid_ids:
            agent_cfg["default_model"] = model_id

    _save_config(cfg_path, raw)
    log.info(
        "[mlx] added to config: %s (window=%s tools=%s)",
        pname,
        context_window or "unknown",
        info.supports_tools,
    )
    return model_id


def remove_from_config(slug: str) -> None:
    """Drop ``providers.mlx-<slug>`` and its model entry.

    Also clears ``default_model`` and any role reference pointing at the
    removed id — a dangling id rides the adapter's fallback and gets rejected
    upstream, which is a confusing failure to debug.
    """
    cfg_path, raw = _load_config()
    pname = provider_name(slug)
    model_id = model_id_for(slug)

    raw.get("providers", {}).pop(pname, None)
    raw["models"] = [m for m in raw.get("models", []) if m.get("provider") != pname]

    agent_cfg = raw.setdefault("agent", {})
    for key in ("default_model", "last_used_model", "vision_model", "compact_model"):
        if agent_cfg.get(key) == model_id:
            agent_cfg[key] = ""

    _save_config(cfg_path, raw)
    log.info("[mlx] removed from config: %s", pname)


def cleanup_stale_config() -> None:
    """Drop ``mlx-*`` entries whose snapshot directory is gone.

    The counterpart of ``manager.cleanup_stale_config``, but keyed on the
    snapshot existing on disk rather than on a running process — an in-process
    provider has no process to check.
    """
    cfg_path, raw = _load_config()
    providers = raw.get("providers", {})
    changed = False
    removed: list[str] = []

    for pname in list(providers.keys()):
        if not (isinstance(pname, str) and pname.startswith(_PROVIDER_PREFIX)):
            continue
        entry = providers.get(pname) or {}
        model_path = entry.get("model_path") if isinstance(entry, dict) else None
        if isinstance(model_path, str) and model_path and is_mlx_snapshot(Path(model_path)):
            continue
        providers.pop(pname, None)
        removed.append(pname)
        changed = True

    if removed:
        raw["models"] = [m for m in raw.get("models", []) if m.get("provider") not in removed]
        valid_ids = {m["id"] for m in raw.get("models", []) if isinstance(m.get("id"), str)}
        agent_cfg = raw.setdefault("agent", {})
        for key in ("default_model", "last_used_model", "vision_model", "compact_model"):
            value = agent_cfg.get(key, "")
            if isinstance(value, str) and value.startswith(_PROVIDER_PREFIX) and value not in valid_ids:
                agent_cfg[key] = ""

    if changed:
        _save_config(cfg_path, raw)
        log.info("[mlx] cleaned up stale entries: %s", ", ".join(removed))


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


def delete_installed(slug: str) -> bool:
    """Deactivate and delete a snapshot directory. ``False`` if it wasn't there."""
    directory = MLX_MODELS_DIR / slug
    remove_from_config(slug)
    if not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    log.info("[mlx] deleted snapshot %s", directory)
    return not directory.exists()
