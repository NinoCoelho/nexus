"""Tests for the skill wizard discovery service.

Phase 1 covers: source loading, classifier JSON parsing, ranking,
refresh-then-cache, and end-to-end discover with stubbed network + LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.skills.discovery import (
    Classification,
    KeyReq,
    ScoredCandidate,
    SkillDiscovery,
    Source,
    load_sources,
    parse_classification,
    score_candidate,
)


_SKILL_BODY_BRAINSTORM = """---
name: brainstorm
description: Generate a structured set of ideas around a problem statement.
---

# Brainstorm

Use this skill when the user asks for ideas around a topic.
"""

_SKILL_BODY_CALENDAR = """---
name: calendar
description: Read and create events on the user's calendar.
requires_keys:
  - name: GOOGLE_CALENDAR_API_KEY
    help: Google Calendar API key
    url: https://console.cloud.google.com
---

# Calendar

Sync events between the user's natural-language requests and Google Calendar.
"""


def _make_source(slug: str = "test-src", verified: bool = True) -> Source:
    return Source(
        slug=slug,
        kind="github",
        owner="example",
        repo="skills",
        branch="main",
        description="test source",
        verified=verified,
    )


def _classification_for(title: str, summary: str, capabilities: list[str]) -> Classification:
    return Classification(
        title=title,
        summary=summary,
        capabilities=tuple(capabilities),
        complexity=2,
        cost_tier="free",
        requires_keys=(),
        risks=(),
        confidence=0.9,
        language="en",
    )


# ── load_sources ───────────────────────────────────────────────────────────


def test_load_sources_reads_builtin_json(tmp_path: Path) -> None:
    builtin = tmp_path / "external_sources.json"
    builtin.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "slug": "anthropics-skills",
                        "kind": "github",
                        "owner": "anthropics",
                        "repo": "skills",
                        "branch": "main",
                        "description": "Anthropic official",
                        "verified": True,
                    }
                ]
            }
        )
    )
    sources = load_sources(builtin_path=builtin)
    assert len(sources) == 1
    assert sources[0].slug == "anthropics-skills"
    assert sources[0].verified is True


def test_load_sources_user_entries_marked_unverified(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin.json"
    user = tmp_path / "user.json"
    builtin.write_text(json.dumps({"sources": []}))
    user.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "slug": "random-pack",
                        "kind": "github",
                        "owner": "rando",
                        "repo": "pack",
                        "branch": "main",
                        "description": "user-added",
                    }
                ]
            }
        )
    )
    sources = load_sources(builtin_path=builtin, user_path=user)
    assert len(sources) == 1
    assert sources[0].slug == "random-pack"
    assert sources[0].verified is False


def test_load_sources_dedups_by_slug(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin.json"
    user = tmp_path / "user.json"
    entry = {
        "slug": "dup",
        "kind": "github",
        "owner": "a",
        "repo": "b",
        "branch": "main",
    }
    builtin.write_text(json.dumps({"sources": [entry]}))
    user.write_text(json.dumps({"sources": [entry]}))
    sources = load_sources(builtin_path=builtin, user_path=user)
    assert len(sources) == 1


# ── parse_classification ───────────────────────────────────────────────────


def test_parse_classification_strips_code_fences() -> None:
    raw = """```json
{"title": "Brainstorm", "summary": "Generates ideas.", "complexity": 1}
```"""
    cls = parse_classification(raw, language="en")
    assert cls.title == "Brainstorm"
    assert cls.complexity == 1


def test_parse_classification_handles_malformed_json() -> None:
    cls = parse_classification("not json at all", language="en")
    assert cls.title == "Untitled skill"
    assert cls.confidence == 0.0


def test_parse_classification_clamps_out_of_range() -> None:
    raw = json.dumps(
        {
            "title": "X",
            "summary": "Y",
            "complexity": 99,
            "cost_tier": "preposterous",
            "confidence": 1.5,
        }
    )
    cls = parse_classification(raw, language="en")
    assert cls.complexity == 5
    assert cls.cost_tier == "free"  # falls back when not in tier set
    assert cls.confidence == 1.0


def test_parse_classification_coerces_key_reqs() -> None:
    raw = json.dumps(
        {
            "title": "Cal",
            "summary": "Calendar",
            "requires_keys": [
                {
                    "name": "GOOGLE_CALENDAR_API_KEY",
                    "vendor": "Google",
                    "get_key_url": "https://example.com",
                    "free_tier_available": True,
                }
            ],
        }
    )
    cls = parse_classification(raw, language="en")
    assert len(cls.requires_keys) == 1
    k = cls.requires_keys[0]
    assert isinstance(k, KeyReq)
    assert k.name == "GOOGLE_CALENDAR_API_KEY"
    assert k.free_tier_available is True


# ── score_candidate ────────────────────────────────────────────────────────


def _make_candidate(skill_slug: str, classification: Classification, body: str = "x") -> "Candidate":  # noqa: F821
    from nexus.skills.discovery import Candidate

    return Candidate(
        id=f"src--{skill_slug}",
        source_slug="src",
        source_url=f"https://example.com/{skill_slug}",
        source_verified=True,
        skill_path=f"{skill_slug}/SKILL.md",
        body=body,
        body_hash="deadbeef",
        classification=classification,
    )


def test_score_candidate_keyword_overlap() -> None:
    cand = _make_candidate(
        "calendar",
        _classification_for(
            "Calendar Events", "Manage calendar events.", ["create events", "schedule"]
        ),
    )
    assert score_candidate(cand, "i want to manage my calendar events") > 0.3


def test_score_candidate_no_overlap_returns_zero() -> None:
    cand = _make_candidate(
        "spreadsheet",
        _classification_for("Spreadsheet", "Edit spreadsheets.", ["xlsx"]),
    )
    assert score_candidate(cand, "tell me about astronomy and stars") == 0.0


# ── refresh_source / cache ─────────────────────────────────────────────────


@pytest.fixture
def stub_pipeline(tmp_path: Path):
    """Provide stubs for path lister, body fetcher, and classifier with call counts."""
    calls = {"list": 0, "fetch": 0, "classify": 0}

    async def list_paths(source: Source) -> list[str]:
        calls["list"] += 1
        return ["brainstorm/SKILL.md", "calendar/SKILL.md"]

    async def fetch_body(source: Source, path: str) -> str:
        calls["fetch"] += 1
        if path.startswith("brainstorm"):
            return _SKILL_BODY_BRAINSTORM
        return _SKILL_BODY_CALENDAR

    async def classify(body: str, language: str) -> Classification:
        calls["classify"] += 1
        if "Brainstorm" in body:
            return _classification_for(
                "Brainstorm", "Generate ideas.", ["ideation"]
            )
        return _classification_for(
            "Calendar", "Manage calendar events.", ["events", "schedule"]
        )

    return tmp_path, calls, list_paths, fetch_body, classify


async def test_refresh_source_writes_cache_and_returns_candidates(stub_pipeline):
    tmp_path, calls, list_paths, fetch_body, classify = stub_pipeline
    discovery = SkillDiscovery(
        cache_dir=tmp_path / "cache",
        sources=[_make_source()],
        path_lister=list_paths,
        body_fetcher=fetch_body,
        classifier=classify,
    )
    cands = await discovery.refresh_source(_make_source(), language="en")
    assert len(cands) == 2
    titles = {c.classification.title for c in cands}
    assert titles == {"Brainstorm", "Calendar"}

    # Cache files written
    cache_dir = tmp_path / "cache" / "test-src"
    assert (cache_dir / "brainstorm.json").is_file()
    assert (cache_dir / "calendar.json").is_file()


async def test_refresh_source_uses_cache_on_second_call(stub_pipeline):
    tmp_path, calls, list_paths, fetch_body, classify = stub_pipeline
    discovery = SkillDiscovery(
        cache_dir=tmp_path / "cache",
        sources=[_make_source()],
        path_lister=list_paths,
        body_fetcher=fetch_body,
        classifier=classify,
    )
    await discovery.refresh_source(_make_source(), language="en")
    classify_calls_before = calls["classify"]
    fetch_calls_before = calls["fetch"]

    await discovery.refresh_source(_make_source(), language="en")

    # Path listing always re-runs (cheap), but body fetch + classifier should be cached.
    assert calls["classify"] == classify_calls_before
    assert calls["fetch"] == fetch_calls_before


async def test_refresh_source_force_reclassifies(stub_pipeline):
    tmp_path, calls, list_paths, fetch_body, classify = stub_pipeline
    discovery = SkillDiscovery(
        cache_dir=tmp_path / "cache",
        sources=[_make_source()],
        path_lister=list_paths,
        body_fetcher=fetch_body,
        classifier=classify,
    )
    await discovery.refresh_source(_make_source(), language="en")
    before = calls["classify"]
    await discovery.refresh_source(_make_source(), language="en", force=True)
    assert calls["classify"] == before + 2


async def test_refresh_source_language_change_invalidates_cache(stub_pipeline):
    tmp_path, calls, list_paths, fetch_body, classify = stub_pipeline
    discovery = SkillDiscovery(
        cache_dir=tmp_path / "cache",
        sources=[_make_source()],
        path_lister=list_paths,
        body_fetcher=fetch_body,
        classifier=classify,
    )
    await discovery.refresh_source(_make_source(), language="en")
    before = calls["classify"]
    await discovery.refresh_source(_make_source(), language="pt-BR")
    # Different language → cache miss → reclassify both candidates
    assert calls["classify"] == before + 2


# ── discover (end-to-end orchestrator) ─────────────────────────────────────


async def test_discover_returns_ranked_candidates(stub_pipeline):
    tmp_path, calls, list_paths, fetch_body, classify = stub_pipeline
    discovery = SkillDiscovery(
        cache_dir=tmp_path / "cache",
        sources=[_make_source()],
        path_lister=list_paths,
        body_fetcher=fetch_body,
        classifier=classify,
    )
    results: list[ScoredCandidate] = await discovery.discover(
        "manage my calendar events", language="en"
    )
    # Calendar should rank above Brainstorm for this ask
    assert len(results) >= 1
    assert results[0].candidate.classification.title == "Calendar"


async def test_discover_filters_zero_scores(stub_pipeline):
    tmp_path, calls, list_paths, fetch_body, classify = stub_pipeline
    discovery = SkillDiscovery(
        cache_dir=tmp_path / "cache",
        sources=[_make_source()],
        path_lister=list_paths,
        body_fetcher=fetch_body,
        classifier=classify,
    )
    results = await discovery.discover("astronomy stars galaxies", language="en")
    assert results == []


async def test_classify_with_llm_passes_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wizard holds a generic provider that wasn't constructed with a
    default model; classify_with_llm must forward the resolved model so
    provider.chat() doesn't raise ``LLMError("No model specified")``.
    """
    from nexus.skills.discovery import classify_with_llm

    captured: dict[str, object] = {}

    class FakeResponse:
        content = '{"title": "T", "summary": "S", "complexity": 1}'

    class FakeProvider:
        async def chat(self, messages, *, max_tokens=None, model=None, tools=None):
            captured["model"] = model
            captured["max_tokens"] = max_tokens
            return FakeResponse()

    cls = await classify_with_llm(
        FakeProvider(),  # type: ignore[arg-type]
        body="some skill body",
        language="en",
        model="claude-sonnet-4-6",
    )
    assert cls.title == "T"
    assert captured["model"] == "claude-sonnet-4-6"


async def test_discover_oversize_body_skipped(tmp_path: Path):
    async def list_paths(source: Source) -> list[str]:
        return ["huge/SKILL.md"]

    async def fetch_body(source: Source, path: str) -> str:
        return "x" * 200_000  # exceeds 100k cap

    classify_calls = 0

    async def classify(body: str, language: str) -> Classification:
        nonlocal classify_calls
        classify_calls += 1
        return _classification_for("Should not reach", "", [])

    discovery = SkillDiscovery(
        cache_dir=tmp_path / "cache",
        sources=[_make_source()],
        path_lister=list_paths,
        body_fetcher=fetch_body,
        classifier=classify,
    )
    cands = await discovery.refresh_source(_make_source(), language="en")
    assert cands == []
    assert classify_calls == 0
