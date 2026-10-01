"""Tests for the Telegram management routes (status / start / stop).

Handlers are invoked directly with a stubbed ``request`` (the routes only
touch ``request.app.state``), and the Telegram client class is faked so no
network is touched. Config reads are isolated from the user's real
``~/.nexus/config.toml``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from nexus.config_schema import TelegramConfig
from nexus.telegram.poller import build_telegram_poller


class FakeClient:
    """Stands in for TelegramClient — get_updates blocks like a long-poll."""

    instances: list["FakeClient"] = []

    def __init__(self, token: str, **kwargs: Any) -> None:
        self.token = token
        self._release = asyncio.Event()
        FakeClient.instances.append(self)

    @classmethod
    def from_config(cls, cfg: Any) -> "FakeClient | None":
        if not getattr(cfg, "_fake_token", ""):
            return None
        return cls(cfg._fake_token)

    async def get_me(self) -> dict:
        return {"username": "testbot", "id": 1}

    async def get_updates(self, *, offset: int, timeout_seconds: int) -> list:
        await self._release.wait()
        return []

    async def aclose(self) -> None:
        self._release.set()

    async def send_text_safe(self, *a: Any, **k: Any) -> int:
        return 1

    async def edit_text_safe(self, *a: Any, **k: Any) -> bool:
        return True

    async def send_chat_action(self, *a: Any, **k: Any) -> None:
        pass

    async def answer_callback_query(self, *a: Any, **k: Any) -> None:
        pass


def _app(cfg: TelegramConfig) -> SimpleNamespace:
    """Fake FastAPI app: state only, as used by the routes."""
    sessions = SimpleNamespace(publish=lambda *a, **k: None, _db_path=None)
    return SimpleNamespace(
        state=SimpleNamespace(
            telegram_poller=None,
            agent=None,
            sessions=sessions,
            job_tracker=None,
        ),
        _tg_cfg=cfg,
    )


def _request(app: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(app=app)


@pytest.fixture
def isolated_config(monkeypatch: pytest.MonkeyPatch):
    """Point the telegram route's config reads at a TelegramConfig we control."""

    def _make(cfg: TelegramConfig) -> None:
        holder = SimpleNamespace(telegram=cfg)
        monkeypatch.setattr(
            "nexus.server.routes.telegram.load_config", lambda: holder
        )

    return _make


@pytest.fixture
def fake_client_cls(monkeypatch: pytest.MonkeyPatch):
    FakeClient.instances = []
    monkeypatch.setattr("nexus.telegram.api.TelegramClient", FakeClient)
    # from_config consults resolve(); fake it via a token attribute on cfg.
    monkeypatch.setattr(
        "nexus.telegram.api.resolve",
        lambda name: "fake-token" if name.endswith("TOKEN") else None,
    )
    return FakeClient


async def test_status_reports_disabled_no_poller(isolated_config) -> None:
    from nexus.server.routes.telegram import telegram_status

    isolated_config(TelegramConfig(enabled=False))
    app = _app(TelegramConfig(enabled=False))
    status = await telegram_status(_request(app))
    assert status["enabled"] is False
    assert status["running"] is False
    assert status["bot_username"] is None
    assert status["token_env"] == "TELEGRAM_BOT_TOKEN"


async def test_start_without_token_400(isolated_config, monkeypatch) -> None:
    from fastapi import HTTPException

    from nexus.server.routes.telegram import telegram_start

    cfg = TelegramConfig(enabled=True)
    cfg._fake_token = ""  # type: ignore[attr-defined]
    isolated_config(cfg)
    monkeypatch.setattr("nexus.telegram.api.TelegramClient", FakeClient)

    app = _app(cfg)
    with pytest.raises(HTTPException) as exc:
        await telegram_start(_request(app))
    assert exc.value.status_code == 400
    assert "TELEGRAM_BOT_TOKEN" in exc.value.detail


async def test_start_validates_token_and_runs(isolated_config, fake_client_cls) -> None:
    from nexus.server.routes.telegram import telegram_start, telegram_status

    cfg = TelegramConfig(enabled=True, allowed_user_ids=[42])
    cfg._fake_token = "fake-token"  # type: ignore[attr-defined]
    isolated_config(cfg)
    app = _app(cfg)

    status = await telegram_start(_request(app))
    assert status["running"] is True
    assert status["bot_username"] == "testbot"

    poller = app.state.telegram_poller
    assert poller is not None and poller.bot_info["username"] == "testbot"

    # Fresh status read reflects the same state.
    status2 = await telegram_status(_request(app))
    assert status2["running"] is True

    await poller.stop()
    app.state.telegram_poller = None


async def test_stop_is_idempotent(isolated_config, fake_client_cls) -> None:
    from nexus.server.routes.telegram import telegram_start, telegram_stop

    cfg = TelegramConfig(enabled=True)
    cfg._fake_token = "fake-token"  # type: ignore[attr-defined]
    isolated_config(cfg)
    app = _app(cfg)

    await telegram_start(_request(app))
    status = await telegram_stop(_request(app))
    assert status["running"] is False
    assert app.state.telegram_poller is None

    # Second stop is a no-op.
    status2 = await telegram_stop(_request(app))
    assert status2["running"] is False


async def test_build_poller_returns_none_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nexus.telegram.api.resolve", lambda name: None)
    assert (
        build_telegram_poller(
            cfg=TelegramConfig(),
            agent=None,
            store=None,
            tracker=None,
        )
        is None
    )


async def test_poller_disables_on_401_and_status_surfaces_error(
    tmp_path, isolated_config, fake_client_cls
) -> None:
    from nexus.server.routes.telegram import telegram_status
    from nexus.server.session_store import SessionStore
    from nexus.telegram.api import TelegramError
    from nexus.telegram.poller import TelegramPoller

    class RejectingClient(FakeClient):
        async def get_updates(self, *, offset: int, timeout_seconds: int) -> list:
            raise TelegramError(
                "getUpdates failed (401): Unauthorized", status_code=401
            )

    cfg = TelegramConfig(enabled=True, allowed_user_ids=[42])
    isolated_config(cfg)
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    poller = TelegramPoller(
        client=RejectingClient("revoked"),
        agent=None,
        store=store,
        tracker=None,
        cfg=cfg,
    )
    poller.start()
    assert poller._task is not None
    await asyncio.wait_for(poller._task, timeout=5.0)

    # The poller self-disabled instead of retrying forever, and the error
    # is surfaced through GET /telegram/status.
    assert poller.running is False
    assert "401" in (poller.last_error or "")
    app = _app(cfg)
    app.state.telegram_poller = poller
    status = await telegram_status(_request(app))
    assert status["running"] is False
    assert status["error"] is not None and "401" in status["error"]


async def test_bindings_lists_with_project_names(tmp_path) -> None:
    """GET /telegram/bindings — rows enriched with project names, tolerant
    of dangling project/session references."""
    import nexus.home as home
    from nexus.server.project_store import ProjectStore
    from nexus.telegram.bindings import TelegramBindingStore

    home.set_user_home(tmp_path)
    try:
        # The projects table is created by the session-store schema; boot it
        # first so both stores share the same DB file.
        from nexus.server.session_store import SessionStore

        SessionStore(db_path=tmp_path / "sessions.sqlite")
        bindings_db = TelegramBindingStore()
        projects = ProjectStore(tmp_path / "sessions.sqlite")
        proj = projects.create(name="Alpha")

        bindings_db.upsert(
            chat_id=1,
            thread_id=7,
            kind="topic",
            project_id=proj.id,
            active_session_id="sess-1",
        )
        bindings_db.upsert(
            chat_id=2, kind="dm", project_id=None, active_session_id="sess-2"
        )

        from nexus.server.routes.telegram import telegram_bindings

        app = _app(TelegramConfig())
        app.state.sessions = SimpleNamespace(
            get=lambda sid: None, publish=lambda *a, **k: None
        )
        res = await telegram_bindings(_request(app))
        assert {b["chat_id"] for b in res} == {1, 2}

        alpha = next(b for b in res if b["chat_id"] == 1)
        assert alpha["kind"] == "topic"
        assert alpha["project_id"] == proj.id
        assert alpha["project_name"] == "Alpha"
        # Dangling session reference degrades to null, not an error.
        assert alpha["active_session_title"] is None

        dm = next(b for b in res if b["chat_id"] == 2)
        assert dm["project_name"] is None
    finally:
        home.set_user_home(None)


async def test_find_by_session_context_fallback(tmp_path) -> None:
    """After /switch, sessions created through a binding still route their
    HITL prompts to that binding via the Telegram:* context prefix."""
    from nexus.server.session_store import SessionStore
    from nexus.telegram.bindings import TelegramBindingStore

    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    bindings = TelegramBindingStore(tmp_path / "sessions.sqlite")

    active = store.create(context="Telegram: topic 11/7")
    old = store.create(context="Telegram: topic 11/7")
    bindings.upsert(
        chat_id=11, thread_id=7, kind="topic",
        project_id=None, active_session_id=active.id,
    )

    # Active session: direct hit.
    assert bindings.find_by_session(active.id) is not None
    # Switched-away session: context-prefix fallback still finds the binding.
    found = bindings.find_by_session(old.id)
    assert found is not None and found.chat_id == 11 and found.thread_id == 7
    # Unrelated session: no binding.
    other = store.create(context="Plain web session")
    assert bindings.find_by_session(other.id) is None


async def test_patch_and_delete_binding_endpoints(tmp_path) -> None:
    from nexus.server.project_store import ProjectStore
    from nexus.server.routes.telegram import (
        delete_telegram_binding,
        patch_telegram_binding,
        telegram_bindings,
    )
    from nexus.server.session_store import SessionStore
    from nexus.telegram.bindings import TelegramBindingStore

    import nexus.home as home

    home.set_user_home(tmp_path)
    try:
        store = SessionStore(db_path=tmp_path / "sessions.sqlite")
        bindings = TelegramBindingStore()
        projects = ProjectStore(tmp_path / "sessions.sqlite")
        proj = projects.create(name="Beta")
        session = store.create(context="Telegram: dm 42")
        bindings.upsert(
            chat_id=42, thread_id=0, kind="dm",
            project_id=None, active_session_id=session.id,
        )

        app = _app(TelegramConfig())
        app.state.sessions = store

        # Rebind to project Beta.
        res = await patch_telegram_binding(
            app, {"chat_id": 42, "thread_id": 0, "project_id": proj.id}
        )
        assert res == {"ok": True}
        row = bindings.get(42, 0)
        assert row is not None and row.project_id == proj.id

        # Unknown project → 404.
        with pytest.raises(Exception):
            await patch_telegram_binding(
                app, {"chat_id": 42, "thread_id": 0, "project_id": "nope"}
            )

        # Listing reflects the enrichment.
        listed = await telegram_bindings(SimpleNamespace(app=app))
        assert listed[0]["project_name"] == "Beta"

        # Unbind.
        res = await delete_telegram_binding({"chat_id": 42, "thread_id": 0})
        assert res == {"ok": True}
        assert bindings.get(42, 0) is None
    finally:
        home.set_user_home(None)
