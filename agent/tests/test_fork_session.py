"""``fork_session`` must create a real session.

It used to be a stub: it assembled a keyword-matched summary and returned
``instructions: "The backend should create a child session"``, but nothing in
the backend handled that. Meanwhile the system prompt advertised the tool and
``context_status`` recommended it at the orange/red zones, so the model would
confidently tell the user it had started a new chat that did not exist.

Also covers ``purge_session_artifacts``: deleting a session used to leave its
rolling summary and the verbatim archive of its collapsed messages behind in
the (Syncthing-replicated) vault forever.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.agent.llm import ChatMessage, Role
from nexus.server.session_store import SessionStore
from nexus.tools import context_tool


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(db_path=tmp_path / "sessions.sqlite")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the session-memory dir at tmp_path and clear the store ref.

    Each test opts in to a store via ``context_tool.set_session_store`` so a
    leaked ref can't make another test pass for the wrong reason.
    """
    import nexus.agent.loop.summarize as summarize_mod

    monkeypatch.setattr(summarize_mod, "_session_memory_fn", lambda: tmp_path / "sm")
    monkeypatch.setattr(context_tool, "_STORE_REF", None)


def _run_fork(**args) -> dict:
    return json.loads(context_tool.handle_fork_session(args))


def test_reports_failure_when_unwired_instead_of_claiming_success() -> None:
    """The old stub always returned ok:true. Without a store there is nothing
    to create, and the model must be told so."""
    out = _run_fork(title="Phase 2")
    assert out["ok"] is False
    assert "summary" in out


def test_creates_a_real_child_session(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    parent = store.create()
    store.replace_history(
        parent.id,
        [
            ChatMessage(role=Role.USER, content="implement the parser in `app/parse.py`"),
            ChatMessage(role=Role.ASSISTANT, content="Decision: we'll go with a recursive descent."),
        ],
    )

    context_tool.set_session_store(store)
    monkeypatch.setattr(context_tool, "_current_session_id", lambda: parent.id)

    out = _run_fork(title="Phase 2: codegen")

    assert out["ok"] is True
    child_id = out["session_id"]
    assert child_id and child_id != parent.id

    # The session really exists, is linked to the parent, and is visible.
    row = store._loom._db.execute(
        "SELECT parent_session_id, hidden, title FROM sessions WHERE id = ?",
        (child_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == parent.id
    assert row[1] == 0, "a fork is user-visible, unlike a sub-agent"
    assert row[2] == "Phase 2: codegen"

    # …and it is seeded with the carried-over summary.
    child = store.get(child_id)
    assert child is not None
    assert len(child.history) == 1
    seeded = child.history[0]
    assert seeded.role == Role.SYSTEM
    assert "Session Memory" in (seeded.content or "")
    assert parent.id in (seeded.content or "")


def test_fork_inherits_the_parents_project(tmp_path, monkeypatch) -> None:
    """A forked chat must stay inside the project, or it vanishes from the
    Projects view."""
    store = _store(tmp_path)
    parent = store.create()
    store._loom._db.execute(
        "UPDATE sessions SET project_id = ? WHERE id = ?", ("proj-1", parent.id)
    )
    store._loom._db.commit()

    context_tool.set_session_store(store)
    monkeypatch.setattr(context_tool, "_current_session_id", lambda: parent.id)

    out = _run_fork(title="Next phase")
    assert out["ok"] is True

    row = store._loom._db.execute(
        "SELECT project_id FROM sessions WHERE id = ?", (out["session_id"],)
    ).fetchone()
    assert row[0] == "proj-1"


def test_fork_prefers_the_persisted_session_note(tmp_path, monkeypatch) -> None:
    """The LLM-written rolling note beats keyword extraction when present."""
    from nexus.agent.loop.summarize import persist_session_summary

    store = _store(tmp_path)
    parent = store.create()
    persist_session_summary(parent.id, "## Session Memory\n- **Goals:** ship the thing")

    context_tool.set_session_store(store)
    monkeypatch.setattr(context_tool, "_current_session_id", lambda: parent.id)

    out = _run_fork(title="Continued")
    assert out["ok"] is True
    assert "ship the thing" in out["summary"]


def test_purge_session_artifacts_removes_note_and_archive(tmp_path, monkeypatch) -> None:
    import nexus.context_artifacts as artifacts

    sm_dir = tmp_path / "sm"
    (sm_dir / ".parts").mkdir(parents=True)
    (sm_dir / "sess-1.md").write_text("note", encoding="utf-8")
    (sm_dir / ".parts" / "sess-1.jsonl").write_text("{}\n", encoding="utf-8")
    (sm_dir / "sess-2.md").write_text("other", encoding="utf-8")

    monkeypatch.setattr(artifacts, "vault_session_memory", lambda: sm_dir)

    removed = artifacts.purge_session_artifacts("sess-1")

    assert removed == 2
    assert not (sm_dir / "sess-1.md").exists()
    assert not (sm_dir / ".parts" / "sess-1.jsonl").exists()
    assert (sm_dir / "sess-2.md").exists(), "other sessions untouched"


def test_purge_is_a_noop_for_unknown_session(tmp_path, monkeypatch) -> None:
    import nexus.context_artifacts as artifacts

    monkeypatch.setattr(artifacts, "vault_session_memory", lambda: tmp_path / "sm")
    assert artifacts.purge_session_artifacts("nope") == 0
    assert artifacts.purge_session_artifacts("") == 0
