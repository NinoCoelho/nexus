"""Tests for the dispatch_card agent tool (logic-only, no real session store)."""

from __future__ import annotations

import json

import pytest

from nexus.tools.dispatch_card_tool import handle_dispatch_card_tool


@pytest.mark.asyncio
async def test_dispatch_card_no_dispatcher():
    out = json.loads(await handle_dispatch_card_tool({"path": "a.md"}, dispatcher=None))
    assert out["ok"] is False
    assert "dispatcher not wired" in out["error"]


@pytest.mark.asyncio
async def test_dispatch_card_missing_path():
    async def stub(**kw):
        return {}
    out = json.loads(await handle_dispatch_card_tool({}, dispatcher=stub))
    assert out["ok"] is False
    assert "`path` is required" in out["error"]


@pytest.mark.asyncio
async def test_dispatch_card_background_requires_card():
    async def stub(**kw):
        return {}
    out = json.loads(await handle_dispatch_card_tool(
        {"path": "a.md", "mode": "background"}, dispatcher=stub,
    ))
    assert out["ok"] is False
    assert "card_id" in out["error"]


@pytest.mark.asyncio
async def test_dispatch_card_invalid_mode():
    async def stub(**kw):
        return {}
    out = json.loads(await handle_dispatch_card_tool(
        {"path": "a.md", "mode": "weird"}, dispatcher=stub,
    ))
    assert out["ok"] is False
    assert "invalid mode" in out["error"]


@pytest.mark.asyncio
async def test_dispatch_card_default_mode_with_card():
    captured = {}

    async def stub(*, path, card_id, mode):
        captured["mode"] = mode
        return {"session_id": "s1", "path": path, "card_id": card_id, "mode": mode}

    out = json.loads(await handle_dispatch_card_tool(
        {"path": "a.md", "card_id": "c1"}, dispatcher=stub,
    ))
    assert out["ok"] is True
    assert out["session_id"] == "s1"
    # Default for card-bound dispatch is background
    assert captured["mode"] == "background"


@pytest.mark.asyncio
async def test_dispatch_card_default_mode_without_card():
    captured = {}

    async def stub(*, path, card_id, mode):
        captured["mode"] = mode
        return {"session_id": "s1", "seed_message": "x", "path": path, "card_id": None, "mode": mode}

    out = json.loads(await handle_dispatch_card_tool({"path": "a.md"}, dispatcher=stub))
    assert out["ok"] is True
    assert captured["mode"] == "chat"


@pytest.mark.asyncio
async def test_dispatch_card_propagates_value_error():
    async def stub(**kw):
        raise ValueError("bad input")
    out = json.loads(await handle_dispatch_card_tool({"path": "a.md"}, dispatcher=stub))
    assert out["ok"] is False
    assert "bad input" in out["error"]
