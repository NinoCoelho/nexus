"""SkillRegistry: bundled-seed, reload, descriptions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, description: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
    )


def test_registry_seeds_bundled_skills_on_first_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _write_skill(bundled, "alpha", "first builtin")
    _write_skill(bundled, "beta", "second builtin")

    monkeypatch.setattr("nexus.skills.registry._BUNDLED_SKILLS_DIR", bundled)

    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    names = [s.name for s in reg.list()]
    assert names == ["alpha", "beta"]
    # builtin trust is recorded for seeded copies.
    meta = json.loads((user / "alpha" / ".meta.json").read_text())
    assert meta["trust"] == "builtin"


def test_registry_reload_picks_up_new_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing")
    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    assert reg.list() == []

    _write_skill(user, "newone", "hand-installed")
    reg.reload()
    assert [s.name for s in reg.list()] == ["newone"]
    assert ("newone", "hand-installed") in reg.descriptions()


def test_registry_skips_malformed_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing")
    user = tmp_path / "user"
    user.mkdir()
    # Missing description — should be skipped, not crash.
    (user / "broken").mkdir()
    (user / "broken" / "SKILL.md").write_text("---\nname: broken\n---\n\nbody\n")
    _write_skill(user, "ok", "fine skill")

    reg = SkillRegistry(skills_dir=user)
    assert [s.name for s in reg.list()] == ["ok"]
