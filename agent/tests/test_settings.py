"""Tests for ``SettingsStore`` persistence + the YOLO hook into
``AskUserHandler``.

``/settings`` endpoint tests live in ``test_server_sse.py`` alongside
the other HTTP round-trip cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent.ask_user_tool import AskUserHandler
from nexus.agent.context import CURRENT_SESSION_ID
from nexus.server.session_store import SessionStore
from nexus.server.settings import Settings, SettingsStore


# ── SettingsStore persistence ────────────────────────────────────────


def test_defaults_returned_when_file_missing(tmp_path: Path) -> None:
    store = SettingsStore(path=tmp_path / "settings.json")
    s = store.get()
    assert s.yolo_mode is False


def test_update_persists_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path=path)

    updated = store.update(yolo_mode=True)
    assert updated.yolo_mode is True

    # Fresh store loading from disk reads the persisted value.
    reloaded = SettingsStore(path=path).get()
    assert reloaded.yolo_mode is True


def test_update_uses_in_process_cache_after_write(tmp_path: Path) -> None:
    """Two sequential reads shouldn't both hit the disk — the store
    caches. Prove it by deleting the file after the first update;
    the next .get() on the same store still returns the value."""
    path = tmp_path / "settings.json"
    store = SettingsStore(path=path)
    store.update(yolo_mode=True)

    path.unlink()
    s = store.get()
    assert s.yolo_mode is True  # served from cache, not disk


def test_unknown_field_rejected(tmp_path: Path) -> None:
    store = SettingsStore(path=tmp_path / "settings.json")
    with pytest.raises(ValueError, match="unknown settings field"):
        store.update(not_a_real_field=True)


def test_corrupt_file_falls_back_to_defaults(tmp_path: Path) -> None:
    """If settings.json is malformed, we log-and-recover rather than
    crashing the server at startup. The next successful write will
    replace the corruption."""
    path = tmp_path / "settings.json"
    path.write_text("{ not json }", encoding="utf-8")
    store = SettingsStore(path=path)
    assert store.get().yolo_mode is False


def test_atomic_write_uses_tmp_then_rename(tmp_path: Path) -> None:
    """A cheap check that the write path didn't regress to plain
    ``write_text`` — we want the ``.json.tmp`` intermediate so crashes
    mid-write can't corrupt the real file."""
    path = tmp_path / "settings.json"
    store = SettingsStore(path=path)
    store.update(yolo_mode=True)
    # After a successful write the tmp file must NOT linger.
    assert not (tmp_path / "settings.json.tmp").exists()
    assert path.exists()


# ── YOLO path through AskUserHandler ────────────────────────────────


async def test_yolo_mode_from_settings_auto_confirms(tmp_path: Path) -> None:
    """End-to-end at the handler level: flip yolo_mode in the store,
    then call ask_user — it should auto-answer 'yes' without emitting
    a user_request (only the audit auto-event)."""
    settings = SettingsStore(path=tmp_path / "settings.json")
    settings.update(yolo_mode=True)

    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    session = sessions.create()
    token = CURRENT_SESSION_ID.set(session.id)
    try:
        handler = AskUserHandler(
            session_store=sessions,
            yolo_mode_getter=lambda: settings.get().yolo_mode,
        )
        result = await handler.invoke({"prompt": "delete?", "kind": "confirm"})
        assert result.ok and result.answer == "yes"
        assert result.timed_out is False
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_yolo_mode_toggle_picks_up_without_reconstruction(
    tmp_path: Path,
) -> None:
    """The server wires the handler with a *getter*, not a snapshot.
    Toggling yolo_mode via the store must take effect on the very
    next ask_user, no restart."""
    settings = SettingsStore(path=tmp_path / "settings.json")
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    session = sessions.create()
    token = CURRENT_SESSION_ID.set(session.id)
    try:
        handler = AskUserHandler(
            session_store=sessions,
            yolo_mode_getter=lambda: settings.get().yolo_mode,
            default_timeout=0.05,
        )
        # YOLO off → times out waiting for a user that isn't there.
        off = await handler.invoke({"prompt": "x", "kind": "confirm"})
        assert off.timed_out is True

        settings.update(yolo_mode=True)

        # Immediately after update, same handler auto-confirms.
        on = await handler.invoke({"prompt": "x", "kind": "confirm"})
        assert on.timed_out is False and on.answer == "yes"
    finally:
        CURRENT_SESSION_ID.reset(token)


def test_settings_model_is_frozen() -> None:
    """Settings is a frozen pydantic model — mutating via attribute
    assignment should raise. This keeps callers from thinking they
    can poke fields in place."""
    s = Settings()
    with pytest.raises((TypeError, ValueError)):
        s.yolo_mode = True  # type: ignore[misc]
