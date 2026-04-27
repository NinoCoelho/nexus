"""USER.md injection into the system prompt."""

from __future__ import annotations

from pathlib import Path

import pytest
from loom.home import AgentHome

from nexus.agent.prompt_builder import build_system_prompt
from nexus.skills.registry import SkillRegistry


@pytest.fixture
def empty_registry(tmp_path: Path) -> SkillRegistry:
    return SkillRegistry(tmp_path / "skills")


def test_prompt_omits_user_block_when_home_missing(empty_registry: SkillRegistry) -> None:
    out = build_system_prompt(empty_registry)
    assert "## About the user" not in out


def test_prompt_omits_user_block_when_user_md_empty(
    empty_registry: SkillRegistry, tmp_path: Path,
) -> None:
    home = AgentHome(tmp_path, name="t")
    home.write_user("   \n  \n")
    out = build_system_prompt(empty_registry, home=home)
    assert "## About the user" not in out


def test_prompt_includes_user_block_and_nudge(
    empty_registry: SkillRegistry, tmp_path: Path,
) -> None:
    home = AgentHome(tmp_path, name="t")
    home.write_user("# About the user\n\nName: Idemir\nTone: terse, pt-BR\n")
    out = build_system_prompt(empty_registry, home=home)
    assert "## About the user" in out
    assert "Name: Idemir" in out
    assert "edit_profile" in out
