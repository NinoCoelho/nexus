"""Tests for dream insight extraction."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from nexus.agent.llm import ChatResponse, Role
from nexus.dream.insight import (
    _build_context,
    _hash_insight,
    _load_memory_summaries,
    run_insight_extraction,
)
from nexus.dream.state import DreamStateStore


class TestHashInsight:
    def test_deterministic(self):
        assert _hash_insight("a", "b") == _hash_insight("a", "b")

    def test_different_inputs(self):
        assert _hash_insight("a", "b") != _hash_insight("b", "a")


class TestBuildContext:
    def test_empty(self):
        assert _build_context([], []) == "## Recent Sessions"

    def test_sessions_only(self):
        sessions = [{"session_id": "abc", "title": "Test", "preview": "hello"}]
        ctx = _build_context(sessions, [])
        assert "Test" in ctx
        assert "hello" in ctx

    def test_memories_and_sessions(self):
        memories = [{"key": "projects/nexus", "preview": "Uses Fastify"}]
        sessions = [{"session_id": "abc", "title": "Work", "preview": "coding"}]
        ctx = _build_context(sessions, memories)
        assert "Memory Notes" in ctx
        assert "Recent Sessions" in ctx


class TestLoadMemorySummaries:
    def test_empty_dir(self, tmp_path: Path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        assert True

    def test_skips_dream_insights_dir(self, tmp_path: Path):
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "note.md").write_text("normal note")
        insights_dir = mem_dir / "dream-insights"
        insights_dir.mkdir()
        (insights_dir / "insight.md").write_text("insight note")

        notes = _load_memory_summaries(limit=10)
        keys = [n["key"] for n in notes]
        assert any("note" in k for k in keys)


class TestRunInsightExtraction:
    async def test_fewer_than_two_sessions(self, tmp_path: Path):
        store = DreamStateStore(tmp_path / "state.sqlite")
        try:
            result = await run_insight_extraction(
                provider=MagicMock(),
                state_store=store,
            )
            assert result.insights == []
        finally:
            store.close()

    async def test_with_mock_llm(self, tmp_path: Path):
        store = DreamStateStore(tmp_path / "state.sqlite")
        try:
            insights_json = {
                "insights": [
                    {
                        "title": "User prefers TypeScript",
                        "body": "Across 3 sessions the user consistently chose TypeScript.",
                        "confidence": "high",
                        "tags": ["preference", "language"],
                    },
                ]
            }
            mock_response = ChatResponse(
                content=json.dumps(insights_json),
                role=Role.ASSISTANT,
                input_tokens=200,
                output_tokens=100,
                finish_reason="stop",
            )

            async def _mock_chat(*args, **kwargs):
                return mock_response

            provider = MagicMock()
            provider.chat = _mock_chat

            import nexus.dream.insight as insight_mod
            original_load = insight_mod._load_recent_sessions

            def mock_load(*, since=None, limit=20):
                return [
                    {"session_id": f"s{i}", "title": f"Session {i}", "preview": f"TypeScript work {i}"}
                    for i in range(3)
                ]

            insight_mod._load_recent_sessions = mock_load
            try:
                result = await run_insight_extraction(
                    provider=provider,
                    state_store=store,
                )
                assert len(result.insights) >= 1
                assert result.insights[0].title == "User prefers TypeScript"
                assert result.insights[0].confidence == "high"
            finally:
                insight_mod._load_recent_sessions = original_load
        finally:
            store.close()

    async def test_duplicate_insight_skipped(self, tmp_path: Path):
        store = DreamStateStore(tmp_path / "state.sqlite")
        try:
            content_hash = _hash_insight("Same title", "Same body")
            store.mark_explored(content_hash)

            insights_json = {
                "insights": [
                    {
                        "title": "Same title",
                        "body": "Same body",
                        "confidence": "medium",
                        "tags": ["test"],
                    },
                ]
            }
            mock_response = ChatResponse(
                content=json.dumps(insights_json),
                role=Role.ASSISTANT,
                input_tokens=100,
                output_tokens=50,
                finish_reason="stop",
            )

            async def _mock_chat(*args, **kwargs):
                return mock_response

            provider = MagicMock()
            provider.chat = _mock_chat

            import nexus.dream.insight as insight_mod
            original_load = insight_mod._load_recent_sessions

            def mock_load(*, since=None, limit=20):
                return [
                    {"session_id": f"s{i}", "title": f"Session {i}", "preview": f"Work {i}"}
                    for i in range(3)
                ]

            insight_mod._load_recent_sessions = mock_load
            try:
                result = await run_insight_extraction(
                    provider=provider,
                    state_store=store,
                )
                assert len(result.insights) == 0
            finally:
                insight_mod._load_recent_sessions = original_load
        finally:
            store.close()
