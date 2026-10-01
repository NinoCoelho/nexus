"""Coordinator (master chat) service + tool tests.

Exercises identity provisioning, the read-only sweep guard, tool gating
(only the coordinator session may inspect/dispatch), and inspect actions
against an isolated session store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import nexus.home as home
from nexus.coordinator import CoordinatorService, set_service, sweep_mode
from nexus.coordinator_tools import handle_nexus_sessions, handle_session_dispatch


class _StubAgent:
    pass


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(home, "_ROOT", tmp_path)
    home.set_user_home(None)
    # Point config load/save at an isolated file with the coordinator on.
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[coordinator]\nenabled = true\nsweep_interval_minutes = 120\n",
        encoding="utf-8",
    )
    import nexus.config_file as cf

    monkeypatch.setattr(cf, "CONFIG_PATH", cfg_file)
    with cf._config_cache_lock:
        cf._config_cache.clear()
    yield tmp_path
    set_service(None)
    with cf._config_cache_lock:
        cf._config_cache.clear()


def _service(tmp_path: Path) -> CoordinatorService:
    from nexus.server.session_store import SessionStore

    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    return CoordinatorService(store, _StubAgent(), tracker=None)


async def test_provisions_and_persists_master_session(isolated_home) -> None:
    svc = _service(isolated_home)
    sid = svc.ensure_session()
    assert svc.is_coordinator(sid)
    # Persisted to config: a fresh service instance sees the same id.
    assert _service(isolated_home).session_id == sid
    # Title set for the sidebar.
    assert svc.store.get(sid).title == "Master"


async def test_dispatch_refused_in_sweep_mode_and_for_self(isolated_home) -> None:
    svc = _service(isolated_home)
    sid = svc.ensure_session()
    with sweep_mode():
        res = await svc.dispatch(session_id="whatever", message="hi")
    assert res["ok"] is False
    assert "read-only" in res["error"]

    res = await svc.dispatch(session_id=sid, message="hi")
    assert res["ok"] is False
    assert "itself" in res["error"]

    res = await svc.dispatch(session_id="missing", message="hi")
    assert res["ok"] is False


async def test_tools_gated_to_coordinator_session(isolated_home) -> None:
    svc = _service(isolated_home)
    sid = svc.ensure_session()
    set_service(svc)
    other = svc.store.create()

    # Non-coordinator session: refused.
    res = json.loads(await handle_nexus_sessions({"action": "projects"}, other.id))
    assert res["ok"] is False
    res = json.loads(await handle_session_dispatch({"session_id": "x", "message": "m"}, other.id))
    assert res["ok"] is False

    # Coordinator session: inspect works.
    res = json.loads(await handle_nexus_sessions({"action": "projects"}, sid))
    assert res["ok"] is True
    assert res["projects"] == []

    res = json.loads(await handle_nexus_sessions({"action": "read", "session_id": other.id}, sid))
    assert res["ok"] is True
    assert res["tail"] == "(empty)"


async def test_inspect_lists_projects_and_sessions(isolated_home) -> None:
    from nexus.server.project_store import ProjectStore
    from nexus.server.session_store import SessionStore

    store = SessionStore(db_path=isolated_home / "sessions.sqlite")
    projects = ProjectStore(isolated_home / "sessions.sqlite")
    proj = projects.create(name="Alpha", description="desc")
    s1 = store.create(project_id=proj.id)
    s2 = store.create()

    svc = CoordinatorService(store, _StubAgent(), tracker=None)
    res = svc.inspect(action="projects")
    assert res["ok"] is True
    assert res["projects"][0]["name"] == "Alpha"
    assert res["projects"][0]["session_count"] == 1

    res = svc.inspect(action="sessions")
    ids = {s["id"] for s in res["sessions"]}
    assert ids == {s1.id, s2.id}

    res = svc.inspect(action="sessions", project_id=proj.id)
    assert [s["id"] for s in res["sessions"]] == [s1.id]


async def test_service_unavailable_without_wiring(isolated_home) -> None:
    set_service(None)
    res = json.loads(await handle_nexus_sessions({"action": "projects"}, "any"))
    assert res["ok"] is False
    assert "unavailable" in res["error"]
