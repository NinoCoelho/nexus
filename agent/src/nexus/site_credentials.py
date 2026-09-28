"""Site login credentials (username + password per site), encrypted at rest.

Used by browser-control flows (the chrome-devtools CDP skill, the ``page``
tool): the agent asks the user for a site login once via a masked HITL
form, stores it here, and later asks the *server* to fill it into a
browser. The plaintext password is never returned to the LLM — the only
readers are server-side fill code and the Settings UI (which only ever
sees usernames).

Storage shape (Fernet-encrypted JSON at ``~/.nexus/site_credentials.db``,
key at ``~/.nexus/keys/site_credentials.key``, both mode 0600)::

    {
      "github.com": {
        "username": "user@example.com",
        "password": "hunter2",
        "created_at": "2026-09-28T...",
        "updated_at": "2026-09-28T...",
        "last_used_at": null
      }
    }

The key file is generated on first use. Encryption protects the file at
rest (backups, sync folders, accidental reads); like every local secret
store, a process running as the same user can decrypt it.

Entries are keyed by normalized site host (``normalize_site``): scheme,
path, port and a leading ``www.`` are stripped, so ``https://www.GitHub.com/login``
and ``github.com`` resolve to the same entry.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SITE_CREDS_PATH = Path.home() / ".nexus" / "site_credentials.db"
SITE_CREDS_KEY_PATH = Path.home() / ".nexus" / "keys" / "site_credentials.key"


@dataclass(frozen=True)
class SiteCredential:
    site: str
    username: str
    password: str
    created_at: str | None = None
    updated_at: str | None = None
    last_used_at: str | None = None


def normalize_site(value: str) -> str:
    """Normalize a URL or bare host to a canonical site key.

    ``https://www.Example.com/login?x=1`` → ``example.com``.
    Returns an empty string for values with no usable host.
    """
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    host = raw
    if "://" in host:
        try:
            host = host.split("://", 1)[1]
        except IndexError:
            return ""
    # Strip path, query, fragment; then userinfo and port.
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1]
    if host.startswith("["):  # IPv6 literal
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.split(":", 1)[0]
    host = host.strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _fernet() -> Any:
    from cryptography.fernet import Fernet

    if SITE_CREDS_KEY_PATH.exists():
        return Fernet(SITE_CREDS_KEY_PATH.read_bytes().strip())
    SITE_CREDS_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd, tmp = tempfile.mkstemp(dir=SITE_CREDS_KEY_PATH.parent)
    try:
        os.write(fd, key)
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SITE_CREDS_KEY_PATH)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.chmod(SITE_CREDS_KEY_PATH, 0o600)
    return Fernet(key)


def _load() -> dict[str, dict[str, Any]]:
    if not SITE_CREDS_PATH.exists():
        return {}
    try:
        decrypted = _fernet().decrypt(SITE_CREDS_PATH.read_bytes())
        data = json.loads(decrypted.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        # Wrong/rotated key or corrupt file — treat as empty but say so.
        # A subsequent save overwrites the store, so surface the loss.
        log.warning(
            "site_credentials: could not decrypt %s — treating as empty",
            SITE_CREDS_PATH,
        )
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    SITE_CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _fernet().encrypt(json.dumps(data, indent=2).encode("utf-8"))
    fd, tmp = tempfile.mkstemp(dir=SITE_CREDS_PATH.parent)
    try:
        os.write(fd, encrypted)
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SITE_CREDS_PATH)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.chmod(SITE_CREDS_PATH, 0o600)


def _entry_to_credential(site: str, entry: dict[str, Any]) -> SiteCredential:
    return SiteCredential(
        site=site,
        username=str(entry.get("username", "")),
        password=str(entry.get("password", "")),
        created_at=entry.get("created_at"),
        updated_at=entry.get("updated_at"),
        last_used_at=entry.get("last_used_at"),
    )


def save(site: str, username: str, password: str) -> SiteCredential:
    """Store (or replace) the login for ``site``. Returns the saved entry."""
    key = normalize_site(site)
    if not key:
        raise ValueError(f"cannot normalize site {site!r}")
    if not username or not password:
        raise ValueError("username and password must be non-empty")
    now = datetime.now(timezone.utc).isoformat()
    data = _load()
    existing = data.get(key) or {}
    entry = {
        "username": username,
        "password": password,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "last_used_at": existing.get("last_used_at"),
    }
    data[key] = entry
    _save(data)
    return _entry_to_credential(key, entry)


def get(site: str) -> SiteCredential | None:
    key = normalize_site(site)
    if not key:
        return None
    entry = _load().get(key)
    if entry is None:
        return None
    return _entry_to_credential(key, entry)


def exists(site: str) -> bool:
    return get(site) is not None


def delete(site: str) -> bool:
    key = normalize_site(site)
    if not key:
        return False
    data = _load()
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def list_sites() -> list[dict[str, Any]]:
    """Listing for the agent tool and Settings UI. Never returns passwords."""
    out: list[dict[str, Any]] = []
    for key, entry in sorted(_load().items()):
        out.append(
            {
                "site": key,
                "username": entry.get("username", ""),
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
                "last_used_at": entry.get("last_used_at"),
            }
        )
    return out


def mark_used(site: str) -> None:
    """Best-effort ``last_used_at`` update after a successful fill."""
    key = normalize_site(site)
    if not key:
        return
    data = _load()
    entry = data.get(key)
    if entry is None:
        return
    entry["last_used_at"] = datetime.now(timezone.utc).isoformat()
    data[key] = entry
    try:
        _save(data)
    except Exception:  # noqa: BLE001 — telemetry only, never fail the fill
        log.debug("site_credentials: mark_used failed for %s", key)
