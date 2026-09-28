"""Tests for the ``site_credentials`` tool — masked HITL capture, server-side
fill dispatch (page + CDP surfaces), and the never-expose-the-password rule.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from nexus import site_credentials as sc
from nexus.agent.context import CURRENT_SESSION_ID
from nexus.agent.site_credentials_tool import (
    SITE_CREDENTIALS_TOOL,
    SiteCredentialsHandler,
)
from nexus.server.session_store import SessionStore


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "SITE_CREDS_PATH", tmp_path / "site_credentials.db")
    monkeypatch.setattr(sc, "SITE_CREDS_KEY_PATH", tmp_path / "keys" / "site_credentials.key")


class _FakePage:
    """Captures programmatic fill_login calls (no panel round-trip)."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result or {"ok": True, "filled": ["username", "password"], "submitted": True}

    async def invoke(self, args: dict[str, Any]) -> str:
        self.calls.append(args)
        return json.dumps(self._result)


class _FakeAskUser:
    def __init__(self, answer: Any = "true") -> None:
        self._answer = answer

    async def invoke(self, args: dict[str, Any]) -> Any:
        class _R:
            ok = True
            timed_out = False
            answer = self._answer
            error = None

        return _R()


def _handler(
    store: SessionStore, page: _FakePage | None = None, ask_user: Any | None = None
) -> SiteCredentialsHandler:
    return SiteCredentialsHandler(session_store=store, ask_user=ask_user, page=page)


def _bind_session(store: SessionStore) -> tuple[str, object]:
    s = store.create()
    token = CURRENT_SESSION_ID.set(s.id)
    return s.id, token


async def _form_answerer(
    store: SessionStore, sid: str, answer: str
) -> asyncio.Task:
    """Resolve the first user_request form on the stream with `answer`."""

    async def run() -> None:
        async for ev in store.subscribe(sid):
            if ev.kind == "user_request":
                store.resolve_pending(sid, ev.data["request_id"], answer)
                return

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    return task


async def test_spec_shape() -> None:
    assert SITE_CREDENTIALS_TOOL.name == "site_credentials"
    assert SITE_CREDENTIALS_TOOL.parameters["required"] == ["action"]
    assert set(SITE_CREDENTIALS_TOOL.parameters["properties"]["action"]["enum"]) == {
        "list", "save", "fill", "delete",
    }


async def test_list_action(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    sc.save("example.com", "alice", "hunter2")
    result = json.loads(await handler.invoke({"action": "list"}))
    assert result["ok"] is True
    assert result["sites"][0]["site"] == "example.com"
    assert "hunter2" not in json.dumps(result)


async def test_save_happy_path_masks_and_stores(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    sid, token = _bind_session(store)
    try:
        seen: dict[str, Any] = {}

        async def run() -> None:
            async for ev in store.subscribe(sid):
                if ev.kind == "user_request":
                    seen.update(ev.data)
                    store.resolve_pending(
                        sid,
                        ev.data["request_id"],
                        json.dumps({"username": "alice", "password": "hunter2"}),
                    )
                    return

        consumer = asyncio.create_task(run())
        await asyncio.sleep(0)

        result = json.loads(
            await handler.invoke(
                {"action": "save", "site": "https://www.example.com/login", "reason": "test"}
            )
        )
        await asyncio.wait_for(consumer, timeout=2.0)

        # Form shape: password field masked, never parkable kind surprises.
        assert seen["kind"] == "form"
        fields = {f["name"]: f for f in seen["fields"]}
        assert fields["password"]["secret"] is True
        assert fields["username"]["secret"] is False

        # Result carries no password; store has the login encrypted.
        assert result["ok"] is True
        assert result["site"] == "example.com"
        assert result["username"] == "alice"
        assert "hunter2" not in json.dumps(result)
        cred = sc.get("example.com")
        assert cred is not None and cred.password == "hunter2"
        assert b"hunter2" not in sc.SITE_CREDS_PATH.read_bytes()
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_save_timeout_publishes_cancelled(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    sid, token = _bind_session(store)
    try:
        seen: list[str] = []

        async def watch() -> None:
            async for ev in store.subscribe(sid):
                if ev.kind in ("user_request", "user_request_cancelled"):
                    seen.append(ev.kind)
                    if ev.kind == "user_request_cancelled":
                        return

        watcher = asyncio.create_task(watch())
        await asyncio.sleep(0)

        import nexus.agent.site_credentials_tool as mod

        old = mod._SAVE_TIMEOUT_SECONDS
        mod._SAVE_TIMEOUT_SECONDS = 0.05
        try:
            result = json.loads(await handler.invoke({"action": "save", "site": "example.com"}))
        finally:
            mod._SAVE_TIMEOUT_SECONDS = old
        await asyncio.wait_for(watcher, timeout=2.0)

        assert result["ok"] is False
        assert "timed out" in result["error"]
        assert seen == ["user_request", "user_request_cancelled"]
        assert sc.exists("example.com") is False
    finally:
        CURRENT_SESSION_ID.reset(token)


async def test_save_requires_site_and_guards_existing(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    result = json.loads(await handler.invoke({"action": "save"}))
    assert result["ok"] is False
    assert "site" in result["error"]

    sc.save("example.com", "alice", "hunter2")
    result = json.loads(await handler.invoke({"action": "save", "site": "example.com"}))
    assert result["ok"] is False
    assert "already saved" in result["error"]


async def test_fill_via_page_surface(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    page = _FakePage()
    handler = _handler(store, page=page)
    sc.save("example.com", "alice", "hunter2")

    result = json.loads(
        await handler.invoke(
            {
                "action": "fill",
                "site": "https://example.com/login",
                "surface": "page",
                "selectors": {"user": "#email", "pass": "#pass"},
                "tab": "example",
            }
        )
    )
    assert result["ok"] is True
    # The page handler received the resolved credential + selectors...
    call = page.calls[0]
    assert call["action"] == "fill_login"
    assert call["username"] == "alice"
    assert call["password"] == "hunter2"
    assert call["user_selector"] == "#email"
    assert call["pass_selector"] == "#pass"
    assert call["site"] == "example.com"
    # ...but the tool result the LLM sees does not.
    assert "hunter2" not in json.dumps(result)
    # last_used tracked
    cred = sc.get("example.com")
    assert cred is not None and cred.last_used_at is not None


async def test_fill_via_cdp_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    sc.save("example.com", "alice", "hunter2")

    captured: dict[str, Any] = {}

    async def _fake_fill(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True, "site": "example.com", "filled": ["password"], "submitted": False}

    import nexus.cdp_fill as cdp_fill

    monkeypatch.setattr(cdp_fill, "cdp_fill_login", _fake_fill)

    result = json.loads(
        await handler.invoke({"action": "fill", "site": "example.com", "surface": "cdp", "port": 9224})
    )
    assert result["ok"] is True
    assert "hunter2" not in json.dumps(result)
    assert captured["site"] == "example.com"
    assert captured["password"] == "hunter2"  # server-side resolution
    assert captured["port"] == 9224


async def test_fill_without_saved_login(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    result = json.loads(await handler.invoke({"action": "fill", "site": "nope.com"}))
    assert result["ok"] is False
    assert "no saved login" in result["error"]


async def test_fill_unreachable_cdp_returns_clean_error(
    tmp_path: Path,
) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    sc.save("example.com", "alice", "hunter2")
    result = json.loads(
        await handler.invoke(
            {"action": "fill", "site": "example.com", "surface": "cdp", "port": 59999}
        )
    )
    assert result["ok"] is False
    assert "9223" in result["error"] or "CDP" in result["error"] or "debug Chrome" in result["error"]


async def test_delete_with_confirm(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store, ask_user=_FakeAskUser(answer="true"))
    sc.save("example.com", "alice", "hunter2")
    result = json.loads(await handler.invoke({"action": "delete", "site": "example.com"}))
    assert result["ok"] is True
    assert result["deleted"] is True
    assert sc.exists("example.com") is False


async def test_delete_cancelled(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store, ask_user=_FakeAskUser(answer="false"))
    sc.save("example.com", "alice", "hunter2")
    result = json.loads(await handler.invoke({"action": "delete", "site": "example.com"}))
    assert result["ok"] is False
    assert sc.exists("example.com") is True


async def test_unknown_action(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    handler = _handler(store)
    result = json.loads(await handler.invoke({"action": "explode"}))
    assert result["ok"] is False
    assert "unknown action" in result["error"]
