"""System-prompt advertises stored credentials so the LLM stops asking
the user for keys we already have."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.agent.prompt_builder import build_system_prompt
from nexus.skills.registry import SkillRegistry


@pytest.fixture
def empty_registry(tmp_path: Path) -> SkillRegistry:
    return SkillRegistry(tmp_path / "skills")


@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexus import secrets as _s

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")


def test_no_credentials_means_no_block(empty_registry: SkillRegistry) -> None:
    out = build_system_prompt(empty_registry)
    assert "## Stored credentials" not in out


def test_credentials_block_lists_names_not_values(empty_registry: SkillRegistry) -> None:
    from nexus import secrets

    secrets.set("APIFY_API_KEY", "the-secret-value-1234567890", kind="generic")
    secrets.set("GITHUB_TOKEN", "ghp_secretsecretsecret", kind="skill", skill="gh")

    out = build_system_prompt(empty_registry)
    assert "## Stored credentials" in out
    assert "`$APIFY_API_KEY`" in out
    assert "`$GITHUB_TOKEN`" in out
    assert "(used by skill `gh`)" in out
    # Raw values must never end up in the prompt
    assert "the-secret-value-1234567890" not in out
    assert "ghp_secretsecretsecret" not in out


def test_credentials_block_warns_against_echo(empty_registry: SkillRegistry) -> None:
    from nexus import secrets

    secrets.set("SOME_KEY", "v" * 30, kind="generic")
    out = build_system_prompt(empty_registry)
    # The ban on echo/printenv is the whole reason this block exists
    assert "echo" in out.lower()
    assert "printenv" in out.lower()
    # And the placeholder mechanic is named explicitly
    assert "$NAME" in out or "placeholder" in out.lower()
