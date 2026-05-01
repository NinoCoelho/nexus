"""Tests for the wizard build path.

Covers:

* manager.create accepts ``trust`` + ``derived_from`` and persists them.
* registry round-trip surfaces ``derived_from`` on the loaded Skill.
* discovery.load_candidate_by_id returns the cached candidate.
* skill_wizard._compose_build_seed embeds the right fields and truncates.

The full POST /skills/wizard/build route would require booting the agent
loop with a real LLM provider; that part is covered by the manual end-to-end
verification step rather than a synchronous unit test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.skills.discovery import (
    Candidate,
    Classification,
    KeyReq,
    _candidate_to_cache,
    load_candidate_by_id,
)
from nexus.skills.manager import SkillManager
from nexus.skills.registry import SkillRegistry
from nexus.server.routes.skill_wizard import _compose_build_seed, _truncate_body


_FRONTMATTER = """---
name: {name}
description: {desc}
---

# {name}

{body}
"""


def _make_skill_md(name: str, desc: str, body: str = "Some content.") -> str:
    return _FRONTMATTER.format(name=name, desc=desc, body=body)


# ── manager.create with trust + derived_from ──────────────────────────────


def test_manager_create_accepts_trust_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing"
    )
    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    mgr = SkillManager(reg)
    res = mgr.invoke(
        "create",
        {
            "name": "wizard-built",
            "content": _make_skill_md("wizard-built", "Wizard-built skill"),
            "trust": "user",
        },
    )
    assert res.ok, res.message
    meta = json.loads((user / "wizard-built" / ".meta.json").read_text())
    assert meta["trust"] == "user"


def test_manager_create_persists_derived_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing"
    )
    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    mgr = SkillManager(reg)
    derived = {
        "wizard_ask": "manage my calendar events",
        "wizard_built_at": "2026-04-30T12:00:00Z",
        "sources": [
            {
                "slug": "anthropics-skills",
                "url": "https://github.com/anthropics/skills/blob/main/calendar/SKILL.md",
                "title": "Calendar",
            }
        ],
    }
    res = mgr.invoke(
        "create",
        {
            "name": "manage-calendar",
            "content": _make_skill_md("manage-calendar", "Calendar helper"),
            "trust": "user",
            "derived_from": derived,
        },
    )
    assert res.ok, res.message
    meta = json.loads((user / "manage-calendar" / ".meta.json").read_text())
    assert meta["derived_from"] == derived


def test_manager_create_invalid_trust_falls_back_to_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing"
    )
    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    mgr = SkillManager(reg)
    res = mgr.invoke(
        "create",
        {
            "name": "trusty",
            "content": _make_skill_md("trusty", "test"),
            "trust": "root",
        },
    )
    assert res.ok, res.message
    meta = json.loads((user / "trusty" / ".meta.json").read_text())
    assert meta["trust"] == "agent"  # invalid → default


def test_manager_create_default_trust_is_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing"
    )
    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    mgr = SkillManager(reg)
    res = mgr.invoke(
        "create",
        {"name": "selfauthored", "content": _make_skill_md("selfauthored", "test")},
    )
    assert res.ok, res.message
    meta = json.loads((user / "selfauthored" / ".meta.json").read_text())
    assert meta["trust"] == "agent"


# ── registry round-trip ────────────────────────────────────────────────────


def test_registry_loads_derived_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing"
    )
    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    mgr = SkillManager(reg)
    derived = {
        "wizard_ask": "summarize my emails",
        "wizard_built_at": "2026-04-30T12:00:00Z",
        "sources": [
            {"slug": "src", "url": "https://example.com/SKILL.md", "title": "Email"}
        ],
    }
    mgr.invoke(
        "create",
        {
            "name": "summarize-emails",
            "content": _make_skill_md("summarize-emails", "Summarize emails"),
            "trust": "user",
            "derived_from": derived,
        },
    )
    skill = reg.get("summarize-emails")
    assert skill.derived_from is not None
    assert skill.derived_from.wizard_ask == "summarize my emails"
    assert len(skill.derived_from.sources) == 1
    assert skill.derived_from.sources[0].slug == "src"


def test_registry_skill_without_derived_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing"
    )
    user = tmp_path / "user"
    reg = SkillRegistry(skills_dir=user)
    mgr = SkillManager(reg)
    mgr.invoke(
        "create",
        {"name": "noprovenance", "content": _make_skill_md("noprovenance", "x")},
    )
    skill = reg.get("noprovenance")
    assert skill.derived_from is None


def test_registry_corrupt_derived_from_does_not_break_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nexus.skills.registry._BUNDLED_SKILLS_DIR", tmp_path / "missing"
    )
    user = tmp_path / "user"
    user.mkdir(parents=True)
    skill_dir = user / "broken"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_make_skill_md("broken", "broken provenance"))
    # Garbage in .meta.json shouldn't break loading
    (skill_dir / ".meta.json").write_text(
        json.dumps({"trust": "user", "derived_from": "not a dict"})
    )
    reg = SkillRegistry(skills_dir=user)
    skill = reg.get("broken")
    assert skill.derived_from is None  # malformed → coerced to None


# ── discovery.load_candidate_by_id ─────────────────────────────────────────


def _make_candidate_record(source_slug: str, skill_slug: str, body: str = "Body.") -> Candidate:
    cls = Classification(
        title="Test",
        summary="A test candidate.",
        capabilities=("a", "b"),
        complexity=2,
        cost_tier="free",
        requires_keys=(KeyReq(name="FOO_KEY", vendor="Foo"),),
        risks=(),
        confidence=0.9,
        language="en",
    )
    return Candidate(
        id=f"{source_slug}--{skill_slug}",
        source_slug=source_slug,
        source_url=f"https://example.com/{skill_slug}",
        source_verified=True,
        skill_path=f"{skill_slug}/SKILL.md",
        body=body,
        body_hash="deadbeef",
        classification=cls,
    )


def test_load_candidate_by_id_returns_cached(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    candidate = _make_candidate_record("src", "test-skill")
    record = _candidate_to_cache(candidate)
    cache_path = cache / "src" / "test-skill.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(record))
    loaded = load_candidate_by_id(cache, "src--test-skill")
    assert loaded is not None
    assert loaded.id == "src--test-skill"
    assert loaded.classification.title == "Test"


def test_load_candidate_by_id_returns_none_for_missing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    assert load_candidate_by_id(cache, "src--missing") is None


def test_load_candidate_by_id_returns_none_for_malformed_id(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    assert load_candidate_by_id(cache, "no-double-dash") is None


def test_load_candidate_by_id_rejects_stale_classifier_version(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    candidate = _make_candidate_record("src", "test-skill")
    record = _candidate_to_cache(candidate)
    record["classifier_version"] = 999  # future version means stale relative to ours
    cache_path = cache / "src" / "test-skill.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(record))
    assert load_candidate_by_id(cache, "src--test-skill") is None


# ── seed composition ───────────────────────────────────────────────────────


def test_compose_build_seed_includes_user_ask_and_primary() -> None:
    primary = _make_candidate_record("src", "test-skill", body="primary body content")
    seed = _compose_build_seed(
        user_ask="manage my calendar",
        language="en",
        primary=primary,
        related=[],
    )
    assert "manage my calendar" in seed
    assert "primary body content" in seed
    assert "src--test-skill" in seed
    assert "skill-builder" in seed  # references the procedure skill


def test_compose_build_seed_with_related() -> None:
    primary = _make_candidate_record("src", "primary", body="primary body")
    rel1 = _make_candidate_record("src", "related-1", body="rel1 body")
    rel2 = _make_candidate_record("src", "related-2", body="rel2 body")
    seed = _compose_build_seed(
        user_ask="ask",
        language="pt-BR",
        primary=primary,
        related=[rel1, rel2],
    )
    assert "Related candidates" in seed
    assert "src--related-1" in seed
    assert "src--related-2" in seed
    assert "rel1 body" in seed
    assert "rel2 body" in seed
    assert "spawn_subagents" in seed


def test_compose_build_seed_no_related_omits_section() -> None:
    primary = _make_candidate_record("src", "test-skill")
    seed = _compose_build_seed(
        user_ask="ask",
        language="en",
        primary=primary,
        related=[],
    )
    assert "Related candidates" not in seed


def test_truncate_body_below_cap_passthrough() -> None:
    body = "small body"
    assert _truncate_body(body) == body


def test_truncate_body_above_cap_marks_truncation() -> None:
    body = "x" * 20_000
    out = _truncate_body(body)
    assert len(out) < 20_000
    assert "body truncated" in out
