"""Read-only session share links.

Issues a signed token for a session (HMAC-SHA256 over ``session_id``
with a secret stored at ``~/.nexus/share_secret``). The token is opaque
to the UI; the server verifies it on every read.

Sharing is opt-in per session: a row in the ``session_share`` table
records the issued token. Revocation deletes the row. Verification
also requires the row to exist, so a stolen token can be killed
without rotating the global secret.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from ..deps import get_sessions
from ..session_store import SessionStore

router = APIRouter()

_SECRET_PATH = Path("~/.nexus/share_secret").expanduser()


def _get_secret() -> bytes:
    """Load (or create) the HMAC secret used for share-link signing."""
    if not _SECRET_PATH.exists():
        _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_PATH.write_bytes(_secrets.token_bytes(32))
        try:
            _SECRET_PATH.chmod(0o600)
        except OSError:
            pass
    return _SECRET_PATH.read_bytes()


def _sign(session_id: str, nonce: str) -> str:
    msg = f"{session_id}:{nonce}".encode("utf-8")
    return hmac.new(_get_secret(), msg, hashlib.sha256).hexdigest()[:32]


def _split_token(token: str) -> tuple[str, str, str] | None:
    """Decode ``<session_id>.<nonce>.<sig>`` triple. Returns None on malformed."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    sid, nonce, sig = parts
    if not sid or not nonce or not sig:
        return None
    return sid, nonce, sig


def _ensure_share_table(store: SessionStore) -> None:
    store._loom._db.executescript(
        """
        CREATE TABLE IF NOT EXISTS session_share (
            session_id TEXT PRIMARY KEY,
            nonce      TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


@router.post("/sessions/{session_id}/share")
async def create_share(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Mint (or rotate) a share token for a session. Returns ``{token, path}``."""
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _ensure_share_table(store)
    nonce = _secrets.token_urlsafe(12)
    store._loom._db.execute(
        "INSERT INTO session_share (session_id, nonce) VALUES (?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET nonce = excluded.nonce, "
        "created_at = CURRENT_TIMESTAMP",
        (session_id, nonce),
    )
    store._loom._db.commit()
    sig = _sign(session_id, nonce)
    token = f"{session_id}.{nonce}.{sig}"
    return {"token": token, "path": f"#/share/{token}"}


@router.delete("/sessions/{session_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share(
    session_id: str,
    store: SessionStore = Depends(get_sessions),
) -> None:
    _ensure_share_table(store)
    store._loom._db.execute(
        "DELETE FROM session_share WHERE session_id = ?", (session_id,)
    )
    store._loom._db.commit()


@router.get("/share/{token}")
async def read_share(
    token: str,
    store: SessionStore = Depends(get_sessions),
) -> dict:
    """Read-only fetch of a shared session. No auth — token is the credential.

    Returns the session title, timestamps, and a filtered message list
    (user + assistant only; tool messages and tool_calls are stripped).
    Returns 404 (not 401/403) for missing or revoked tokens to avoid
    leaking which session ids exist.
    """
    parts = _split_token(token)
    if parts is None:
        raise HTTPException(status_code=404, detail="Invalid share link")
    sid, nonce, sig = parts
    expected = _sign(sid, nonce)
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=404, detail="Invalid share link")

    _ensure_share_table(store)
    row = store._loom._db.execute(
        "SELECT nonce, created_at FROM session_share WHERE session_id = ?", (sid,)
    ).fetchone()
    if row is None or row[0] != nonce:
        raise HTTPException(status_code=404, detail="Share link revoked or missing")

    session = store.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Session no longer exists")

    ts_list: list[int] = getattr(session, "_message_timestamps", []) or []

    def _iso(ts: int | None) -> str | None:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    messages = []
    for i, m in enumerate(session.history):
        role = str(m.role.value if hasattr(m.role, "value") else m.role)
        if role not in ("user", "assistant"):
            continue
        content = (m.content or "").strip()
        if not content:
            continue
        messages.append({
            "role": role,
            "content": content,
            "created_at": _iso(ts_list[i] if i < len(ts_list) else None),
        })

    return {
        "title": session.title,
        "shared_at": row[1],
        "now": int(time.time()),
        "messages": messages,
    }
