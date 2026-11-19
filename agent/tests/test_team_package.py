"""Tests for the team_package agent tool (export/import portable teams)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.server.project_store import ProjectStore
from nexus.server.session_store import SessionStore
from nexus.tools.team_package import (
    TEAM_PLUGIN_KEY,
    TEAM_PLUGIN_VERSION,
    _export_team,
    _import_team,
    _serialize_team,
    _slugify,
)

# _wf_md is defined in test_trigger_tool; we redefine locally below.


def _write_vault_file(vault_dir: Path, rel_path: str, content: str) -> None:
    full = vault_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _ps(tmp_path: Path) -> ProjectStore:
    SessionStore(db_path=tmp_path / "sessions.sqlite")  # ensures schema exists
    return ProjectStore(tmp_path / "sessions.sqlite")


# ── slugify ────────────────────────────────────────────────────────────


def test_slugify():
    assert _slugify("My Team") == "my-team"
    assert _slugify("A Bot!!!") == "a-bot"
    assert _slugify("   ") == "team"


# ── serialize ──────────────────────────────────────────────────────────


def test_serialize_team_contains_plugin_key():
    fm = {
        "id": "my-team",
        "name": "My Team",
        "team": [{"name": "Bot A", "handle": "bot-a", "persona": "helper"}],
        "rooms": [{"id": "my-team", "name": "My Team", "members": ["bot-a"], "mode": "lead"}],
        "skills": [],
        "routines": [],
        "avatars": [],
    }
    md = _serialize_team(fm, ["# My Team", "", "body"], [])
    assert f"{TEAM_PLUGIN_KEY}: {TEAM_PLUGIN_VERSION}" in md
    assert "my-team" in md
    assert "Bot A" in md


# ── export ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_team_requires_team_session(tmp_path: Path):
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    ps = _ps(tmp_path)
    s = store.create()  # no team
    result = await _export_team(s.id, None, store, ps)
    assert result["ok"] is False
    assert "not a team chat" in result["error"]


@pytest.mark.asyncio
async def test_export_team_ok(tmp_path: Path, monkeypatch):
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    ps = _ps(tmp_path)
    bot = ps.create(name="Research Bot", persona="finds information")
    import sqlite3

    conn = sqlite3.connect(str(ps._db_path))
    conn.execute("UPDATE projects SET handle = 'research-bot' WHERE id = ?", (bot.id,))
    conn.commit()
    conn.close()

    s = store.create(project_id=bot.id)
    store.set_team(
        s.id,
        {"name": "My Team", "members": [bot.id], "mode": "round"},
    )

    monkeypatch.setattr(
        "nexus.tools.team_package._vault.list_tree", list
    )
    monkeypatch.setattr(
        "nexus.tools.team_package._vault.write_file", lambda p, c: None
    )
    monkeypatch.setattr(
        "nexus.tools.team_package._vault.read_file",
        lambda p: {"content": ""},
    )

    result = await _export_team(s.id, None, store, ps)
    assert result["ok"] is True
    assert result["action"] == "export"
    assert result["team"]["bots"] == 1
    assert result["team"]["mode"] == "round"
    assert result["path"].endswith(".md")


# ── import ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_team_not_found(tmp_path: Path):
    ps = _ps(tmp_path)
    result = await _import_team("teams/nope.md", None, False, ps)
    assert result["ok"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_import_team_preview(tmp_path: Path, monkeypatch):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir,
        "teams/my-team.md",
        (
            "---\n"
            "team-plugin: 2\n"
            "id: my-team\n"
            "name: My Team\n"
            "team:\n"
            "  - name: Alpha Bot\n"
            "    handle: alpha-bot\n"
            "    persona: helps with alpha tasks\n"
            "    skills: [writing]\n"
            "skills:\n"
            "  - name: writing\n"
            "    description: writes things\n"
            "---\n"
            "# My Team\n\nimported team\n"
        ),
    )

    ps = _ps(tmp_path)

    # Monkeypatch vault read/write to use our temp dir
    import nexus.tools.team_package as tp
    from nexus import vault as _vault

    orig_read = _vault.read_file
    orig_write = _vault.write_file

    def fake_read(path):
        full = vault_dir / path
        if full.exists():
            return {"content": full.read_text(encoding="utf-8")}
        raise FileNotFoundError(path)

    def fake_write(path, content):
        full = vault_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    monkeypatch.setattr(tp, "_vault", type("_V", (), {"read_file": staticmethod(fake_read), "write_file": staticmethod(fake_write), "list_tree": staticmethod(list)}))

    result = await _import_team("teams/my-team.md", None, False, ps)
    assert result["ok"] is True
    assert result["preview"] is True
    assert result["needs_confirmation"] is True
    assert result["team"]["bots"] == 1
    assert result["team"]["routines_will_be_paused"] is True
    # No bots created yet (preview only)
    assert ps.get_by_handle("alpha-bot") is None


@pytest.mark.asyncio
async def test_import_team_creates_bots(tmp_path: Path, monkeypatch):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir,
        "teams/my-team.md",
        (
            "---\n"
            "team-plugin: 2\n"
            "id: my-team\n"
            "name: My Team\n"
            "team:\n"
            "  - name: Alpha Bot\n"
            "    handle: alpha-bot\n"
            "    persona: helps with alpha tasks\n"
            "    skills: [writing]\n"
            "skills:\n"
            "  - name: writing\n"
            "    description: writes things\n"
            "---\n"
            "# My Team\n\nimported team\n"
        ),
    )

    ps = _ps(tmp_path)

    import nexus.tools.team_package as tp

    def fake_read(path):
        full = vault_dir / path
        if full.exists():
            return {"content": full.read_text(encoding="utf-8")}
        raise FileNotFoundError(path)

    def fake_write(path, content):
        full = vault_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    monkeypatch.setattr(tp, "_vault", type("_V", (), {"read_file": staticmethod(fake_read), "write_file": staticmethod(fake_write), "list_tree": staticmethod(list)}))

    result = await _import_team("teams/my-team.md", None, True, ps)
    assert result["ok"] is True
    assert result["action"] == "import"
    assert result["team"]["bots"] == 1
    assert result["team"]["routines_paused"] is True

    # Bot was created
    bot = ps.get_by_handle("alpha-bot")
    assert bot is not None
    assert bot.name == "Alpha Bot"
    assert bot.persona == "helps with alpha tasks"

    # Skill file was written
    skill = (vault_dir / "skills/writing/SKILL.md").read_text(encoding="utf-8")
    assert "writing" in skill
    assert "writes things" in skill
