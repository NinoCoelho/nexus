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
