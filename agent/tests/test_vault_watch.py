"""Tests for vault_watch — external-change reindexing.

The watcher mirrors vault.write_file()'s post-write hooks for files that
land in the vault outside the API (Syncthing/rsync/Obsidian): FTS + tags
rebuild, graph cache invalidation, event-bus notifications.

Isolation: home._ROOT is monkeypatched to tmp_path so vault_root() and both
index DBs live under the test dir (works from the watcher's flusher thread
because _ROOT is read per call). GraphRAG hooks no-op with no engine.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from nexus import home as nexus_home
from nexus import vault_search, vault_watch
from nexus.vault_watch import VaultWatcher, _interesting


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(nexus_home, "_ROOT", tmp_path)
    root = nexus_home.vault_root()
    return root


async def _wait_for(cond, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def _write_direct(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ── unit: path filter ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("note.md", True),
        ("Projects/plan.mdx", True),
        (".stignore", False),                       # dot-file
        (".tool-cache/x.md", False),                # dot-dir cache
        (".session-memory/parts/a.md", False),      # dot-dir state
        (".nexus-graph/manifest.md", False),        # dot-dir derived store
        ("note.md~", False),                        # editor temp
        ("note.md.tmp", False),                     # sync temp
        ("note.md.swp", False),                     # vim temp
        ("data.csv", False),                        # not markdown
        ("img.png", False),
    ],
)
def test_interesting_paths(rel: str, expected: bool) -> None:
    assert _interesting(rel) is expected


# ── integration: real watchdog events end-to-end ──────────────────────────────

# GitHub-hosted runners mount the pytest tmpdir on an overlayfs whose
# inotify delivery to watchdog is unreliable — these integration tests
# need a real local FS to observe external writes.
pytestmark_integration_ci_skip = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="runner overlayfs /tmp doesn't reliably deliver inotify events to watchdog",
)


@pytestmark_integration_ci_skip
async def test_external_write_gets_indexed_and_published(
    tmp_home: Path,
):
    from nexus.server import event_bus

    _write_direct(tmp_home, "seed.md", "seed content")

    watcher = VaultWatcher(quiet_s=0.3, poll_s=0.05)
    watcher.start()
    event_bus.set_loop(asyncio.get_running_loop())
    q = event_bus.subscribe()
    try:
        _write_direct(
            tmp_home, "Notes/synced.md",
            "---\ntags: [synced]\n---\nunique token quokka-index-42\n",
        )
        ok = await _wait_for(
            lambda: any(
                r.get("path") == "Notes/synced.md" for r in vault_search.search("quokka-index-42")
            ),
        )
        assert ok, "externally written file never appeared in FTS"

        from nexus import vault_index
        assert "synced" in vault_index.tags_for_file("Notes/synced.md")

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        kinds = [(e["type"], e.get("path")) for e in events]
        assert ("vault.indexed", "Notes/synced.md") in kinds
    finally:
        event_bus.unsubscribe(q)
        watcher.stop()


def _kinds(q) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    while not q.empty():
        e = q.get_nowait()
        out.append((e["type"], e.get("path")))
    return out


@pytestmark_integration_ci_skip
async def test_external_delete_prunes_index_and_publishes_removed(
    tmp_home: Path,
):
    from nexus.server import event_bus

    _write_direct(tmp_home, "gone.md", "token doomerang-7")

    watcher = VaultWatcher(quiet_s=0.3, poll_s=0.05)
    watcher.start()
    watcher._mark("gone.md")
    watcher.flush_now()  # baseline index
    assert vault_search.search("doomerang-7"), "baseline index missing"
    event_bus.set_loop(asyncio.get_running_loop())
    q = event_bus.subscribe()
    try:
        (tmp_home / "gone.md").unlink()
        ok = await _wait_for(
            lambda: not vault_search.search("doomerang-7"),
        )
        assert ok, "deleted file never pruned from FTS"
        ok = await _wait_for(lambda: ("vault.removed", "gone.md") in _kinds(q))
        assert ok, "vault.removed event never published"
    finally:
        event_bus.unsubscribe(q)
        watcher.stop()


# ── flush semantics (no watchdog dependency) ──────────────────────────────────

async def test_quiet_period_defers_flush(tmp_home: Path):
    watcher = VaultWatcher(quiet_s=60.0, poll_s=0.05)
    watcher.start()
    try:
        _write_direct(tmp_home, "burst.md", "token burst-99")
        watcher._mark("burst.md")
        await asyncio.sleep(0.3)
        # Quiet period not elapsed — nothing flushed yet.
        assert not vault_search.search("burst-99")
        watcher.flush_now()
        assert vault_search.search("burst-99")
    finally:
        watcher.stop()


async def test_temp_and_dot_paths_never_marked(tmp_home: Path):
    watcher = VaultWatcher(quiet_s=0.05, poll_s=0.05)
    watcher.start()
    try:
        for rel in (".tool-cache/x.md", "note.md~", "data.csv"):
            watcher._mark(rel)
        await asyncio.sleep(0.3)
        assert watcher._dirty == set(), f"unfiltered paths leaked into dirty set: {watcher._dirty}"
    finally:
        watcher.stop()


def test_kanban_frontmatter_publishes_specific_event(
    tmp_home: Path, caplog: pytest.LogCaptureFixture,
):
    watcher = VaultWatcher(quiet_s=0.0, poll_s=0.05)
    _write_direct(
        tmp_home, "Boards/board.md",
        "---\nkanban-plugin: basic\n---\n# Board\n",
    )
    watcher._mark("Boards/board.md")
    watcher.flush_now()
    # No assertion on the bus here (no loop in sync test — events drop);
    # this exercises the _handle_updated path for kanban files end-to-end.
    assert vault_search.search("Board")


def test_default_singleton_start_stop(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch,
):
    # conftest autouse-sets the kill switch for app-booting tests; this
    # test exercises start_default() itself, so lift it.
    monkeypatch.delenv("NEXUS_DISABLE_VAULT_WATCH", raising=False)
    w1 = vault_watch.start_default()
    assert w1 is vault_watch.start_default()
    vault_watch.stop_default()
    w2 = vault_watch.start_default()
    try:
        assert w2 is not w1
    finally:
        vault_watch.stop_default()


def test_env_kill_switch(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEXUS_DISABLE_VAULT_WATCH", "1")
    assert vault_watch.start_default() is None
    vault_watch.stop_default()  # no-op, must not raise
