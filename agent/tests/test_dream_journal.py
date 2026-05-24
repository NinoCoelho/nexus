"""Tests for the dream journal writer."""

from pathlib import Path

from nexus.dream.consolidate import ConsolidationResult
from nexus.dream.journal import write_journal


class TestDreamJournal:
    def test_creates_journal_entry(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("nexus.dream.journal._DREAMS_DIR", tmp_path / "dreams")

        path = write_journal(
            run_id=1,
            depth="medium",
            phases_run=["consolidation", "insight"],
            tokens_in=500,
            tokens_out=200,
            duration_ms=3500,
        )

        assert path.startswith("dreams/")
        journal = tmp_path / "dreams" / path.split("/")[-1]
        assert journal.exists()
        content = journal.read_text()
        assert "Dream Run #1" in content
        assert "consolidation" in content
        assert "insight" in content
        assert "3.5s" in content
        assert "500" in content

    def test_appends_to_existing_journal(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("nexus.dream.journal._DREAMS_DIR", tmp_path / "dreams")

        write_journal(run_id=1, depth="light", phases_run=["consolidation"])
        write_journal(run_id=2, depth="medium", phases_run=["consolidation", "insight"])

        journal_dir = tmp_path / "dreams"
        files = list(journal_dir.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "Dream Run #1" in content
        assert "Dream Run #2" in content

    def test_journal_with_consolidation_result(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("nexus.dream.journal._DREAMS_DIR", tmp_path / "dreams")

        result = ConsolidationResult(
            actions_applied=5,
            merges=2,
            updates=1,
            deletes=1,
            flags=["note.md: stale ref"],
            errors=[],
        )
        write_journal(
            run_id=3,
            depth="deep",
            phases_run=["consolidation"],
            consolidation=result,
            tokens_in=800,
            tokens_out=300,
            duration_ms=5000,
        )

        journal_dir = tmp_path / "dreams"
        content = list(journal_dir.glob("*.md"))[0].read_text()
        assert "Merges: 2" in content
        assert "Updates: 1" in content
        assert "Deletes: 1" in content
        assert "stale ref" in content

    def test_journal_with_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("nexus.dream.journal._DREAMS_DIR", tmp_path / "dreams")

        write_journal(
            run_id=4,
            depth="light",
            phases_run=["consolidation"],
            error="LLM call failed: timeout",
        )

        journal_dir = tmp_path / "dreams"
        content = list(journal_dir.glob("*.md"))[0].read_text()
        assert "Error:" in content
        assert "timeout" in content
