"""Tests for the fact-review API routes (conflicts + merges).

Uses a minimal FastAPI app with the graph router and a real EntityGraph
as the engine stand-in — the routes only call the conflict/merge surface
that EntityGraph itself exposes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from loom.store.graph import EntityGraph


@pytest.fixture
def graph(tmp_path):
    g = EntityGraph(tmp_path / "review_test.sqlite")
    yield g
    g.close()


@pytest.fixture
def client(graph, monkeypatch):
    from nexus.server.routes import graph as graph_routes

    monkeypatch.setattr(
        "nexus.agent.graphrag_manager.get_engine", lambda: graph
    )
    app = FastAPI()
    app.include_router(graph_routes.router)
    return TestClient(app)


def _seed_conflict(graph):
    head = graph.resolve_entity("Nexus", "project")
    fastapi = graph.resolve_entity("FastAPI", "technology")
    flask = graph.resolve_entity("Flask", "technology")
    old_id, _ = graph.add_triple(head, "uses", fastapi, "c1", source_path="notes/a.md")
    new_id, conflict_id = graph.add_triple(
        head, "uses", flask, "c2", source_path="notes/b.md"
    )
    assert old_id and new_id and conflict_id
    return old_id, new_id, conflict_id


def test_review_lists_open_conflict(client, graph):
    _seed_conflict(graph)
    r = client.get("/graph/knowledge/review")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert len(data["conflicts"]) == 1
    c = data["conflicts"][0]
    assert c["head"] == "Nexus"
    assert c["old_tail"] == "FastAPI"
    assert c["new_tail"] == "Flask"
    assert c["triple_b"]["status"] == "pending"
    assert c["triple_b"]["source_path"] == "notes/b.md"


def test_review_resolve_approve_new(client, graph):
    old_id, new_id, conflict_id = _seed_conflict(graph)
    r = client.post(
        "/graph/knowledge/review/resolve",
        json={"conflict_id": conflict_id, "resolution": "approve_new"},
    )
    assert r.status_code == 200
    assert graph.get_triple(old_id).status == "superseded"
    assert graph.get_triple(new_id).status == "active"
    data = client.get("/graph/knowledge/review").json()
    assert data["conflicts"] == []


def test_review_resolve_rejects_bad_resolution(client, graph):
    _, _, conflict_id = _seed_conflict(graph)
    r = client.post(
        "/graph/knowledge/review/resolve",
        json={"conflict_id": conflict_id, "resolution": "delete_everything"},
    )
    assert r.status_code == 422


def test_review_resolve_404_on_unknown(client, graph):
    r = client.post(
        "/graph/knowledge/review/resolve",
        json={"conflict_id": 999, "resolution": "keep_both"},
    )
    assert r.status_code == 404


def test_review_merge_unmerge_roundtrip(client, graph):
    a = graph.resolve_entity("PostgreSQL", "technology")
    b = graph.resolve_entity("Postgres", "technology")
    r = client.post(
        "/graph/knowledge/review/merge",
        json={"survivor_id": a, "merged_id": b},
    )
    assert r.status_code == 200
    merge_id = r.json()["merge_id"]
    assert graph.get_entity(b) is None

    data = client.get("/graph/knowledge/review").json()
    assert len(data["merges"]) == 1
    assert data["merges"][0]["merged_name"] == "Postgres"

    r = client.post("/graph/knowledge/review/unmerge", json={"merge_id": merge_id})
    assert r.status_code == 200
    assert graph.get_entity(b) is not None
    assert client.get("/graph/knowledge/review").json()["merges"] == []


def test_review_merge_validation(client, graph):
    r = client.post(
        "/graph/knowledge/review/merge",
        json={"survivor_id": "x", "merged_id": 2},
    )
    assert r.status_code == 422


def test_review_unmerge_404_on_reverted(client, graph):
    r = client.post("/graph/knowledge/review/unmerge", json={"merge_id": 12345})
    assert r.status_code == 404


def test_review_disabled_when_engine_missing(client, monkeypatch):
    monkeypatch.setattr("nexus.agent.graphrag_manager.get_engine", lambda: None)
    r = client.get("/graph/knowledge/review")
    assert r.status_code == 200
    assert r.json()["enabled"] is False
