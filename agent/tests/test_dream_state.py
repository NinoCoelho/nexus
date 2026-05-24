"""Tests for the dream state store."""

from pathlib import Path

import pytest

from nexus.dream.state import DreamStateStore


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "dream_state.sqlite"
    s = DreamStateStore(db)
    yield s
    s.close()


class TestDreamStateStoreRunLifecycle:
    def test_start_and_finish_run(self, store: DreamStateStore):
        run_id = store.start_run(depth="light", phases="consolidation")
        assert run_id > 0
        assert store.is_running()

        store.finish_run(
            run_id,
            status="done",
            tokens_in=100,
            tokens_out=50,
            duration_ms=2000,
            memories_merged=3,
            insights_generated=1,
        )
        assert not store.is_running()

        last = store.last_run()
        assert last is not None
        assert last.id == run_id
        assert last.depth == "light"
        assert last.status == "done"
        assert last.tokens_in == 100
        assert last.tokens_out == 50
        assert last.duration_ms == 2000
        assert last.memories_merged == 3
        assert last.insights_generated == 1

    def test_finish_run_with_error(self, store: DreamStateStore):
        run_id = store.start_run(depth="medium")
        store.finish_run(run_id, status="failed", error="timeout after 300s")
        assert not store.is_running()

        last = store.last_run()
        assert last is not None
        assert last.status == "failed"
        assert last.error == "timeout after 300s"

    def test_no_runs_initially(self, store: DreamStateStore):
        assert not store.is_running()
        assert store.last_run() is None

    def test_list_runs_ordered_newest_first(self, store: DreamStateStore):
        r1 = store.start_run(depth="light")
        store.finish_run(r1, status="done")
        r2 = store.start_run(depth="medium")
        store.finish_run(r2, status="done")
        r3 = store.start_run(depth="deep")
        store.finish_run(r3, status="done")

        runs = store.list_runs()
        assert len(runs) == 3
        assert runs[0].id == r3
        assert runs[1].id == r2
        assert runs[2].id == r1

    def test_list_runs_pagination(self, store: DreamStateStore):
        for _ in range(5):
            rid = store.start_run(depth="light")
            store.finish_run(rid, status="done")

        page1 = store.list_runs(limit=3, offset=0)
        assert len(page1) == 3
        page2 = store.list_runs(limit=3, offset=3)
        assert len(page2) == 2


class TestDreamStateStoreConcurrencyLock:
    def test_is_running_while_active(self, store: DreamStateStore):
        assert not store.is_running()
        store.start_run(depth="light")
        assert store.is_running()

    def test_is_running_clears_on_finish(self, store: DreamStateStore):
        run_id = store.start_run(depth="light")
        store.finish_run(run_id, status="done")
        assert not store.is_running()

    def test_multiple_unfinished_runs_show_running(self, store: DreamStateStore):
        store.start_run(depth="light")
        store.start_run(depth="medium")
        assert store.is_running()
        assert store.list_runs(limit=10)[0].status == "running"


class TestDreamStateStoreBudget:
    def test_budget_zero_initially(self, store: DreamStateStore):
        assert store.budget_used_today() == 0

    def test_add_budget_spend_accumulates(self, store: DreamStateStore):
        store.add_budget_spend(100)
        assert store.budget_used_today() == 100
        store.add_budget_spend(50)
        assert store.budget_used_today() == 150

    def test_budget_per_day(self, store: DreamStateStore):
        store.add_budget_spend(200)
        assert store.budget_used_today() == 200


class TestDreamStateStoreExploredTerritory:
    def test_not_explored_initially(self, store: DreamStateStore):
        assert not store.has_explored("hash1")

    def test_mark_and_check_explored(self, store: DreamStateStore):
        store.mark_explored("hash1")
        assert store.has_explored("hash1")
        assert not store.has_explored("hash2")

    def test_mark_idempotent(self, store: DreamStateStore):
        store.mark_explored("hash1")
        store.mark_explored("hash1")
        assert store.has_explored("hash1")

    def test_cleanup_territory(self, store: DreamStateStore):
        store.mark_explored("old_hash")
        store.mark_explored("new_hash")
        count = store.cleanup_territory(max_age_days=0)
        assert count >= 1


class TestDreamStateStoreClose:
    def test_close_idempotent(self, tmp_path: Path):
        s = DreamStateStore(tmp_path / "test.db")
        s.close()
        s.close()
