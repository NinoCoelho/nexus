"""File-backed secrets; plaintext at rest, 0600, not committed."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import tomllib
import tomli_w

SECRETS_PATH = Path.home() / ".nexus" / "secrets.toml"


def _load() -> dict[str, str]:
    if not SECRETS_PATH.exists():
        return {}
    with open(SECRETS_PATH, "rb") as f:
        raw = tomllib.load(f)
    return {k: str(v) for k, v in raw.get("keys", {}).items()}


def _save(keys: dict[str, str]) -> None:
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"keys": keys}
    content = tomli_w.dumps(data)
    # Atomic write via tempfile + os.replace
    fd, tmp = tempfile.mkstemp(dir=SECRETS_PATH.parent)
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SECRETS_PATH)
    except Exception:
        os.close(fd)
        os.unlink(tmp)
        raise
    os.chmod(SECRETS_PATH, 0o600)


def get(provider_name: str) -> str | None:
    return _load().get(provider_name)


def set(provider_name: str, key: str) -> None:
    keys = _load()
    keys[provider_name] = key
    _save(keys)


def delete(provider_name: str) -> None:
    keys = _load()
    keys.pop(provider_name, None)
    _save(keys)


def list_provider_names() -> list[str]:
    return list(_load().keys())
