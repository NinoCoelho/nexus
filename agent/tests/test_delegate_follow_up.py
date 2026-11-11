"""delegate_follow_up — completion hook for async agent_delegate jobs.

Covers the `[delegate].on_complete` gate (silent vs report), the session
event publication, and both follow-up paths: enqueue into a running turn
vs a fresh detached ChatTurnRunner when the session is idle.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nexus.server.job_tracker import JobTracker
from nexus.server.services import delegate_follow_up as dfu


class _FakeSession:
    def __init__(self) -> None:
        self.history: list = []
        self.context = "ctx"


class _FakeStore:
    def __init__(self, session: _FakeSession | None) -> None:
        self.session = session
        self.published: list[tuple[str, str, Any]] = []
        self.replaced: list[tuple[str, list]] = []
        self.cleared: list[str] = []

    def get(self, sid: str) -> _FakeSession | None:
        return self.session

    def publish(self, sid: str, event: Any) -> None:
        self.published.append((sid, event.kind, event.data))

    def replace_history(self, sid: str, history: list) -> None:
        self.replaced.append((sid, history))

    def clear_replay(self, sid: str) -> None:
        self.cleared.append(sid)


class _FakeRunner:
    instances: list["_FakeRunner"] = []
    started: int = 0

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        _FakeRunner.instances.append(self)

    def start(self) -> None:
        _FakeRunner.started += 1


class _BusyRunner:
    def __init__(self, qid: str | None = "q1") -> None:
        self.enqueued: list[tuple[str, str]] = []
        self._qid = qid

    def enqueue(self, text: str, origin: str = "web", **kw: Any) -> str | None:
        self.enqueued.append((text, origin))
        return self._qid


_EVENT = {
    "session_id": "sess-1",
    "job_id": "jobabc123",
    "agent": "opencode",
    "task": "do the thing",
    "status": "done",
    "exit_code": 0,
    "duration_s": 2.0,
    "log_ref": "vault://.tool-cache/delegate-opencode-1.md",
}


class _Mode:
    value = "report"


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeRunner.instances.clear()
    _FakeRunner.started = 0
    _Mode.value = "report"
    monkeypatch.setattr(dfu, "ChatTurnRunner", _FakeRunner)
    monkeypatch.setattr(
        dfu, "_load_cfg", lambda: type("C", (), {"delegate": type("D", (), {"on_complete": _Mode.value})()})()
    )


def _make(store: _FakeStore, tracker: JobTracker | None = None):
    tracker = tracker or JobTracker()
    hook = dfu.wire_delegate_follow_up(
        sessions=store,
        agent=object(),
        tracker=tracker,
        publish_job_event=lambda kind, data: None,
    )
    return hook, tracker


def _run(coro: Any) -> None:
    asyncio.new_event_loop().run_until_complete(coro)


def test_silent_mode_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    _Mode.value = "silent"
    store = _FakeStore(_FakeSession())
    hook, _ = _make(store)

    _run(hook(dict(_EVENT)))

    assert store.published == []
    assert store.replaced == []
    assert _FakeRunner.started == 0


def test_report_publishes_event_and_starts_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore(_FakeSession())
    hook, _ = _make(store)

    # No running turn → fresh detached report turn.
    monkeypatch.setattr(dfu, "get_running_turn", lambda sid: None)

    _run(hook(dict(_EVENT)))

    # 1) delegate_done on the session bus.
    assert [(s, k) for s, k, _ in store.published] == [("sess-1", "delegate_done")]
    assert store.published[0][2]["job_id"] == "jobabc123"

    # 2) Report turn started with the fetch-then-summarize prompt.
    assert _FakeRunner.started == 1
    runner = _FakeRunner.instances[0]
    assert runner.kwargs["session_id"] == "sess-1"
    assert runner.kwargs["origin"] == "web"
    prompt = runner.kwargs["message"]
    assert "delegate_status" in prompt and "jobabc123" in prompt

    # 3) The prompt was persisted as the user message.
    assert store.replaced and store.replaced[0][0] == "sess-1"


def test_report_enqueues_when_turn_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _FakeStore(_FakeSession())
    hook, _ = _make(store)
    busy = _BusyRunner()
    monkeypatch.setattr(dfu, "get_running_turn", lambda sid: busy)

    _run(hook(dict(_EVENT)))

    assert len(busy.enqueued) == 1
    assert "jobabc123" in busy.enqueued[0][0]
    # No new runner, no history rewrite while a turn owns the session.
    assert _FakeRunner.started == 0
    assert store.replaced == []


def test_enqueue_race_falls_through_to_new_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """enqueue returns None when the runner already finalized → fresh turn."""
    store = _FakeStore(_FakeSession())
    hook, _ = _make(store)
    busy = _BusyRunner(qid=None)  # finalized between the check and enqueue
    monkeypatch.setattr(dfu, "get_running_turn", lambda sid: busy)

    _run(hook(dict(_EVENT)))

    assert _FakeRunner.started == 1


def test_no_session_id_skips_everything() -> None:
    store = _FakeStore(_FakeSession())
    hook, _ = _make(store)

    _run(hook({"job_id": "x", "status": "done"}))

    assert store.published == []
    assert _FakeRunner.started == 0


def test_report_prompt_shape() -> None:
    prompt = dfu._report_prompt(dict(_EVENT))
    assert "opencode" in prompt
    assert "status: done" in prompt
    assert "exit_code: 0" in prompt
    assert "vault://.tool-cache/delegate-opencode-1.md" in prompt
    assert "jobabc123" in prompt
