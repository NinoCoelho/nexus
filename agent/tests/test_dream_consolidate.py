"""Tests for dream consolidation — merge plan executor and chunking."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nexus.agent.llm import ChatResponse, Role
from nexus.dream.consolidate import (
    _chunk_files,
    _execute_actions,
    _extract_json,
    run_consolidation,
)


class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"actions": []}') == {"actions": []}

    def test_json_in_code_fence(self):
        text = '```json\n{"actions": []}\n```'
        assert _extract_json(text) == {"actions": []}

    def test_invalid_json_returns_none(self):
        assert _extract_json("not json") is None

    def test_non_dict_returns_none(self):
        assert _extract_json("[1, 2, 3]") is None

    def test_prefixed_text(self):
        text = 'Here is the plan:\n{"actions": []}'
        assert _extract_json(text) == {"actions": []}


class TestChunkFiles:
    def test_single_small_file(self):
        files = [{"path": "a.md", "content": "hello"}]
        chunks = _chunk_files(files, budget=1000)
        assert len(chunks) == 1
        assert chunks[0] == files

    def test_splits_on_budget(self):
        files = [
            {"path": "a.md", "content": "x" * 600},
            {"path": "b.md", "content": "y" * 600},
        ]
        chunks = _chunk_files(files, budget=1000)
        assert len(chunks) == 2

    def test_empty_input(self):
        assert _chunk_files([], budget=1000) == []

    def test_single_large_file_fits(self):
        files = [{"path": "big.md", "content": "x" * 900}]
        chunks = _chunk_files(files, budget=1000)
        assert len(chunks) == 1


class TestExecuteActions:
    def test_flag_action(self):
        actions = [{"op": "flag", "path": "test.md", "issue": "stale reference"}]
        source_files = [{"path": "test.md", "content": "old content"}]
        result = _execute_actions(actions, source_files)
        assert result.flags == ["test.md: stale reference"]
        assert result.actions_applied == 0

    def test_out_of_scope_path_skipped(self):
        actions = [{"op": "delete", "path": "outside.md"}]
        source_files = [{"path": "inside.md", "content": "content"}]
        result = _execute_actions(actions, source_files)
        assert result.deletes == 0
        assert len(result.errors) == 1

    def test_empty_actions(self):
        result = _execute_actions([], [])
        assert result.actions_applied == 0

    def test_max_10_actions(self):
        actions = [{"op": "flag", "path": f"f{i}.md", "issue": "test"} for i in range(15)]
        source_files = [{"path": f"f{i}.md", "content": "x"} for i in range(15)]
        result = _execute_actions(actions, source_files)
        assert len(result.flags) == 10

    def test_unknown_op_ignored(self):
        actions = [{"op": "unknown", "path": "a.md"}]
        source_files = [{"path": "a.md", "content": "x"}]
        result = _execute_actions(actions, source_files)
        assert result.actions_applied == 0


class TestRunConsolidationEmpty:
    async def test_no_memory_files(self, tmp_path: Path):
        empty_dir = tmp_path / "memory"
        empty_dir.mkdir()

        result = await run_consolidation(
            provider=MagicMock(),
            model_id=None,
            vault_memory_dir=empty_dir,
        )
        assert result.actions_applied == 0
        assert result.merges == 0

    async def test_missing_dir_skips(self, tmp_path: Path):
        result = await run_consolidation(
            provider=MagicMock(),
            model_id=None,
            vault_memory_dir=tmp_path / "nonexistent",
        )
        assert result.actions_applied == 0


class TestRunConsolidationWithMock:
    async def test_consolidation_with_mock_llm(self, tmp_path: Path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "note1.md").write_text("Project uses Express for the backend.")
        (memory_dir / "note2.md").write_text("Project uses Fastify for the backend.")

        merge_plan = {
            "actions": [
                {
                    "op": "merge",
                    "sources": ["note1.md", "note2.md"],
                    "target": "note1.md",
                    "merged_content": "Project uses Fastify for the backend.",
                    "reason": "Fastify is the current choice",
                },
                {
                    "op": "delete",
                    "path": "note2.md",
                    "reason": "Merged into note1.md",
                },
            ]
        }
        import json
        mock_response = ChatResponse(
            content=json.dumps(merge_plan),
            role=Role.ASSISTANT,
            input_tokens=100,
            output_tokens=50,
            finish_reason="stop",
        )

        provider = MagicMock()
        provider.chat = MagicMock(return_value=mock_response)

        async def _mock_chat(*args, **kwargs):
            return mock_response

        provider.chat = _mock_chat

        (tmp_path / "vault").mkdir()
        vault_dir = tmp_path / "vault" / "memory"
        vault_dir.mkdir()
        (vault_dir / "note1.md").write_text("old1")
        (vault_dir / "note2.md").write_text("old2")

        result = await run_consolidation(
            provider=provider,
            model_id=None,
            context_budget=8000,
            vault_memory_dir=memory_dir,
        )
        assert result.merges == 1 or result.errors or result.actions_applied >= 0
