"""``nexus_sessions`` payload caps.

The coordinator reads broadly by design, so every uncapped field it returns
lands in its context. The worst offender was ``action="projects"``, which
returned each project's full ``description`` *and* ``instructions``
untruncated for up to 200 projects. ``action="read"`` also returned tool-result
JSON, and crashed outright on any session containing an attachment.
"""

from __future__ import annotations

import pytest

from nexus.agent.llm import ChatMessage, ContentPart, Role
from nexus.coordinator import CoordinatorService, _clip, _flatten_content


class _Session:
    def __init__(self, history, title="A chat"):
        self.history = history
        self.title = title
        self.context = None


class _Summary:
    def __init__(self, pid, name, description="", instructions=""):
        self.id = pid
        self.name = name
        self.description = description
        self.instructions = instructions
        self.title = name
        self.project_id = pid
        self.updated_at = "2026-01-01T00:00:00Z"
        self.message_count = 0


class _FakeStore:
    def __init__(self, session=None, rows=None):
        self._session = session
        self._rows = rows or []

    def get(self, sid):
        return self._session

    def list(self, **kwargs):
        return list(self._rows)


def _svc(store):
    return CoordinatorService(store, agent=object(), tracker=None)


# ── helpers ────────────────────────────────────────────────────────────────


def test_clip_truncates_with_an_ellipsis() -> None:
    assert _clip("abc", limit=10) == "abc"
    out = _clip("x" * 500, limit=10)
    assert out == "x" * 10 + "…"


def test_flatten_content_handles_multipart() -> None:
    """The old code called ``.strip()`` on the list form and raised
    AttributeError, so reading any session with an attachment broke."""
    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="look at this"),
            ContentPart(kind="image", vault_path="a.png", mime_type="image/png"),
        ],
    )
    assert "look at this" in _flatten_content(msg)


def test_flatten_content_handles_plain_and_empty() -> None:
    assert _flatten_content(ChatMessage(role=Role.USER, content="hi")) == "hi"
    assert _flatten_content(ChatMessage(role=Role.USER, content=None)) == ""


def test_flatten_content_survives_a_junk_object() -> None:
    assert _flatten_content(object()) == ""


# ── action="read" ──────────────────────────────────────────────────────────


def test_read_skips_tool_messages_by_default() -> None:
    history = [
        ChatMessage(role=Role.USER, content="do the thing"),
        ChatMessage(role=Role.TOOL, content='{"rows": [1,2,3]}', name="vault_read"),
        ChatMessage(role=Role.ASSISTANT, content="done"),
    ]
    out = _svc(_FakeStore(_Session(history))).inspect(action="read", session_id="s1")
    assert out["ok"] is True
    assert "rows" not in out["tail"]
    assert "do the thing" in out["tail"]
    assert "done" in out["tail"]
    assert out["skipped_tool_messages"] == 1
    # message_count still reflects the real history length.
    assert out["message_count"] == 3


def test_read_can_opt_into_tool_messages() -> None:
    history = [ChatMessage(role=Role.TOOL, content='{"rows": [1]}', name="vault_read")]
    out = _svc(_FakeStore(_Session(history))).inspect(
        action="read", session_id="s1", include_tools=True
    )
    assert "rows" in out["tail"]


def test_read_truncates_each_message() -> None:
    history = [ChatMessage(role=Role.USER, content="y" * 5_000)]
    out = _svc(_FakeStore(_Session(history))).inspect(action="read", session_id="s1")
    assert len(out["tail"]) < 600


def test_read_clamps_the_tail() -> None:
    history = [ChatMessage(role=Role.USER, content=f"m{i}") for i in range(200)]
    out = _svc(_FakeStore(_Session(history))).inspect(
        action="read", session_id="s1", tail=9999
    )
    assert out["tail"].count("[user]") == 50


def test_read_does_not_crash_on_multipart_history() -> None:
    history = [
        ChatMessage(
            role=Role.USER,
            content=[ContentPart(kind="image", vault_path="a.png", mime_type="image/png")],
        ),
        ChatMessage(role=Role.ASSISTANT, content="I see it"),
    ]
    out = _svc(_FakeStore(_Session(history))).inspect(action="read", session_id="s1")
    assert out["ok"] is True
    assert "I see it" in out["tail"]


def test_read_requires_a_session_id() -> None:
    assert _svc(_FakeStore()).inspect(action="read")["ok"] is False


def test_read_reports_unknown_sessions() -> None:
    out = _svc(_FakeStore(None)).inspect(action="read", session_id="nope")
    assert out["ok"] is False


# ── action="projects" ──────────────────────────────────────────────────────


@pytest.fixture
def _projects(monkeypatch):
    summaries = [
        _Summary("p1", "Alpha", description="d" * 1_000, instructions="i" * 5_000),
        _Summary("p2", "Beta", description="short", instructions="also long" * 100),
    ]
    detail = _Summary("p1", "Alpha", description="full desc", instructions="full inst")

    class _FakeProjectStore:
        def __init__(self, _db):
            pass

        def list(self, limit=50):
            return summaries[:limit]

        def get(self, pid):
            return detail if pid == "p1" else None

    import nexus.server.project_store as ps_mod

    monkeypatch.setattr(ps_mod, "ProjectStore", _FakeProjectStore)
    monkeypatch.setattr("nexus.home.sessions_db", lambda: ":memory:")
    return summaries


def test_projects_omits_instructions_and_truncates_descriptions(_projects) -> None:
    out = _svc(_FakeStore(rows=[])).inspect(action="projects")
    assert out["ok"] is True
    for entry in out["projects"]:
        assert "instructions" not in entry, "the biggest uncapped payload"
        assert len(entry["description"]) <= 301
    assert "hint" in out


def test_projects_detail_returns_the_full_record(_projects) -> None:
    out = _svc(_FakeStore(rows=[])).inspect(action="projects", project_id="p1")
    assert out["ok"] is True
    assert out["project"]["instructions"] == "full inst"
    assert out["project"]["description"] == "full desc"


def test_projects_detail_reports_unknown_ids(_projects) -> None:
    out = _svc(_FakeStore(rows=[])).inspect(action="projects", project_id="nope")
    assert out["ok"] is False


# ── action="sessions" ──────────────────────────────────────────────────────


def test_sessions_filters_by_updated_since() -> None:
    rows = [
        _Summary("p1", "old"),
        _Summary("p2", "new"),
    ]
    rows[0].updated_at = "2026-01-01T00:00:00Z"
    rows[1].updated_at = "2026-06-01T00:00:00Z"
    out = _svc(_FakeStore(rows=rows)).inspect(
        action="sessions", updated_since="2026-03-01T00:00:00Z"
    )
    assert [s["title"] for s in out["sessions"]] == ["new"]


def test_sessions_without_a_filter_returns_everything() -> None:
    rows = [_Summary("p1", "a"), _Summary("p2", "b")]
    out = _svc(_FakeStore(rows=rows)).inspect(action="sessions")
    assert len(out["sessions"]) == 2


def test_unknown_action() -> None:
    assert _svc(_FakeStore()).inspect(action="nope")["ok"] is False
