"""Site login credentials (username + password per site), encrypted at rest.

Used by browser-control flows (the ``browser`` tool, the chrome-devtools CDP
skill, the ``page`` tool): the agent asks the user for a site login once via
a masked HITL form, stores it here, and later asks the *server* to fill it
into a browser. The plaintext password is never returned to the LLM — the
only readers are server-side fill code and the Settings UI (which only ever
sees usernames).

Storage shape (Fernet-encrypted JSON at ``~/.nexus/site_credentials.db``,
key at ``~/.nexus/keys/site_credentials.key``, both mode 0600). **v2 —
multiple logins per site** (one entry per ``(site, username)`` pair)::

    {
      "github.com": [
        {
          "username": "user@example.com",
          "password": "hunter2",
          "created_at": "2026-09-28T...",
          "updated_at": "2026-09-28T...",
          "last_used_at": null
        },
        { "username": "second@example.com", ... }
      ]
    }

v1 files (``{site: {entry}}`` — one login per site) are migrated on load:
the dict entry becomes a single-element list, persisted on the next save.

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


def _load() -> dict[str, list[dict[str, Any]]]:
    """Load the store; migrates v1 single-entry shape to the v2 list shape."""
    if not SITE_CREDS_PATH.exists():
        return {}
    try:
        decrypted = _fernet().decrypt(SITE_CREDS_PATH.read_bytes())
        data = json.loads(decrypted.decode("utf-8"))
        if not isinstance(data, dict):
            return {}
    except Exception:
        # Wrong/rotated key or corrupt file — treat as empty but say so.
        # A subsequent save overwrites the store, so surface the loss.
        log.warning(
            "site_credentials: could not decrypt %s — treating as empty",
            SITE_CREDS_PATH,
        )
        return {}
    migrated: dict[str, list[dict[str, Any]]] = {}
    for site, value in data.items():
        if isinstance(value, dict):  # v1: one entry per site
            migrated[site] = [value]
        elif isinstance(value, list):  # v2: entry per (site, username)
            migrated[site] = [e for e in value if isinstance(e, dict)]
    return migrated


def _save(data: dict[str, list[dict[str, Any]]]) -> None:
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


def _pick_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Default credential for a site with several: the most recently
    updated (ISO-8601 strings sort lexicographically); ties → first."""
    if not entries:
        return None
    return max(
        enumerate(entries),
        key=lambda pair: (pair[1].get("updated_at") or "", -pair[0]),
    )[1]


def save(site: str, username: str, password: str) -> SiteCredential:
    """Store (or replace, keyed by site+username) a login. A new username
    for an existing site adds a second credential — that's the point."""
    key = normalize_site(site)
    if not key:
        raise ValueError(f"cannot normalize site {site!r}")
    if not username or not password:
        raise ValueError("username and password must be non-empty")
    now = datetime.now(timezone.utc).isoformat()
    data = _load()
    entries = data.get(key) or []
    existing = next((e for e in entries if e.get("username") == username), None)
    entry = {
        "username": username,
        "password": password,
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "last_used_at": (existing or {}).get("last_used_at"),
    }
    if existing is not None:
        entries[entries.index(existing)] = entry
    else:
        entries.append(entry)
    data[key] = entries
    _save(data)
    return _entry_to_credential(key, entry)


def usernames(site: str) -> list[str]:
    """Saved usernames for a site, most recently updated first (so
    disambiguation prompts lead with the default credential)."""
    key = normalize_site(site)
    if not key:
        return []
    entries = _load().get(key) or []
    ordered = sorted(entries, key=lambda e: e.get("updated_at") or "", reverse=True)
    return [str(e.get("username", "")) for e in ordered if e.get("username")]


def get(site: str, username: str | None = None) -> SiteCredential | None:
    """Resolve a credential. With ``username``: that exact entry. Without:
    the site's default (most recently updated) — backward compatible."""
    key = normalize_site(site)
    if not key:
        return None
    entries = _load().get(key) or []
    if username is not None:
        entry = next((e for e in entries if e.get("username") == username), None)
    else:
        entry = _pick_entry(entries)
    if entry is None:
        return None
    return _entry_to_credential(key, entry)


def exists(site: str) -> bool:
    return get(site) is not None


def delete(site: str, username: str | None = None) -> bool:
    """Delete one credential (site+username) or, without a username, every
    credential for the site."""
    key = normalize_site(site)
    if not key:
        return False
    data = _load()
    entries = data.get(key)
    if not entries:
        return False
    if username is None:
        del data[key]
    else:
        remaining = [e for e in entries if e.get("username") != username]
        if len(remaining) == len(entries):
            return False
        if remaining:
            data[key] = remaining
        else:
            del data[key]
    _save(data)
    return True


def list_sites() -> list[dict[str, Any]]:
    """Listing for the agent tool and Settings UI — one row per credential
    (site × username). Never returns passwords."""
    out: list[dict[str, Any]] = []
    data = _load()
    for key in sorted(data):
        for entry in sorted(
            data[key], key=lambda e: e.get("updated_at") or "", reverse=True
        ):
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


def mark_used(site: str, username: str | None = None) -> None:
    """Best-effort ``last_used_at`` update after a successful fill."""
    key = normalize_site(site)
    if not key:
        return
    data = _load()
    entries = data.get(key)
    if not entries:
        return
    if username is not None:
        entry = next((e for e in entries if e.get("username") == username), None)
    else:
        entry = _pick_entry(entries)
    if entry is None:
        return
    entry["last_used_at"] = datetime.now(timezone.utc).isoformat()
    try:
        _save(data)
    except Exception:  # noqa: BLE001 — telemetry only, never fail the fill
        log.debug("site_credentials: mark_used failed for %s", key)
