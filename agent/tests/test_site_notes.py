"""Tests for the site-notes distiller (knowledge, not recipes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.browser import extract as page_extract
from nexus.browser import site_notes


@pytest.fixture()
def vault_sites(tmp_path: Path, monkeypatch):
    d = tmp_path / "sites"
    d.mkdir()
    monkeypatch.setattr(site_notes, "sites_dir", lambda: d)
    return d


def test_host_of_normalizes() -> None:
    assert site_notes.host_of("https://Admin.Cloud.Microsoft/x/y?z") == "admin.cloud.microsoft"
    assert site_notes.host_of("http://www.example.com/") == "example.com"
    assert site_notes.host_of("") == "unknown"


def test_note_excerpt_missing_and_present(vault_sites: Path) -> None:
    assert site_notes.note_excerpt("https://none.example/") is None
    (vault_sites / "portal.example.md").write_text("# Portal\n- knows things\n")
    hint = site_notes.note_excerpt("https://portal.example/deep/path")
    assert hint is not None
    assert "knows things" in hint["excerpt"]
    assert hint["path"] == "vault://sites/portal.example.md"


def test_note_excerpt_truncates(vault_sites: Path) -> None:
    (vault_sites / "big.example.md").write_text("x" * 5000)
    hint = site_notes.note_excerpt("https://big.example/")
    assert hint is not None
    assert len(hint["excerpt"]) <= site_notes.NOTE_EXCERPT_CHARS + 80
    assert "truncated" in hint["excerpt"]


async def test_distill_and_save_merges_and_writes(vault_sites: Path, monkeypatch) -> None:
    prompts: list[str] = []

    async def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return "## Navigation\n- Enterprise apps > API permissions\n## Behaviors\n- flyout appends at DOM end"

    page_extract.set_extract_llm(fake_llm)
    try:
        steps = [
            {"action": "navigate", "args": {"url": "https://admin.example/apps"}, "url": "https://admin.example/apps", "page_title": "Apps"},
            {"action": "read", "args": {}, "url": "https://admin.example/apps", "page_title": "Apps"},
            {"action": "click", "args": {"selector": "#add"}, "url": "https://admin.example/apps", "page_title": "Apps"},
            {"action": "click", "args": {"text": "Grant admin consent"}, "url": "https://admin.example/apps", "page_title": "Apps"},
        ]
        path = await site_notes.distill_and_save("https://admin.example/x", steps)
        assert path is not None and path.exists()
        note = path.read_text()
        assert "## Navigation" in note and "flyout" in note
        # the prompt carried the steps and no existing notes
        assert "click text=Grant admin consent" in prompts[0]
        assert "(none yet)" in prompts[0]

        # second episode: existing note is passed for incremental merge
        await site_notes.distill_and_save(
            "admin.example",
            [{"action": "click", "args": {"text": "Save"}, "url": "https://admin.example/apps", "page_title": ""}],
        )
        assert "flyout appends" in prompts[1]  # existing note in the prompt
    finally:
        page_extract.set_extract_llm(None)


async def test_distill_failure_returns_none(vault_sites: Path, monkeypatch) -> None:
    async def boom(prompt: str) -> str:
        raise RuntimeError("llm down")

    page_extract.set_extract_llm(boom)
    try:
        out = await site_notes.distill_and_save(
            "x.example", [{"action": "click", "args": {"text": "y"}, "url": "https://x.example/", "page_title": ""}]
        )
        assert out is None
    finally:
        page_extract.set_extract_llm(None)
