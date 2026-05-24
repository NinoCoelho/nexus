"""HTTP-level tests for the skill admin routes:

- ``DELETE /skills/{name}`` — wraps SkillManager.delete.
- ``GET  /skills/export/archive`` — streams a ZIP of every skill dir.
- ``POST /skills/import/archive`` — extracts an uploaded ZIP and reloads.

The wizard build / discover endpoints have their own tests; these cover
the manual admin surface that the new SkillDrawer + AgentGraphView
toolbar buttons exercise.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio  # noqa: F401

from nexus.agent.llm import ChatMessage, ChatResponse, LLMProvider, StopReason, ToolSpec
from nexus.agent.loop import Agent
from nexus.server.app import create_app
from nexus.server.session_store import SessionStore
from nexus.server.settings import SettingsStore
from nexus.skills.registry import SkillRegistry


class _NoopProvider(LLMProvider):
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        return ChatResponse(content="", stop_reason=StopReason.STOP)


def _seed_skill(skills_dir: Path, name: str, description: str = "test skill") -> None:
    sd = skills_dir / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        "## When to use\n- when testing.\n\n"
        "## Steps\n1. do nothing.\n\n## Gotchas\n- none.\n"
    )


@pytest_asyncio.fixture
async def client_and_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Path, SkillRegistry]]:
    # Empty bundled-skills dir so the registry doesn't seed anything we
    # don't ask for in each test.
    monkeypatch.setattr("nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing")
    skills_dir = tmp_path / "skills"
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    settings = SettingsStore(path=tmp_path / "settings.json")
    registry = SkillRegistry(skills_dir)
    agent = Agent(provider=_NoopProvider(), registry=registry)
    app = create_app(agent=agent, registry=registry, sessions=sessions, settings_store=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, skills_dir, registry


# ── DELETE ────────────────────────────────────────────────────────────────


async def test_delete_skill_removes_directory_and_returns_204(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, skills_dir, registry = client_and_dir
    _seed_skill(skills_dir, "victim")
    # Seed happened after registry construction — pick it up now.
    registry.reload()

    res = await client.delete("/skills/victim")
    assert res.status_code == 204, res.text
    assert not (skills_dir / "victim").exists()
    # Subsequent GET returns 404.
    res = await client.get("/skills/victim")
    assert res.status_code == 404


async def test_delete_unknown_skill_returns_404(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, _, _registry = client_and_dir
    res = await client.delete("/skills/nope")
    assert res.status_code == 404


# ── EXPORT ────────────────────────────────────────────────────────────────


async def test_export_returns_zip_with_every_skill(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, skills_dir, registry = client_and_dir
    _seed_skill(skills_dir, "alpha", "first")
    _seed_skill(skills_dir, "beta", "second")
    
    registry.reload()

    res = await client.get("/skills/export/archive")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "filename=" in res.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(res.content))
    names = sorted(zf.namelist())
    assert "alpha/SKILL.md" in names
    assert "beta/SKILL.md" in names
    # Bundled-marker is intentionally excluded.
    assert ".seeded-builtins.json" not in names


# ── IMPORT ────────────────────────────────────────────────────────────────


def _build_skill_zip(name: str, body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{name}/SKILL.md", body)
    return buf.getvalue()


async def test_import_extracts_skill_and_reloads_registry(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, skills_dir, registry = client_and_dir
    body = (
        "---\nname: imported\ndescription: a skill that came from a zip\n---\n\n"
        "## When to use\n- when imported.\n"
    )
    archive = _build_skill_zip("imported", body)
    files = {"file": ("skills.zip", archive, "application/zip")}
    res = await client.post("/skills/import/archive", files=files)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["imported"] == ["imported"]
    assert payload["skipped"] == []

    # On disk + visible via the listing endpoint.
    assert (skills_dir / "imported" / "SKILL.md").read_text() == body
    res = await client.get("/skills/imported")
    assert res.status_code == 200
    assert res.json()["description"] == "a skill that came from a zip"


async def test_import_overwrites_existing_skill_with_same_name(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, skills_dir, registry = client_and_dir
    _seed_skill(skills_dir, "shared", description="original")
    
    registry.reload()

    new_body = (
        "---\nname: shared\ndescription: replaced via import\n---\n\n"
        "## When to use\n- when re-imported.\n"
    )
    archive = _build_skill_zip("shared", new_body)
    res = await client.post(
        "/skills/import/archive",
        files={"file": ("skills.zip", archive, "application/zip")},
    )
    assert res.status_code == 200
    assert (skills_dir / "shared" / "SKILL.md").read_text() == new_body


async def test_import_skips_entries_without_skill_md(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, skills_dir, registry = client_and_dir
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("nodir/README.md", "no SKILL.md here")
        zf.writestr(
            "good/SKILL.md",
            "---\nname: good\ndescription: ok\n---\n\n## When to use\n- now.\n",
        )
    res = await client.post(
        "/skills/import/archive",
        files={"file": ("skills.zip", buf.getvalue(), "application/zip")},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["imported"] == ["good"]
    assert any(s["name"] == "nodir" for s in payload["skipped"])
    assert (skills_dir / "good").is_dir()
    assert not (skills_dir / "nodir").exists()


async def test_import_rejects_invalid_zip(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, _, _registry = client_and_dir
    res = await client.post(
        "/skills/import/archive",
        files={"file": ("not.zip", b"this is not a zip", "application/zip")},
    )
    assert res.status_code == 400


async def test_import_rejects_path_traversal(
    client_and_dir: tuple[httpx.AsyncClient, Path, SkillRegistry],
) -> None:
    client, skills_dir, registry = client_and_dir
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "evil/SKILL.md",
            "---\nname: evil\ndescription: tries traversal\n---\n\n"
            "## When to use\n- never.\n",
        )
        zf.writestr("evil/../escapee.txt", b"should not land outside")
    res = await client.post(
        "/skills/import/archive",
        files={"file": ("skills.zip", buf.getvalue(), "application/zip")},
    )
    assert res.status_code == 200
    # The traversal entry rolls back the whole 'evil' skill — neither
    # the skill nor the escapee end up on disk.
    assert not (skills_dir / "evil").exists()
    assert not (skills_dir.parent / "escapee.txt").exists()
