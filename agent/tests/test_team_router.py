"""Tests for the team auto-router (mode="auto") — embedding-based routing.

Uses a fake embedder with deterministic vectors so no model download is
needed in CI. Covers: single pick, several, fail-open to lead, non-auto
modes, single-member teams, embedder errors, and persona-cache invalidation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from nexus.agent.team_router import (
    MARGIN,
    THRESHOLD,
    RouteDecision,
    route_team_decision,
    route_team_message,
)
from nexus.server.project_store import ProjectStore
from nexus.server.session_store import SessionStore

# ── Fake embedder ──────────────────────────────────────────────────────

class _FakeEmbedder:
    """Deterministic embeddings: each text maps to a fixed vector."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            if t not in self.vectors:
                raise KeyError(f"no vector for {t!r}")
            out.append(self.vectors[t])
        return out


def _install_fake(monkeypatch, vectors: dict[str, list[float]]) -> None:
    fake = _FakeEmbedder(vectors)

    def _get() -> _FakeEmbedder:
        return fake

    monkeypatch.setattr("nexus.agent.team_router.get_builtin_embedder", _get)


# ── Helpers ────────────────────────────────────────────────────────────

def _ps(tmp_path: Path) -> ProjectStore:
    SessionStore(db_path=tmp_path / "sessions.sqlite")  # ensures schema exists
    return ProjectStore(tmp_path / "sessions.sqlite")


def _pt(name: str, handle: str, persona: str) -> str:
    """Full persona text as built by ``team_router._persona_text``."""
    return f"{name} (@{handle}) — {persona}"


def _bot(ps: ProjectStore, name: str, handle: str, persona: str) -> object:
    proj = ps.create(name=name, persona=persona)
    if handle:
        conn = sqlite3.connect(str(ps._db_path))
        conn.execute("UPDATE projects SET handle = ? WHERE id = ?", (handle, proj.id))
        conn.commit()
        conn.close()
    return ps.get(proj.id)


def _team(lead_id: str, *rest: str, mode: str = "auto") -> dict:
    return {"id": "team-1", "name": "Squad", "members": [lead_id, *rest], "mode": mode}


# ── Tests ─────────────────────────────────────────────────────────────


async def test_auto_single_pick(monkeypatch, tmp_path):
    """Clear semantic winner → single, that bot answers."""
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "project management and planning")
    writer = _bot(ps, "Writer", "writer", "creative writing and storytelling")
    coder = _bot(ps, "Coder", "coder", "software engineering and debugging")

    vectors = {
        _pt("Writer", "writer", "creative writing and storytelling"): [1.0, 0.0, 0.0],
        _pt("Coder", "coder", "software engineering and debugging"): [0.0, 1.0, 0.0],
        _pt("Lead", "lead", "project management and planning"): [0.0, 0.0, 1.0],
        "please write a story about a dragon": [0.9, 0.1, 0.0],
    }
    _install_fake(monkeypatch, vectors)

    decision = await route_team_message(
        team=_team(lead.id, writer.id, coder.id),
        message="please write a story about a dragon",
        project_store=ps,
    )
    assert decision.mode == "single"
    assert decision.chosen == writer.id
    assert decision.score is not None and decision.score >= THRESHOLD
    assert decision.margin is not None and decision.margin >= MARGIN


async def test_auto_several_when_close(monkeypatch, tmp_path):
    """Top-2 both above threshold within margin → several members answer."""
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "general help")
    a = _bot(ps, "Alpha", "alpha", "writing assistant")
    b = _bot(ps, "Beta", "beta", "writing assistant")  # identical persona

    vectors = {
        _pt("Lead", "lead", "general help"): [0.5, 0.5, 0.0],
        _pt("Alpha", "alpha", "writing assistant"): [1.0, 0.0, 0.0],
        _pt("Beta", "beta", "writing assistant"): [1.0, 0.0, 0.0],
        "draft an email for me": [1.0, 0.0, 0.0],
    }
    _install_fake(monkeypatch, vectors)

    decision = await route_team_message(
        team=_team(lead.id, a.id, b.id),
        message="draft an email for me",
        project_store=ps,
    )
    assert decision.mode == "several"
    assert decision.several is not None
    assert len(decision.several) == 2
    assert lead.id not in decision.several  # lead is "general help", far from "writing"
    assert decision.score is not None and decision.score >= THRESHOLD


async def test_auto_fail_open_to_lead(monkeypatch, tmp_path):
    """Low confidence → lead (fail-open, never blocks)."""
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "general help")
    writer = _bot(ps, "Writer", "writer", "creative writing")

    vectors = {
        _pt("Lead", "lead", "general help"): [1.0, 0.0, 0.0],
        _pt("Writer", "writer", "creative writing"): [0.0, 1.0, 0.0],
        "what is the weather in tokyo": [0.0, 0.0, 1.0],
    }
    _install_fake(monkeypatch, vectors)

    decision = await route_team_message(
        team=_team(lead.id, writer.id),
        message="what is the weather in tokyo",
        project_store=ps,
    )
    assert decision.mode == "lead"


async def test_non_auto_mode_returns_lead(monkeypatch, tmp_path):
    """round / lead / None modes → router returns lead immediately."""
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "x")
    member = _bot(ps, "Member", "member", "y")

    _install_fake(
        monkeypatch,
        {_pt("Lead", "lead", "x"): [1.0, 0.0], _pt("Member", "member", "y"): [0.0, 1.0], "anything": [1.0, 0.0]},
    )

    for mode in ("round", "lead", None):
        decision = await route_team_message(
            team=_team(lead.id, member.id, mode=mode),
            message="anything",
            project_store=ps,
        )
        assert decision.mode == "lead"


async def test_single_member_team_returns_lead(monkeypatch, tmp_path):
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "x")
    _install_fake(
        monkeypatch, {_pt("Lead", "lead", "x"): [1.0, 0.0], "msg": [1.0, 0.0]}
    )
    decision = await route_team_message(
        team=_team(lead.id, mode="auto"), message="msg", project_store=ps
    )
    assert decision.mode == "lead"


async def test_embedder_error_falls_back_to_lead(monkeypatch, tmp_path):
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "x")
    member = _bot(ps, "Member", "member", "y")

    def _boom():
        raise RuntimeError("model not loaded")

    monkeypatch.setattr("nexus.agent.team_router.get_builtin_embedder", _boom)

    decision = await route_team_message(
        team=_team(lead.id, member.id), message="msg", project_store=ps
    )
    assert decision.mode == "lead"


async def test_route_team_decision_resolves_handles(monkeypatch, tmp_path):
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "general help")
    writer = _bot(ps, "Writer", "writer", "creative writing")

    vectors = {
        _pt("Lead", "lead", "general help"): [1.0, 0.0, 0.0],
        _pt("Writer", "writer", "creative writing"): [0.0, 1.0, 0.0],
        "write a poem": [0.1, 0.9, 0.0],
    }
    _install_fake(monkeypatch, vectors)

    decision, handles = await route_team_decision(
        team=_team(lead.id, writer.id),
        message="write a poem",
        project_store=ps,
    )
    assert decision.mode == "single"
    assert decision.chosen == writer.id
    assert handles.get(writer.id) == "writer"


async def test_persona_cache_invalidates_on_edit(monkeypatch, tmp_path):
    ps = _ps(tmp_path)
    lead = _bot(ps, "Lead", "lead", "general help")
    writer = _bot(ps, "Writer", "writer", "creative writing")

    vectors_v1 = {
        _pt("Lead", "lead", "general help"): [1.0, 0.0],
        _pt("Writer", "writer", "creative writing"): [0.0, 1.0],
        "write code": [0.0, 1.0],
    }
    _install_fake(monkeypatch, vectors_v1)

    d1 = await route_team_message(
        team=_team(lead.id, writer.id), message="write code", project_store=ps
    )
    assert d1.mode == "single" and d1.chosen == writer.id

    # Change writer's persona → cache key changes → re-embed.
    conn = sqlite3.connect(str(ps._db_path))
    conn.execute("UPDATE projects SET persona = ? WHERE id = ?", ("software engineering", writer.id))
    conn.commit()
    conn.close()

    vectors_v2 = {
        _pt("Lead", "lead", "general help"): [1.0, 0.0],
        _pt("Writer", "writer", "software engineering"): [0.0, 1.0],
        "write code": [0.0, 1.0],
    }
    _install_fake(monkeypatch, vectors_v2)

    d2 = await route_team_message(
        team=_team(lead.id, writer.id), message="write code", project_store=ps
    )
    assert d2.mode == "single" and d2.chosen == writer.id


def test_route_decision_dataclass_fields():
    d = RouteDecision(mode="lead", chosen=None, several=None, score=None, margin=None, latency_ms=None)
    assert d.mode == "lead"
    assert d.chosen is None


# ── Threshold constants sanity ────────────────────────────────────────


def test_thresholds_sensible():
    assert 0.0 < THRESHOLD < 1.0
    assert 0.0 < MARGIN < THRESHOLD
