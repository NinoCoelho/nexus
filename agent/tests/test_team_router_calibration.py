"""Calibration test: team router against real labelled chat data.

Uses ``tests/fixtures/team_router_labels.json`` — 59 real user messages
from project chats (pt-BR, 5 bots) where the chat owner is the ground-truth
answerer. Rebuilds the roster in a temp ProjectStore, runs the actual
router (real fastembed model), and asserts the safety property that
matters most: **the router never sends a message to the wrong bot** —
low-confidence cases fall back to the lead, which is cheap; wrong routing
is the expensive failure.

Skipped when the fastembed model isn't already cached (no downloads in CI):
run ``uv run python scripts/calibrate_team_router.py`` first, or use Nexus
normally (GraphRAG caches the same model), then run this test.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nexus.server.project_store import ProjectStore
from nexus.server.session_store import SessionStore

FIXTURES = Path(__file__).parent / "fixtures" / "team_router_labels.json"


def _model_cached() -> bool:
    """True when the fastembed model is already on disk (no download)."""
    import os

    base = os.environ.get("NEXUS_MODELS_DIR")
    root = Path(base) if base else Path.home() / ".nexus" / "models"
    marker = root / "fastembed"
    if not marker.is_dir():
        return False
    return any("paraphrase-multilingual" in p.name for p in marker.iterdir())


pytestmark = pytest.mark.skipif(
    not FIXTURES.exists() or not _model_cached(),
    reason="needs cached fastembed model + calibration fixtures",
)


def _ps(tmp_path: Path) -> ProjectStore:
    SessionStore(db_path=tmp_path / "sessions.sqlite")
    return ProjectStore(tmp_path / "sessions.sqlite")


def _create_bots(ps: ProjectStore, roster: list[dict]) -> dict[str, str]:
    """Create the fixture roster; returns handle -> project_id."""
    out: dict[str, str] = {}
    for b in roster:
        proj = ps.create(name=b["name"], persona=b.get("persona", ""))
        handle = str(b["handle"])
        conn = sqlite3.connect(str(ps._db_path))
        conn.execute("UPDATE projects SET handle = ? WHERE id = ?", (handle, proj.id))
        conn.commit()
        conn.close()
        out[handle] = proj.id
    return out


def _handle_of(handle_to_id: dict[str, str], project_id: str) -> str:
    for handle, pid in handle_to_id.items():
        if pid == project_id:
            return handle
    return project_id


async def test_no_wrong_routing_on_real_data(tmp_path: Path, monkeypatch):
    """The safety property: with calibrated thresholds, the router never
    routes a real user message to the wrong bot."""
    from nexus.agent.team_router import route_team_message

    # Pin config thresholds to the calibrated defaults so a local
    # config.toml with custom values doesn't skew the assertions.
    class _AgentCfg:
        team_router = True
        team_router_threshold = 0.42
        team_router_margin = 0.08

    class _Cfg:
        agent = _AgentCfg()

    monkeypatch.setattr("nexus.config_file.load_cached", lambda: _Cfg())

    data = json.loads(FIXTURES.read_text())
    ps = _ps(tmp_path)
    handle_to_id = _create_bots(ps, data["roster"])
    all_ids = list(handle_to_id.values())
    team = {"id": "t", "name": "T", "members": all_ids, "mode": "auto"}

    wrong: list[dict] = []
    routed_single = 0
    routed_several = 0
    lead = 0

    for pair in data["pairs"]:
        expected_id = handle_to_id.get(pair["expected_handle"])
        assert expected_id is not None, f"fixture handle missing: {pair['expected_handle']}"

        decision = await route_team_message(team=team, message=pair["message"], project_store=ps)

        if decision.mode == "single":
            assert decision.chosen is not None
            routed_single += 1
            if decision.chosen != expected_id:
                wrong.append(
                    {
                        "message": pair["message"][:80],
                        "expected": pair["expected_handle"],
                        "got": _handle_of(handle_to_id, decision.chosen),
                    }
                )
        elif decision.mode == "several":
            routed_several += 1
            chosen = set(decision.several or ())
            if expected_id not in chosen:
                wrong.append(
                    {
                        "message": pair["message"][:80],
                        "expected": pair["expected_handle"],
                        "got": "several:" + ",".join(_handle_of(handle_to_id, c) for c in chosen),
                    }
                )
        else:
            lead += 1

    # Zero wrong routings — wrong routing sends the user's message to a bot
    # that shouldn't see it; lead fallback just keeps today's behaviour.
    assert not wrong, (
        f"{len(wrong)} wrong routings on real data: {json.dumps(wrong[:5], ensure_ascii=False)}"
    )
    # Sanity: with calibrated thresholds some messages should route (if
    # everything falls to lead the thresholds are too tight to be useful).
    assert routed_single + routed_several > 0, "router never routes — thresholds too tight"
