"""SKILL.md frontmatter ``requires_keys`` parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, frontmatter: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\nbody for {name}\n"
    )
    return skill_dir


def test_no_requires_keys_yields_empty_tuple(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "plain",
        'name: plain\ndescription: "x"',
    )
    reg = SkillRegistry(tmp_path)
    assert reg.get("plain").requires_keys == ()


def test_string_form(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "stringform",
        'name: stringform\ndescription: "x"\nrequires_keys:\n  - GITHUB_TOKEN\n  - OPENAI_API_KEY',
    )
    reg = SkillRegistry(tmp_path)
    reqs = reg.get("stringform").requires_keys
    assert [r.name for r in reqs] == ["GITHUB_TOKEN", "OPENAI_API_KEY"]
    assert reqs[0].help is None
    assert reqs[0].url is None


def test_object_form(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "objform",
        (
            'name: objform\ndescription: "x"\n'
            "requires_keys:\n"
            "  - name: GITHUB_TOKEN\n"
            "    help: Personal access token (repo scope)\n"
            "    url: https://github.com/settings/tokens\n"
        ),
    )
    reg = SkillRegistry(tmp_path)
    reqs = reg.get("objform").requires_keys
    assert reqs[0].name == "GITHUB_TOKEN"
    assert reqs[0].help == "Personal access token (repo scope)"
    assert reqs[0].url == "https://github.com/settings/tokens"


def test_mixed_forms(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "mixed",
        (
            'name: mixed\ndescription: "x"\n'
            "requires_keys:\n"
            "  - PLAIN_KEY\n"
            "  - name: FANCY_KEY\n"
            "    help: with help\n"
        ),
    )
    reg = SkillRegistry(tmp_path)
    reqs = reg.get("mixed").requires_keys
    assert [r.name for r in reqs] == ["PLAIN_KEY", "FANCY_KEY"]
    assert reqs[1].help == "with help"


def test_invalid_name_skips_skill_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_skill(
        tmp_path,
        "badname",
        'name: badname\ndescription: "x"\nrequires_keys:\n  - lower_case_bad',
    )
    with caplog.at_level("WARNING", logger="nexus.skills.registry"):
        reg = SkillRegistry(tmp_path)
    assert "badname" not in reg
    assert any("requires_keys" in rec.getMessage() for rec in caplog.records)
