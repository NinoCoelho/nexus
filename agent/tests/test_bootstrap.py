"""Tests for the /bootstrap agent handoff endpoints.

Covers: the living markdown doc (context, roster, skill links), the
per-skill sub-resource, 404 for unknown skills, and the cross-links from
/ext/delegate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.ext_skill import SKILL_NAME
from nexus.server.project_store import ProjectStore
from nexus.server.session_store import SessionStore


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "sessions.sqlite"
    SessionStore(db_path=db_path)  # schema
    ps = ProjectStore(db_path)
    ps.create(name="Swe Bot", description="software engineering")
    ps.create(name="Cfo Bot", description="finance")

    from nexus.server.routes import bootstrap

    monkeypatch.setattr(
        bootstrap, "_bot_roster", lambda: [
            {"handle": "swe-bot", "name": "Swe Bot", "description": "software engineering"},
            {"handle": "cfo-bot", "name": "Cfo Bot", "description": "finance"},
        ]
    )

    app = FastAPI()
    app.include_router(bootstrap.router)
    return TestClient(app)


def test_bootstrap_doc_is_markdown_with_context(client: TestClient):
    r = client.get("/bootstrap")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    body = r.text
    for needle in (
        "# Nexus agent bootstrap",
        "nexus bots list --json",
        "nexus run @handle",
        "Exit codes",
        "Version:",
        "Port:",
    ):
        assert needle in body, f"bootstrap doc missing: {needle}"


def test_bootstrap_lists_real_roster(client: TestClient):
    body = client.get("/bootstrap").text
    assert "@swe-bot" in body
    assert "@cfo-bot" in body
    assert "software engineering" in body


def test_bootstrap_links_skill_resource(client: TestClient):
    body = client.get("/bootstrap").text
    assert f"/bootstrap/skills/{SKILL_NAME}.md" in body


def test_bootstrap_skill_resource_serves_delegation(client: TestClient):
    r = client.get(f"/bootstrap/skills/{SKILL_NAME}.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    body = r.text
    # Header prepended + full skill body present.
    assert f"# Nexus skill: {SKILL_NAME}" in body
    assert "nexus run" in body
    assert "Self-install" in body
    assert "Claude Code" in body


def test_bootstrap_unknown_skill_404(client: TestClient):
    r = client.get("/bootstrap/skills/nope.md")
    assert r.status_code == 404
    assert "Available" in r.text


def test_ext_delegate_page_offers_curl_option():
    from nexus.server.routes.ext_delegate import router as ext_router

    app = FastAPI()
    app.include_router(ext_router)
    client = TestClient(app)
    body = client.get("/ext/delegate").text
    assert "Option A" in body
    assert "/bootstrap" in body
    assert "curl" in body


def test_skill_mentions_live_bootstrap_alternative():
    from nexus.ext_skill import delegation_skill_markdown

    assert "/bootstrap" in delegation_skill_markdown()
