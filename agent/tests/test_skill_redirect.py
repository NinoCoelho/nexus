"""Auto-redirect: tool calls to a skill name route to skill_view."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nexus.agent._loom_bridge.registry import AgentHandlers, build_tool_registry
from nexus.skills.registry import SkillRegistry


def _seed_skill(skills_dir: Path, name: str) -> None:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: fixture skill for redirect test\n---\n\n"
        "Body content for the test skill.\n",
        encoding="utf-8",
    )


@pytest.fixture
def skill_registry(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        skills_dir = Path(td)
        _seed_skill(skills_dir, "deep-research")
        # Point seeding at an empty dir so no bundled skills get copied in.
        with tempfile.TemporaryDirectory() as bundled:
            monkeypatch.setenv("NEXUS_BUILTIN_SKILLS_DIR", bundled)
            yield SkillRegistry(skills_dir=skills_dir)


async def test_underscore_skill_name_redirects_to_skill_view(skill_registry):
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    # Model emits snake_case; skill is hyphenated.
    result = await registry.dispatch("deep_research", {})
    assert not result.is_error
    assert "Body content" in result.text


async def test_hyphenated_skill_name_redirects_to_skill_view(skill_registry):
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    result = await registry.dispatch("deep-research", {})
    assert not result.is_error
    assert "Body content" in result.text


async def test_unknown_tool_still_errors(skill_registry):
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    result = await registry.dispatch("totally-fake-tool", {})
    assert result.is_error
    assert "Unknown tool" in result.text


async def test_real_tool_call_passes_through(skill_registry):
    registry = build_tool_registry(
        skill_registry=skill_registry,
        handlers=AgentHandlers(),
    )
    # skills_list is a real registered tool — must not be intercepted.
    result = await registry.dispatch("skills_list", {})
    assert not result.is_error
