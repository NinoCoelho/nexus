"""Tests for the bots conversion: mentions, archive filter, persona/handle backfill."""

from __future__ import annotations

import json

from pathlib import Path

from nexus.agent.bots import (
    attribute_bot_replies,
    parse_mentions,
    resolve_mention_project,
    resolve_mention_target,
)
from nexus.server.project_store import ProjectStore
from nexus.server.session_store import SessionStore

_NICO = {"id": "main", "handle": "nico", "name": "Nico"}


def _ps(tmp_path: Path) -> ProjectStore:
    SessionStore(db_path=tmp_path / "sessions.sqlite")  # ensures schema exists
    return ProjectStore(tmp_path / "sessions.sqlite")


def test_parse_mentions_finds_handles() -> None:
    assert parse_mentions("hey @research do it") == ["research"]
    assert parse_mentions("@a and @b-two") == ["a", "b-two"]
    assert parse_mentions("email me at x@y.com") == []  # mid-word @ is not a mention
    assert parse_mentions("(via @bot)") == ["bot"]
    assert parse_mentions("") == []


def test_resolve_mention_project_reroutes_except_self(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    a = ps.create(name="Research Bot")
    b = ps.create(name="Writer")
    assert a.handle == "research-bot"
    assert b.handle == "writer"

    msg = "please @research-bot summarize this"
    hit = resolve_mention_project(msg, own_project_id=None, project_store=ps)
    assert hit is not None and hit.id == a.id

    # Self-mention must not reroute.
    assert resolve_mention_project(msg, own_project_id=a.id, project_store=ps) is None
    # main is the coordinator id, not a project — treated as no-op here
    # (coordinator matching is suppressd in the project-only wrapper).
    assert resolve_mention_project("@main hi", own_project_id=b.id, project_store=ps) is None


def test_resolve_mention_target_routes_coordinator_by_name(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    other = ps.create(name="Other Bot")

    # @nico (the coordinator's name-derived handle) routes to the coordinator.
    target = resolve_mention_target(
        "@nico update the altar persona", own_project_id=other.id,
        project_store=ps, coordinator=_NICO,
    )
    assert target == ("coordinator", _NICO)

    # A bot mention beats a colliding coordinator handle (explicit wins).
    clash = ps.create(name="Nico")  # reserved-slug guard would suffix it…
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "sessions.sqlite"))
    conn.execute("UPDATE projects SET handle = 'nico' WHERE id = ?", (clash.id,))
    conn.commit()
    conn.close()  # …so force the collision, as a pre-guard project would have
    target = resolve_mention_target(
        "@nico hi", own_project_id=other.id, project_store=ps, coordinator=_NICO,
    )
    assert target is not None and target[0] == "bot" and target[1].id == clash.id

    # coordinator=False suppresses coordinator routing entirely (no project
    # holds the reserved "main" handle, so nothing matches).
    assert resolve_mention_target(
        "@main hi", own_project_id=other.id, project_store=ps, coordinator=False,
    ) is None


def test_attribute_marks_coordinator_replies_except_in_master(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    other = ps.create(name="Other Bot")
    msgs = [
        {"role": "user", "content": "@nico pode aplicar?"},
        {"role": "assistant", "content": "claro"},
        {"role": "user", "content": "obrigado"},
        {"role": "assistant", "content": "às ordens"},
    ]
    # In another bot's chat: the coordinator's reply is attributed.
    out = attribute_bot_replies(
        msgs, own_project_id=other.id, project_store=ps, is_master=False
    )
    assert out[1]["bot"] == _NICO
    assert out[3]["bot"] is None
    # In the master chat itself: @nico is a self-mention, no attribution.
    out = attribute_bot_replies(
        msgs, own_project_id=None, project_store=ps, is_master=True
    )
    assert out[1]["bot"] is None


def test_archive_filter_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    s1 = store.create()
    s2 = store.create()
    store.mark_archived(s2.id, True)

    active = store.list(10, 0)
    assert [s.id for s in active] == [s1.id]
    assert active[0].archived == 0

    archived = store.list(10, 0, archived=True)
    assert [s.id for s in archived] == [s2.id]
    assert archived[0].archived == 1

    store.mark_archived(s2.id, False)
    assert len(store.list(10, 0)) == 2


def test_origin_tagging_and_activity(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    ps = _ps(tmp_path)
    bot = ps.create(name="Runner Bot")
    run = store.create(project_id=bot.id, origin={"kind": "workflow", "source": "wf.md"})
    store.mark_hidden(run.id)
    store.create(project_id=bot.id)

    activity = store.list_activity(bot.id)
    assert [a["id"] for a in activity] == [run.id]
    assert activity[0]["origin"]["kind"] == "workflow"
    # Hidden internal sessions never appear in the normal list.
    assert all(s.id != run.id for s in store.list(10, 0))


def test_persona_and_handle_seed_on_upgrade(tmp_path: Path) -> None:
    # Simulate a pre-upgrade DB: projects without persona/handle columns.
    import sqlite3

    db_path = tmp_path / "sessions.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', instructions TEXT NOT NULL DEFAULT '', "
        "vault_path TEXT NOT NULL, color TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT '', "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO projects (id, name, description, vault_path) "
        "VALUES ('p1', 'Old Project', 'does things', 'projects/old-project')"
    )
    conn.commit()
    conn.close()

    SessionStore(db_path=db_path)  # runs the migrations

    ps = ProjectStore(db_path)
    proj = ps.get("p1")
    assert proj is not None
    assert proj.handle == "old-project"
    assert proj.persona == "does things"


def test_attribute_bot_replies_marks_mentioned_turns(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    other = ps.create(name="Other Bot")
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},  # own bot — no attribution
        {"role": "user", "content": f"@{other.handle} take over"},
        {"role": "assistant", "content": "sure"},
        {"role": "user", "content": "back to normal"},
        {"role": "assistant", "content": "ok"},
    ]
    out = attribute_bot_replies(msgs, own_project_id=None, project_store=ps)
    assert out[1]["bot"] is None
    assert out[3]["bot"] is not None and out[3]["bot"]["handle"] == other.handle
    assert out[5]["bot"] is None


def test_team_round_followups(tmp_path: Path) -> None:
    from nexus.agent.bots import TEAM_MARKER, parse_mentions, team_followup_messages

    ps = _ps(tmp_path)
    lead = ps.create(name="Lead Bot")
    m2 = ps.create(name="Second Bot")
    m3 = ps.create(name="Third Bot")
    team = {"name": "Squad", "members": [lead.id, m2.id, m3.id], "mode": "round"}

    # No mention → passes for every member except the lead.
    follow = team_followup_messages(team, "what do you all think?", project_store=ps)
    assert follow == [f"{TEAM_MARKER}@{m2.handle}", f"{TEAM_MARKER}@{m3.handle}"]
    for msg in follow:
        assert parse_mentions(msg)  # the marker line routes as a real mention

    # A directed message goes only to the mentioned member.
    assert team_followup_messages(team, f"@{m3.handle} you go", project_store=ps) == []
    # Lead mode and small teams never fan out.
    assert team_followup_messages({**team, "mode": "lead"}, "hi", project_store=ps) == []
    assert team_followup_messages(
        {"name": "Duo", "members": [lead.id, m2.id], "mode": "round"}, "hi", project_store=ps
    ) == [f"{TEAM_MARKER}@{m2.handle}"]


def test_team_store_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    s = store.create()
    assert store.get_team(s.id) is None
    store.set_team(s.id, {"name": "Crew", "members": ["a", "b"], "mode": "round"})
    assert store.get_team(s.id) == {"name": "Crew", "members": ["a", "b"], "mode": "round"}
    store.set_team(s.id, None)
    assert store.get_team(s.id) is None
    # Team rides along on the summary list.
    store.set_team(s.id, {"name": "Crew", "members": ["a"], "mode": "lead"})
    assert store.list(10, 0)[0].team is not None


async def test_delete_bot_routes(tmp_path: Path, monkeypatch) -> None:
    from nexus.server.routes import bots as bots_routes

    db_path = tmp_path / "sessions.sqlite"
    store = SessionStore(db_path=db_path)
    monkeypatch.setattr(bots_routes, "_DB_PATH", db_path)
    ps = ProjectStore(db_path)

    # Archive mode: chats survive, restorable, detached from the dead bot.
    bot_a = ps.create(name="Soft Gone")
    sa1 = store.create(project_id=bot_a.id)
    sa2 = store.create(project_id=bot_a.id)
    result = await bots_routes.delete_bot(bot_a.id, chats="archive", store=store)
    assert result["deleted"] is True and result["sessions_archived"] == 2
    assert ps.get(bot_a.id) is None
    for sid in (sa1.id, sa2.id):
        sess = store.get(sid)
        assert sess is not None and store.get_team(sid) is None
        assert store.list(10, 0, archived=True)[0].id in {sa1.id, sa2.id}
        assert sess.project_id is None  # detached by ProjectStore.delete

    # Delete mode: chats go with the bot.
    bot_b = ps.create(name="Hard Gone")
    sb = store.create(project_id=bot_b.id)
    result = await bots_routes.delete_bot(bot_b.id, chats="delete", store=store)
    assert result["sessions_deleted"] == 1
    assert store.get(sb.id) is None

    # The main bot is protected.
    import pytest

    with pytest.raises(Exception):
        await bots_routes.delete_bot("main", chats="archive", store=store)


async def test_create_and_update_team(tmp_path: Path, monkeypatch) -> None:
    from nexus.server.routes import bots as bots_routes

    db_path = tmp_path / "sessions.sqlite"
    store = SessionStore(db_path=db_path)
    monkeypatch.setattr(bots_routes, "_DB_PATH", db_path)
    ps = ProjectStore(db_path)
    lead = ps.create(name="Team Lead")
    other = ps.create(name="Team Mate")

    created = await bots_routes.create_team(
        {"name": "Squad", "members": [lead.id, other.id, "missing"], "mode": "round"},
        store=store,
    )
    assert created["team"]["members"] == [lead.id, other.id]  # unknown ids dropped
    sid = created["session_id"]
    assert store.get_team(sid)["mode"] == "round"
    assert store.get(sid).project_id == lead.id  # lives under the lead
    assert store.get(sid).title == "Squad"

    updated = await bots_routes.update_team(
        sid, {"mode": "lead", "members": [other.id, lead.id]}, store=store
    )
    assert updated["team"]["mode"] == "lead"
    assert store.get_team(sid)["members"] == [other.id, lead.id]
    assert store.get(sid).project_id == other.id  # re-pointed to the new lead

    cleared = await bots_routes.update_team(sid, {"clear": True}, store=store)
    assert cleared["team"] is None and store.get_team(sid) is None


class _FakeCoordConfig:
    enabled = True
    session_id = "master-sid"


class _FakeCoordService:
    def __init__(self, store):
        self.config = _FakeCoordConfig()
        self.store = store

    def is_coordinator(self, session_id):
        return session_id == "master-sid"

    def _dispatch_approved(self, project_id):
        rules = getattr(self, "auto_approve", [])
        return "all" in [str(r).lower() for r in rules]


async def test_bot_config_create_and_delete(tmp_path: Path, monkeypatch) -> None:
    import nexus.coordinator_tools as ct
    from nexus.server.project_store import ProjectStore as PS
    from nexus.server.routes import bots as bots_routes

    db_path = tmp_path / "sessions.sqlite"
    store = SessionStore(db_path=db_path)
    ps = PS(db_path)
    monkeypatch.setattr(ct, "get_service", lambda: _FakeCoordService(store))
    monkeypatch.setattr(ct, "_bot_store", lambda: ps)
    monkeypatch.setattr(bots_routes, "_DB_PATH", db_path)  # used by _delete_bot_impl

    # Unconfirmed create is refused with needs_confirmation.
    res = json.loads(await ct.handle_bot_config({"action": "create", "name": "Novo"}, "master-sid"))
    assert res["ok"] is False and res.get("needs_confirmation") is True

    # Confirmed create goes through ProjectStore (unique handle + vault path).
    res = json.loads(await ct.handle_bot_config(
        {"action": "create", "name": "Novo", "persona": "teste", "confirmed": True},
        "master-sid",
    ))
    assert res["ok"] is True
    bot_id = res["bot"]["id"]
    assert res["bot"]["handle"] == "novo"
    proj = ps.get(bot_id)
    assert proj is not None and proj.persona == "teste"
    assert proj.vault_path.startswith("projects/")

    # Non-master sessions are refused.
    res = json.loads(await ct.handle_bot_config({"action": "create", "name": "X", "confirmed": True}, "other"))
    assert res["ok"] is False

    # Confirmed delete removes the bot.
    res = json.loads(await ct.handle_bot_config(
        {"action": "delete", "bot": "novo", "chats": "keep", "confirmed": True},
        "master-sid",
    ))
    assert res["ok"] is True and res.get("deleted") is True
    assert ps.get(bot_id) is None


async def test_teams_tool_creates_and_lists(tmp_path: Path, monkeypatch) -> None:
    import nexus.coordinator_tools as ct
    from nexus.server.project_store import ProjectStore as PS
    from nexus.server.routes import bots as bots_routes

    db_path = tmp_path / "sessions.sqlite"
    store = SessionStore(db_path=db_path)
    ps = PS(db_path)
    monkeypatch.setattr(ct, "get_service", lambda: _FakeCoordService(store))
    monkeypatch.setattr(ct, "_bot_store", lambda: ps)
    monkeypatch.setattr(bots_routes, "_DB_PATH", db_path)

    lead = ps.create(name="Lider")
    m2 = ps.create(name="Segundo")

    # Handles as member refs (not ids) must resolve.
    res = json.loads(await ct.handle_teams(
        {"action": "create", "name": "Esquadrão", "members": [f"@{lead.handle}", m2.id], "mode": "lead"},
        "master-sid",
    ))
    assert res["ok"] is True
    sid = res["session_id"]
    team = store.get_team(sid)
    assert team["members"] == [lead.id, m2.id]
    assert store.get(sid).project_id == lead.id

    res = json.loads(await ct.handle_teams({"action": "list"}, "master-sid"))
    assert res["ok"] is True and len(res["teams"]) == 1
    entry = res["teams"][0]
    assert entry["name"] == "Esquadrão"
    assert {m["handle"] for m in entry["members"]} == {lead.handle, m2.handle}

    # Update: switch to round + reorder members (new lead).
    res = json.loads(await ct.handle_teams(
        {"action": "update", "session_id": sid, "members": [m2.id, lead.id], "mode": "round"},
        "master-sid",
    ))
    assert res["ok"] is True
    team = store.get_team(sid)
    assert team["mode"] == "round" and team["members"][0] == m2.id
    assert store.get(sid).project_id == m2.id

    # Non-master refused.
    res = json.loads(await ct.handle_teams({"action": "list"}, "other"))
    assert res["ok"] is False
