"""Background dispatch visibility — scheduler-fired sessions must be hidden.

Calendar events and kanban lane auto-runs dispatch with ``mode="background"``.
Each recurrence creates a fresh session; if those were visible, every fire
would drop a new row into the chat list. These tests pin the contract:
background dispatch sessions are hidden + origin-tagged, while user-initiated
``chat`` dispatches stay visible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import nexus.vault as vault_module
from nexus.server.routes.vault_dispatch import _dispatch_impl
from nexus.server.session_store import SessionStore

CAL_MD = """---
calendar-plugin: basic
---

# Default

### Daily sweep
<!-- nx:id=fe8579ae-8e1f-4630-a3b9-0b9f1e24b2a4 -->
<!-- nx:start=2026-10-02 -->
<!-- nx:status=scheduled -->
<!-- nx:trigger=on_start -->
<!-- nx:rrule=FREQ=DAILY -->

Agent prompt: propose 2-3 concrete actions.
"""


@pytest.fixture(autouse=True)
def _vault_tmp(tmp_path: Path, monkeypatch):
    vault_root = tmp_path / "vault"
    (vault_root / "Calendars").mkdir(parents=True)
    monkeypatch.setattr(vault_module, "_VAULT_ROOT", vault_root)
    (vault_root / "Calendars" / "Default.md").write_text(CAL_MD, encoding="utf-8")
    return vault_root


class _StubAgent:
    _provider_registry = None
    _nexus_cfg = None


async def _run(mode: str, store: SessionStore) -> dict[str, Any]:
    captured: list[dict[str, Any]] = []

    async def stub(**kwargs):
        captured.append(kwargs)

    import nexus.server.routes.vault_dispatch_helpers as helpers

    orig = helpers.run_background_agent_turn
    helpers.run_background_agent_turn = stub
    try:
        result = await _dispatch_impl(
            path="Calendars/Default.md",
            card_id=None,
            event_id="fe8579ae-8e1f-4630-a3b9-0b9f1e24b2a4",
            mode=mode,
            a=_StubAgent(),
            store=store,
        )
    finally:
        helpers.run_background_agent_turn = orig
    result["_captured"] = captured
    return result


async def test_background_dispatch_is_hidden_and_tagged(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    result = await _run("background", store)
    sid = result["session_id"]

    # Hidden: excluded from every default chat listing.
    assert all(s.id != sid for s in store.list(100, 0))
    row = store.list(100, 0, include_hidden=True)
    assert any(s.id == sid for s in row)

    # Origin-tagged: monitorable as a calendar run.
    origin = store.get_origin(sid)
    assert origin == {
        "kind": "calendar",
        "source": "Calendars/Default.md",
        "run_id": "fe8579ae-8e1f-4630-a3b9-0b9f1e24b2a4",
    }


async def test_chat_dispatch_stays_visible(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    result = await _run("chat", store)
    sid = result["session_id"]
    assert any(s.id == sid for s in store.list(100, 0))
    assert store.get_origin(sid) is None
