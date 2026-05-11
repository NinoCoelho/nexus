"""Tests for dream skill refinement."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from nexus.agent.llm import ChatResponse, Role
from nexus.dream.skill_refine import (
    _build_context,
    _hash_suggestion,
    _load_existing_skills,
    run_skill_refinement,
)
from nexus.dream.state import DreamStateStore


class TestHashSuggestion:
    def test_deterministic(self):
        assert _hash_suggestion("a", "b") == _hash_suggestion("a", "b")

    def test_different(self):
        assert _hash_suggestion("a", "b") != _hash_suggestion("c", "d")


class TestBuildContext:
    def test_empty(self):
        ctx = _build_context([], [])
        assert "Recent Sessions" in ctx

    def test_with_skills(self):
        skills = [{"name": "code-review", "description": "Reviews code"}]
        sessions = [{"session_id": "abc", "title": "T", "preview": "p", "date": "2026-01-01"}]
        ctx = _build_context(skills, sessions)
        assert "Existing Skills" in ctx
        assert "code-review" in ctx

    def test_no_skills(self):
        sessions = [{"session_id": "abc", "title": "T", "preview": "p", "date": "2026-01-01"}]
        ctx = _build_context([], sessions)
        assert "Existing Skills" not in ctx


class TestLoadExistingSkills:
    def test_no_skills_dir(self, tmp_path: Path):
        import nexus.dream.skill_refine as mod
        original = mod.Path

        class MockPath(type(original())):
            @staticmethod
            def home():
                return tmp_path

        mod.Path = MockPath
        try:
            skills = _load_existing_skills()
            assert skills == []
        finally:
            mod.Path = original

    def test_loads_skills(self, tmp_path: Path):
        skills_dir = tmp_path / ".nexus" / "skills"
        skill_dir = skills_dir / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Does things\n---\nBody"
        )

        import nexus.dream.skill_refine as mod
        original = mod.Path

        class MockPath(type(original())):
            @staticmethod
            def home():
                return tmp_path

        mod.Path = MockPath
        try:
            skills = _load_existing_skills()
            assert len(skills) == 1
            assert skills[0]["name"] == "my-skill"
            assert skills[0]["description"] == "Does things"
        finally:
            mod.Path = original


class TestRunSkillRefinement:
    async def test_with_mock_llm(self, tmp_path: Path):
        store = DreamStateStore(tmp_path / "state.sqlite")
        try:
            suggestions_json = {
                "suggestions": [
                    {
                        "name": "deploy-checklist",
                        "description": "Runs pre-deploy checks",
                        "reason": "Seen in 4 sessions across 3 days",
                        "evidence_sessions": ["a", "b", "c", "d"],
                        "draft_content": "---\nname: deploy-checklist\ndescription: Runs pre-deploy checks\n---\nSteps here",
                    },
                ]
            }
            mock_response = ChatResponse(
                content=json.dumps(suggestions_json),
                role=Role.ASSISTANT,
                input_tokens=300,
                output_tokens=150,
                finish_reason="stop",
            )

            async def _mock_chat(*args, **kwargs):
                return mock_response

            provider = MagicMock()
            provider.chat = _mock_chat

            import nexus.dream.skill_refine as mod
            orig_load = mod._load_existing_skills
            orig_sessions = mod._load_session_summaries
            orig_exists = mod._skill_exists

            mod._load_existing_skills = lambda: []
            mod._load_session_summaries = lambda **kw: [
                {"session_id": f"s{i}", "title": f"Deploy {i}", "preview": "deploying", "date": f"2026-01-{i+1:02d}"}
                for i in range(5)
            ]
            mod._skill_exists = lambda n: False

            class MockPath2(type(mod.Path())):
                @staticmethod
                def home():
                    return tmp_path

            orig_path = mod.Path
            mod.Path = MockPath2

            try:
                result = await run_skill_refinement(
                    provider=provider,
                    state_store=store,
                )
                assert len(result.suggestions) >= 1
                assert result.suggestions[0].name == "deploy-checklist"
            finally:
                mod._load_existing_skills = orig_load
                mod._load_session_summaries = orig_sessions
                mod._skill_exists = orig_exists
                mod.Path = orig_path
        finally:
            store.close()

    async def test_duplicate_skipped(self, tmp_path: Path):
        store = DreamStateStore(tmp_path / "state.sqlite")
        try:
            content_hash = _hash_suggestion("deploy-checklist", "Runs pre-deploy checks")
            store.mark_explored(content_hash)

            suggestions_json = {
                "suggestions": [
                    {
                        "name": "deploy-checklist",
                        "description": "Runs pre-deploy checks",
                        "reason": "duplicate",
                        "evidence_sessions": ["a"],
                        "draft_content": "---\nname: deploy-checklist\ndescription: Runs checks\n---\n",
                    },
                ]
            }
            mock_response = ChatResponse(
                content=json.dumps(suggestions_json),
                role=Role.ASSISTANT,
                input_tokens=100,
                output_tokens=50,
                finish_reason="stop",
            )

            async def _mock_chat(*args, **kwargs):
                return mock_response

            provider = MagicMock()
            provider.chat = _mock_chat

            import nexus.dream.skill_refine as mod
            orig_load = mod._load_existing_skills
            orig_sessions = mod._load_session_summaries
            orig_exists = mod._skill_exists
            orig_path = mod.Path

            mod._load_existing_skills = lambda: []
            mod._load_session_summaries = lambda **kw: [
                {"session_id": f"s{i}", "title": f"T{i}", "preview": "p", "date": "2026-01-01"}
                for i in range(5)
            ]
            mod._skill_exists = lambda n: False

            class MockPath(type(orig_path())):
                @staticmethod
                def home():
                    return tmp_path

            mod.Path = MockPath
            try:
                result = await run_skill_refinement(
                    provider=provider,
                    state_store=store,
                )
                assert len(result.suggestions) == 0
            finally:
                mod._load_existing_skills = orig_load
                mod._load_session_summaries = orig_sessions
                mod._skill_exists = orig_exists
                mod.Path = orig_path
        finally:
            store.close()

    async def test_existing_skill_not_suggested(self, tmp_path: Path):
        store = DreamStateStore(tmp_path / "state.sqlite")
        try:
            suggestions_json = {
                "suggestions": [
                    {
                        "name": "existing-skill",
                        "description": "Already exists",
                        "reason": "test",
                        "evidence_sessions": ["a"],
                        "draft_content": "---\nname: existing-skill\ndescription: exists\n---\n",
                    },
                ]
            }
            mock_response = ChatResponse(
                content=json.dumps(suggestions_json),
                role=Role.ASSISTANT,
                input_tokens=100,
                output_tokens=50,
                finish_reason="stop",
            )

            async def _mock_chat(*args, **kwargs):
                return mock_response

            provider = MagicMock()
            provider.chat = _mock_chat

            import nexus.dream.skill_refine as mod
            orig_load = mod._load_existing_skills
            orig_sessions = mod._load_session_summaries
            orig_exists = mod._skill_exists
            orig_path = mod.Path

            mod._load_existing_skills = lambda: [{"name": "existing-skill", "description": "exists"}]
            mod._load_session_summaries = lambda **kw: [
                {"session_id": f"s{i}", "title": f"T{i}", "preview": "p", "date": "2026-01-01"}
                for i in range(5)
            ]
            mod._skill_exists = lambda n: n == "existing-skill"

            class MockPath(type(orig_path())):
                @staticmethod
                def home():
                    return tmp_path

            mod.Path = MockPath
            try:
                result = await run_skill_refinement(
                    provider=provider,
                    state_store=store,
                )
                assert len(result.suggestions) == 0
            finally:
                mod._load_existing_skills = orig_load
                mod._load_session_summaries = orig_sessions
                mod._skill_exists = orig_exists
                mod.Path = orig_path
        finally:
            store.close()
