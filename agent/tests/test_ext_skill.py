"""Tests for the external-agent delegation skill (ext_skill).

Covers: the generated markdown content (frontmatter, CLI commands,
self-install steps), the /ext/delegate route, and the `nexus skill print`
CLI command.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus.ext_skill import (
    SKILL_NAME,
    delegation_skill_markdown,
    paste_prompt,
)

# ── skill content ─────────────────────────────────────────────────────


def test_skill_has_agent_skills_frontmatter():
    md = delegation_skill_markdown()
    assert md.startswith("---\n")
    assert f"name: {SKILL_NAME}" in md
    assert "description:" in md
    # Frontmatter must be closed before the body.
    assert md.count("---") >= 2


def test_skill_documents_cli_surface():
    md = delegation_skill_markdown()
    for needle in (
        "nexus bots list --json",
        "nexus run <bot-handle>",
        "--json",
        "--new",
        "--timeout",
        "Exit codes",
    ):
        assert needle in md, f"skill missing: {needle}"


def test_skill_has_self_install_steps():
    md = delegation_skill_markdown()
    assert "Self-install" in md
    assert "Claude Code" in md and "~/.claude/skills/" in md
    assert "OpenCode" in md and "AGENTS.md" in md
    assert "Codex" in md
    # The agent must verify before claiming success.
    assert "nexus bots list --json" in md
    assert "do not mark the\nskill as installed" in md


def test_skill_port_note_only_for_non_default():
    default = delegation_skill_markdown(port=18989)
    assert "Nexus server port" not in default
    custom = delegation_skill_markdown(port=19999)
    assert "Nexus server port: 19999" in custom


def test_paste_prompt_mentions_self_install():
    prompt = paste_prompt()
    assert "auto-instala" in prompt or "self-install" in prompt.lower()


# ── route ─────────────────────────────────────────────────────────────


def _client() -> TestClient:
    from nexus.server.routes.ext_delegate import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_ext_delegate_page_renders_skill():
    client = _client()
    r = client.get("/ext/delegate")
    assert r.status_code == 200
    body = r.text
    # Page purpose + the whole skill block embedded.
    for needle in (
        "nexus run",
        SKILL_NAME,
        "self-install",
        "Claude Code",
        "OpenCode",
        "Codex",
        "nexus bots list --json",
        "copyAll",
        # meta marker so the chrome extension port-capture keeps working
        'name="nexus-server"',
    ):
        assert needle in body, f"page missing: {needle}"


def test_ext_delegate_page_escapes_skill_html():
    client = _client()
    r = client.get("/ext/delegate")
    # The skill body contains yaml frontmatter with `---`; it must be
    # embedded as text, not interpreted as HTML tags. A raw "<skill" tag
    # would indicate failed escaping.
    assert "<skill" not in r.text.lower()


# ── CLI ───────────────────────────────────────────────────────────────


def test_cli_skill_print_outputs_skill(capsys):
    from nexus.cli.ext_skill_cmd import print_skill

    print_skill(port=18989)
    out = capsys.readouterr().out
    assert out.startswith("---\n")
    assert f"name: {SKILL_NAME}" in out
    assert "nexus run" in out
    assert "Self-install" in out
