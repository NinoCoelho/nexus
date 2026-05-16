"""Tests for dream scenario rehearsal."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from nexus.agent.llm import ChatResponse, Role
from nexus.dream.rehearse import (
    _build_context,
    _load_recent_insights,
    run_scenario_rehearsal,
)


class TestBuildContext:
    def test_sessions_only(self):
        sessions = [{"session_id": "a", "title": "Work", "preview": "coding"}]
        ctx = _build_context(sessions, [])
        assert "Recent Sessions" in ctx
        assert "Dream Insights" not in ctx

    def test_with_insights(self):
        sessions = [{"session_id": "a", "title": "Work", "preview": "coding"}]
        insights = ["User prefers TypeScript"]
        ctx = _build_context(sessions, insights)
        assert "Dream Insights" in ctx
        assert "TypeScript" in ctx

    def test_empty(self):
        ctx = _build_context([], [])
        assert "Recent Sessions" in ctx


class TestLoadRecentInsights:
    def test_no_dir(self, tmp_path: Path):
        result = _load_recent_insights(insights_dir=tmp_path / "nonexistent")
        assert result == []

    def test_loads_insights(self, tmp_path: Path):
        insights_dir = tmp_path / "insights"
        insights_dir.mkdir()
        (insights_dir / "insight1.md").write_text(
            "---\ntags: [dream-insight]\n---\n\nTypeScript preference"
        )
        result = _load_recent_insights(insights_dir=insights_dir)
        assert len(result) == 1
        assert "TypeScript" in result[0]

    def test_limits(self, tmp_path: Path):
        insights_dir = tmp_path / "insights"
        insights_dir.mkdir()
        for i in range(5):
            (insights_dir / f"ins{i}.md").write_text(f"---\n---\n\nInsight {i}")
        result = _load_recent_insights(limit=2, insights_dir=insights_dir)
        assert len(result) == 2


class TestRunScenarioRehearsal:
    async def test_no_sessions(self):
        import nexus.dream.rehearse as mod
        orig = mod._load_recent_sessions
        mod._load_recent_sessions = lambda **kw: []
        try:
            result = await run_scenario_rehearsal(
                provider=MagicMock(),
                state_store=MagicMock(),
            )
            assert result.scenarios == []
        finally:
            mod._load_recent_sessions = orig

    async def test_with_mock_llm(self, tmp_path: Path):
        scenarios_json = {
            "scenarios": [
                {
                    "title": "Deploy to staging",
                    "likelihood": "high",
                    "predicted_task": "User will deploy the API to staging",
                    "precomputed_note": "Recent sessions show deploy commands. Run: uv run pytest first.",
                    "tags": ["deploy"],
                },
            ]
        }
        mock_response = ChatResponse(
            content=json.dumps(scenarios_json),
            role=Role.ASSISTANT,
            input_tokens=200,
            output_tokens=100,
            finish_reason="stop",
        )

        async def _mock_chat(*args, **kwargs):
            return mock_response

        provider = MagicMock()
        provider.chat = _mock_chat

        import nexus.dream.rehearse as mod
        orig_sessions = mod._load_recent_sessions
        orig_insights = mod._load_recent_insights
        orig_path = mod.Path

        mod._load_recent_sessions = lambda **kw: [
            {"session_id": f"s{i}", "title": f"Deploy {i}", "preview": f"deploying {i}"}
            for i in range(5)
        ]
        mod._load_recent_insights = lambda **kw: []

        class MockPath(type(orig_path())):
            @staticmethod
            def home():
                return tmp_path

        mod.Path = MockPath
        try:
            result = await run_scenario_rehearsal(
                provider=provider,
                state_store=MagicMock(),
            )
            assert len(result.scenarios) >= 1
            assert result.scenarios[0].title == "Deploy to staging"
            assert result.scenarios[0].likelihood == "high"
        finally:
            mod._load_recent_sessions = orig_sessions
            mod._load_recent_insights = orig_insights
            mod.Path = orig_path

    async def test_llm_failure(self):
        provider = MagicMock()

        async def _fail_chat(*args, **kwargs):
            raise RuntimeError("API down")

        provider.chat = _fail_chat

        import nexus.dream.rehearse as mod
        orig = mod._load_recent_sessions
        mod._load_recent_sessions = lambda **kw: [
            {"session_id": "s1", "title": "T", "preview": "p"}
        ]
        try:
            result = await run_scenario_rehearsal(
                provider=provider,
                state_store=MagicMock(),
            )
            assert result.scenarios == []
            assert len(result.errors) == 1
        finally:
            mod._load_recent_sessions = orig
