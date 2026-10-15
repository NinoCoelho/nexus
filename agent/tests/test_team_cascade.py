"""Tests for the bot→bot cascade in team (group) chats.

Unit-tests the pure helpers (cascade_mentions, turn_speaker_id) and the
chain-only queue semantics; the server-harness tests live in
test_team_cascade_server.py.
"""

from __future__ import annotations

from pathlib import Path

from nexus.agent.bots import (
    TEAM_MARKER,
    cascade_mentions,
    team_followup_messages,
    turn_speaker_id,
)
from nexus.server.project_store import ProjectStore
from nexus.server.session_store import SessionStore


def _ps(tmp_path: Path) -> ProjectStore:
    SessionStore(db_path=tmp_path / "sessions.sqlite")  # ensures schema exists
    return ProjectStore(tmp_path / "sessions.sqlite")


def _team(*members: str, mode: str = "lead") -> dict:
    return {"name": "T", "members": list(members), "mode": mode}


def test_cascade_mentions_members_only_and_self_excluded(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    a = ps.create(name="Alpha")
    b = ps.create(name="Beta")
    ps.create(name="Outsider")

    reply = f"done — @alpha will refine it, @outsider is not in the team, @{a.handle} twice"
    targets = cascade_mentions(
        reply, team=_team(b.id, a.id), speaker_id=a.id, project_store=ps
    )
    handles = [t["bot"]["handle"] for t in targets]
    # @alpha is the speaker (self-mention → no loop); @outsider is not a
    # member; the duplicate @alpha collapses.
    assert handles == []
    assert all(t["marker"].startswith(TEAM_MARKER) for t in targets)

    targets2 = cascade_mentions(
        "ping @beta and @outsider", team=_team(b.id, a.id), speaker_id=a.id, project_store=ps
    )
    assert [t["bot"]["handle"] for t in targets2] == [b.handle]
    assert targets2[0]["marker"] == f"{TEAM_MARKER}@{b.handle}"


def test_turn_speaker_id_routes_marker_and_plain(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    a = ps.create(name="Alpha")
    b = ps.create(name="Beta")
    team = _team(a.id, b.id)

    # Synthetic marker turn → the mentioned bot spoke.
    assert turn_speaker_id(f"{TEAM_MARKER}@{b.handle}", team=team, own_project_id=a.id, project_store=ps) == b.id
    # User @mention → the mentioned bot spoke.
    assert turn_speaker_id(f"@{b.handle} please", team=team, own_project_id=a.id, project_store=ps) == b.id
    # Plain message → the lead (chat owner) spoke.
    assert turn_speaker_id("status?", team=team, own_project_id=a.id, project_store=ps) == a.id
    # No chat owner known → the lead by member order.
    assert turn_speaker_id("status?", team=team, own_project_id=None, project_store=ps) == a.id


def test_round_fanout_directed_message_has_no_fanout(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    a = ps.create(name="Alpha")
    b = ps.create(name="Beta")
    team = _team(a.id, b.id, mode="round")

    # A directed @mention goes to that member only — no fan-out.
    assert team_followup_messages(team, f"@{b.handle} please", project_store=ps) == []
    # Undirected in round mode → one marker per non-lead member.
    out = team_followup_messages(team, "status?", project_store=ps)
    assert out == [f"{TEAM_MARKER}@{b.handle}"]


def test_cascade_mentions_requires_reply_and_team(tmp_path: Path) -> None:
    ps = _ps(tmp_path)
    a = ps.create(name="Alpha")
    assert cascade_mentions("", team=_team(a.id), speaker_id=None, project_store=ps) == []
    assert cascade_mentions("@alpha hi", team=None, speaker_id=None, project_store=ps) == []
