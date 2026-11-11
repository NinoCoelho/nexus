"""``history_recall`` — the agent-facing reader over the compaction archive.

Compaction is deliberately reversible: everything summarized or elided is
journaled verbatim to ``vault/.session-memory/.parts/<sid>.jsonl``. Until now
that archive had no reader — the elision marker literally told the model to
"ask the user". These tests pin the loop the tool closes:

    persist → index → search (live history + archive) → fetch verbatim

including the cross-session property ("all history is a plus") and the
``live:`` / ``part:`` ref round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.agent.context import CURRENT_HISTORY
from nexus.agent.llm import ChatMessage, Role
from nexus.agent.loop import parts_index
from nexus.agent.loop.summarize import persist_summary_part
from nexus.server.session_store import SessionStore
from nexus.tools import history_recall_tool
from nexus.tools.history_recall_tool import HISTORY_RECALL_TOOL, handle_history_recall


def _call(args: dict) -> dict:
    return json.loads(handle_history_recall(args))


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated home root + store, wired into both tool modules.

    Redirects via ``set_user_home`` — the ContextVar every vault path
    function resolves through — instead of monkeypatching
    ``vault_session_memory`` attributes. Import-time bindings (e.g.
    ``context_artifacts`` imports the function directly) would miss an
    attribute patch depending on which test module imported them first,
    which is exactly the cross-test pollution that made the purge test
    pass alone and fail in the full suite.
    """
    import nexus.home as home_mod

    prev_home = home_mod._USER_HOME_DIR.get(None)
    home_mod.set_user_home(tmp_path)

    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    monkeypatch.setattr(history_recall_tool, "_STORE_REF", store)
    monkeypatch.setattr(parts_index, "_STORE_REF", store)
    monkeypatch.setattr(parts_index, "_backfill_done", False)
    yield store, tmp_path / "vault" / ".session-memory"
    home_mod.set_user_home(prev_home)
    parts_index.set_parts_store(None)
    history_recall_tool.set_session_store(None)


def _u(text: str) -> ChatMessage:
    return ChatMessage(role=Role.USER, content=text)


def _a(text: str) -> ChatMessage:
    return ChatMessage(role=Role.ASSISTANT, content=text)


def test_tool_registered_with_three_actions() -> None:
    props = HISTORY_RECALL_TOOL.parameters["properties"]
    assert props["action"]["enum"] == ["search", "get", "recent"]
    assert "query" in props and "ref" in props


def test_search_finds_live_history_across_sessions(env) -> None:
    store, _ = env
    s1 = store.create()
    store.replace_history(s1.id, [
        _u("what's our stripe pricing threshold?"),
        _a("The Stripe pricing threshold we set was $2,400 monthly."),
    ])
    s2 = store.create()
    store.replace_history(s2.id, [
        _u("remind me about the deploy window"),
        _a("Deploys are frozen on Fridays; the window is Tuesday 14:00 UTC."),
    ])
    data = _call({"action": "search", "query": "stripe pricing threshold"})
    assert data["ok"] is True
    hits = data["results"]
    assert hits, "expected a live hit for the stripe fact"
    assert hits[0]["ref"].startswith("live:")
    assert hits[0]["session_id"] in {s1.id, s2.id}
    assert "[[" in hits[0]["snippet"], "snippets must mark the matched text"


def test_search_finds_archived_messages_after_compaction(env) -> None:
    """The whole point: content removed from a window is still retrievable."""
    store, _ = env
    s1 = store.create()
    store.replace_history(s1.id, [_u("hello")])
    archived = [
        ChatMessage(role=Role.USER, content="remember the vault recovery code is NX-7742-QK"),
        _a("Noted and stored."),
    ]
    persist_summary_part(s1.id, archived, reason="summarized")

    hits = _call({"action": "search", "query": "recovery code NX-7742"})["results"]
    assert hits and hits[0]["ref"].startswith("part:"), "archived message must surface as part: ref"
    assert hits[0]["archived"] is True


def test_part_ref_get_returns_verbatim(env) -> None:
    store, _ = env
    s1 = store.create()
    secret = "the api base is https://api.example.biz/v9 and the key id is kk_9931"
    persist_summary_part(s1.id, [ChatMessage(role=Role.USER, content=secret)], reason="hard_trim")

    ref = _call({"action": "search", "query": "kk_9931"})["results"][0]["ref"]
    get_out = _call({"action": "get", "ref": ref})
    assert get_out["ok"] is True
    assert get_out["content"] == secret, "get must return the verbatim archived message"
    assert get_out["archived"] is True


def test_live_ref_get_returns_verbatim(env) -> None:
    store, _ = env
    s1 = store.create()
    body = "exact figure: 41,155 tokens per document"
    store.replace_history(s1.id, [_u(body), _a("ok")])
    out = _call({"action": "get", "ref": f"live:{s1.id}:0"})
    assert out["ok"] is True
    assert out["content"] == body


def test_scope_session_filters_other_sessions(env, monkeypatch) -> None:
    store, _ = env
    s1 = store.create()
    store.replace_history(s1.id, [_u("alpha zebra content one")])
    s2 = store.create()
    store.replace_history(s2.id, [_u("alpha zebra content two")])
    monkeypatch.setattr(history_recall_tool, "_current_session_id", lambda: s1.id)

    all_out = _call({"action": "search", "query": "alpha zebra", "scope": "all"})
    assert {r["session_id"] for r in all_out["results"]} >= {s1.id, s2.id}

    scoped = _call({"action": "search", "query": "alpha zebra", "scope": "session"})
    assert {r["session_id"] for r in scoped["results"]} == {s1.id}


def test_role_filter(env) -> None:
    store, _ = env
    s1 = store.create()
    store.replace_history(s1.id, [_u("budget question about the mouse cohort"), _a("the cohort budget is $100K")])
    out = _call({"action": "search", "query": "cohort budget", "role": "assistant"})
    assert all(r["role"] == "assistant" for r in out["results"])
    assert out["results"], "assistant hit expected"


def test_recent_returns_verbatim_tail(env, monkeypatch) -> None:
    history = [_u("first"), _a("second"), _u("third with the number 4711")]
    token = CURRENT_HISTORY.set(history)
    try:
        out = _call({"action": "recent", "n": 2})
    finally:
        CURRENT_HISTORY.reset(token)
    assert out["count"] == 2
    assert out["messages"][-1]["content"].endswith("4711")


def test_disabled_config_returns_error(env, monkeypatch) -> None:
    class _Agent:
        history_recall = False

    class _Cfg:
        agent = _Agent()

    import nexus.config_file as config_file

    monkeypatch.setattr(config_file, "load_cached", lambda: _Cfg(), raising=False)
    out = _call({"action": "search", "query": "anything"})
    assert out["ok"] is False
    assert "disabled" in out["error"]


def test_malformed_refs_are_errors_not_crashes(env) -> None:
    for ref in ("bogus", "live:noseq", "part:sid:notanumber", "part:sid:1:999999"):
        out = _call({"action": "get", "ref": ref})
        assert out["ok"] is False, ref


def test_purge_removes_index_rows(env, monkeypatch) -> None:
    """Deleting a session must not leave searchable ghost rows behind."""
    from nexus.context_artifacts import purge_session_artifacts

    store, _ = env
    s1 = store.create()
    store.replace_history(s1.id, [_u("hello")])
    persist_summary_part(s1.id, [ChatMessage(role=Role.USER, content="ghostly phrase qzx")], reason="summarized")

    purge_session_artifacts(s1.id)
    out = _call({"action": "search", "query": "ghostly phrase qzx"})
    assert out["results"] == [], "archived rows must go with the archive file"


def test_context_stats_reports_recall_and_discovery(env) -> None:
    """/context-stats exposes context-optimization telemetry."""
    import asyncio

    from nexus.server.routes.sessions import get_context_stats

    store, _ = env
    s1 = store.create()
    store.replace_history(s1.id, [_u("hello telemetry")])

    payload = asyncio.run(get_context_stats(session_id=s1.id, store=store, locale="en"))
    assert payload["history_recall"] == {"enabled": True}
    discovery = payload["tool_discovery"]
    assert "installed" in discovery and "mode" in discovery
