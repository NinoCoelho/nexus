"""File-backed secrets; plaintext at rest, 0600, not committed.

Storage shape on disk (TOML)::

    [keys]
    OPENAI_API_KEY = "sk-..."
    GITHUB_TOKEN = "ghp_..."

    [meta.OPENAI_API_KEY]
    kind = "provider"
    created_at = "2026-04-30T..."

    [meta.GITHUB_TOKEN]
    kind = "skill"
    skill = "github_issues"
    created_at = "2026-04-30T..."

Resolution rules:
- ``get(name)``: file-only (back-compat for provider routing).
- ``resolve(name)``: env var first, then file. Used by ``$NAME`` substitution.
- ``exists(name)``: true if env or file has it. Used by first-use prompts.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomli_w
import tomllib

SECRETS_PATH = Path.home() / ".nexus" / "secrets.toml"


def _load_raw() -> dict[str, dict[str, Any]]:
    if not SECRETS_PATH.exists():
        return {"keys": {}, "meta": {}}
    with open(SECRETS_PATH, "rb") as f:
        raw = tomllib.load(f)
    keys = {k: str(v) for k, v in (raw.get("keys") or {}).items()}
    meta_in = raw.get("meta") or {}
    meta = {k: dict(v) for k, v in meta_in.items() if isinstance(v, dict)}
    return {"keys": keys, "meta": meta}


def _save_raw(data: dict[str, dict[str, Any]]) -> None:
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"keys": data.get("keys", {})}
    meta = data.get("meta") or {}
    if meta:
        payload["meta"] = meta
    content = tomli_w.dumps(payload)
    fd, tmp = tempfile.mkstemp(dir=SECRETS_PATH.parent)
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SECRETS_PATH)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.chmod(SECRETS_PATH, 0o600)


def get(name: str) -> str | None:
    """Return the file-stored value, or None. Does NOT consult env vars."""
    return _load_raw()["keys"].get(name)


def resolve(name: str) -> str | None:
    """Env var first, then file-stored value. Used by ``$NAME`` substitution."""
    env_val = os.environ.get(name)
    if env_val:
        return env_val
    return get(name)


def exists(name: str) -> bool:
    """True if env or file has a non-empty value."""
    return bool(resolve(name))


def set(name: str, key: str, *, kind: str = "generic", skill: str | None = None) -> None:
    data = _load_raw()
    data["keys"][name] = key
    meta_entry: dict[str, Any] = {
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if skill:
        meta_entry["skill"] = skill
    data["meta"][name] = meta_entry
    _save_raw(data)


def delete(name: str) -> None:
    data = _load_raw()
    data["keys"].pop(name, None)
    data["meta"].pop(name, None)
    _save_raw(data)


def list_provider_names() -> list[str]:
    """Back-compat — returns all stored key names."""
    return list(_load_raw()["keys"].keys())


def _mask(value: str) -> str:
    if len(value) >= 12:
        return f"{value[:3]}…{value[-4:]}"
    return "••••"


def list_all() -> list[dict[str, Any]]:
    """Listing for the Settings UI. Never returns raw values.

    Each entry: ``{name, kind, skill, created_at, masked, source}``.
    Only file-stored entries are returned — env-only values are not Nexus-managed.
    """
    data = _load_raw()
    out: list[dict[str, Any]] = []
    for name, value in data["keys"].items():
        meta = data["meta"].get(name) or {}
        out.append(
            {
                "name": name,
                "kind": meta.get("kind") or "generic",
                "skill": meta.get("skill"),
                "created_at": meta.get("created_at"),
                "masked": _mask(value),
                "source": "store",
            }
        )
    out.sort(key=lambda e: e["name"])
    return out
