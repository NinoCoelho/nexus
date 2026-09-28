"""Leak fix: parked ask_user forms with ``secret: true`` fields must never
persist their raw answer (sessions DB) nor replay it into the LLM context
(``Agent.continue_after_hitl``).

Covers ``form_schema.redact_secret_fields`` and both choke points.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nexus.agent.form_schema import FieldSchema, redact_secret_fields
from nexus.agent.llm import ChatResponse, LLMProvider, StopReason
from nexus.agent.loop import Agent
from nexus.server.session_store import SessionStore
from nexus.skills.registry import SkillRegistry

SECRET_FIELDS = [
    {"name": "username", "kind": "text", "required": True},
    {"name": "password", "kind": "text", "required": True, "secret": True},
]


def test_redact_secret_fields_dict_schema() -> None:
    answer = {"username": "alice", "password": "hunter2"}
    out = redact_secret_fields(SECRET_FIELDS, answer)
    assert out == {"username": "alice", "password": "[redacted]"}
    # Original untouched (caller may still need the raw value in memory).
    assert answer["password"] == "hunter2"


def test_redact_secret_fields_pydantic_schema() -> None:
    fields = [
        FieldSchema(name="user", kind="text"),
        FieldSchema(name="token", kind="text", secret=True),
    ]
    out = redact_secret_fields(fields, {"user": "a", "token": "b"})
    assert out == {"user": "a", "token": "[redacted]"}


def test_redact_secret_fields_passthrough_cases() -> None:
    assert redact_secret_fields(None, {"password": "x"}) == {"password": "x"}
    assert redact_secret_fields([{"name": "a"}], {"a": "x"}) == {"a": "x"}
    assert redact_secret_fields(SECRET_FIELDS, "plain string") == "plain string"


async def test_mark_hitl_pending_answered_redacts_secrets(tmp_path: Path) -> None:
    from nexus.server.events import SessionEvent

    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    # Real flow: publish creates the hitl_events (bell) row, park persists
    # the hitl_pending row, the late answer resolves both.
    store.publish(
        s.id,
        SessionEvent(
            kind="user_request",
            data={
                "request_id": "req_secret_1",
                "prompt": "Credentials required for skill: demo",
                "kind": "form",
                "choices": None,
                "default": None,
                "timeout_seconds": 300,
                "fields": SECRET_FIELDS,
                "form_title": "Credentials required for skill: demo",
                "form_description": None,
            },
        ),
    )
    store.persist_hitl_pending(
        session_id=s.id,
        request_id="req_secret_1",
        tool_call_id="tc1",
        kind="form",
        prompt="Credentials required for skill: demo",
        choices=None,
        fields=SECRET_FIELDS,
        form_title="Credentials required for skill: demo",
        form_description=None,
        default=None,
        timeout_seconds=300,
    )
    row = store.mark_hitl_pending_answered(
        "req_secret_1", {"username": "alice", "password": "hunter2"}
    )
    assert row is not None

    stored = store.get_hitl_pending("req_secret_1")
    assert stored is not None
    assert "hunter2" not in json.dumps(stored)
    assert stored["answer_json"] is not None
    answer = json.loads(stored["answer_json"])
    assert answer["password"] == "[redacted]"
    assert answer["username"] == "alice"

    # The hitl_events mirror (bell history) must be clean too.
    db = store._hitl_db()
    rows = db.execute(
        "SELECT answer FROM hitl_events WHERE request_id = ?", ("req_secret_1",)
    ).fetchall()
    assert rows, "expected the resolution mirrored to hitl_events"
    for (answer_text,) in rows:
        if answer_text:
            assert "hunter2" not in str(answer_text)


async def test_resolve_pending_redacts_secret_form_answer(tmp_path: Path) -> None:
    """Live (non-parked) path: answering a secret form via /respond's
    resolve_pending must not write the raw password to hitl_events."""
    from nexus.server.events import SessionEvent

    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    store.publish(
        s.id,
        SessionEvent(
            kind="user_request",
            data={
                "request_id": "req_live_1",
                "prompt": "Site login for example.com",
                "kind": "form",
                "choices": None,
                "default": None,
                "timeout_seconds": 600,
                "fields": SECRET_FIELDS,
                "form_title": "Save site login: example.com",
                "form_description": None,
            },
        ),
    )
    # The handler side registers the pending future (as site_credentials
    # and ask_user both do); the answer arrives through resolve_pending.
    fut = store.register_pending(s.id, "req_live_1")

    async def _answer() -> None:
        await asyncio.sleep(0.01)
        store.resolve_pending(
            s.id,
            "req_live_1",
            json.dumps({"username": "alice", "password": "hunter2"}),
        )

    task = asyncio.create_task(_answer())
    raw = await asyncio.wait_for(fut, timeout=2.0)
    await task
    # The in-memory future keeps the raw answer (server-side consumers
    # need it to actually save the credential)...
    assert json.loads(raw)["password"] == "hunter2"
    # ...but the bell history row is redacted.
    db = store._hitl_db()
    (answer_text,) = db.execute(
        "SELECT answer FROM hitl_events WHERE request_id = ?", ("req_live_1",)
    ).fetchone()
    assert answer_text is not None
    assert "hunter2" not in str(answer_text)
    assert json.loads(answer_text)["password"] == "[redacted]"


async def test_mark_hitl_pending_answered_non_secret_untouched(tmp_path: Path) -> None:
    store = SessionStore(db_path=tmp_path / "s.sqlite")
    s = store.create()
    store.persist_hitl_pending(
        session_id=s.id,
        request_id="req_plain_1",
        tool_call_id="tc2",
        kind="form",
        prompt="What name?",
        choices=None,
        fields=[{"name": "name", "kind": "text"}],
        form_title=None,
        form_description=None,
        default=None,
        timeout_seconds=300,
    )
    store.mark_hitl_pending_answered("req_plain_1", {"name": "alice"})
    stored = store.get_hitl_pending("req_plain_1")
    assert stored is not None
    assert json.loads(stored["answer_json"]) == {"name": "alice"}


class _NoopProvider(LLMProvider):
    async def chat(self, messages, *, tools=None, model=None, max_tokens=None) -> ChatResponse:
        return ChatResponse(content="", stop_reason=StopReason.STOP)

    async def aclose(self) -> None:
        pass


class _StubLoom:
    """Captures the message list continue_after_hitl feeds to the LLM."""

    def __init__(self, real_loom: Any) -> None:
        self._real = real_loom
        self.captured: list[Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)

    async def run_turn_stream(self, messages, *, model_id=None):  # type: ignore[no-untyped-def]
        self.captured = list(messages)
        return
        yield  # pragma: no cover — make this an async generator


async def test_continue_after_hitl_redacts_secret_answer(tmp_path: Path) -> None:
    agent = Agent(provider=_NoopProvider(), registry=SkillRegistry(tmp_path / "skills"))
    stub = _StubLoom(agent._loom)
    agent._loom = stub  # type: ignore[assignment]

    class _Sessions:
        def get_hitl_pending(self, request_id: str) -> dict[str, Any]:
            return {
                "request_id": request_id,
                "session_id": "s1",
                "tool_call_id": "tc_secret",
                "kind": "form",
                "fields": SECRET_FIELDS,
                "parked_messages_json": json.dumps(
                    [
                        {"role": "user", "content": "set up the skill"},
                        {"role": "assistant", "content": "I need credentials."},
                    ]
                ),
                "model_id": None,
            }

    agent._sessions = _Sessions()  # type: ignore[assignment]

    consumed: list[dict[str, Any]] = []
    async for _ev in agent.continue_after_hitl(
        session_id="s1",
        request_id="req_x",
        answer={"username": "alice", "password": "hunter2"},
    ):
        consumed.append(_ev)

    assert stub.captured is not None
    tool_msg = stub.captured[-1]
    content = tool_msg.content if hasattr(tool_msg, "content") else tool_msg["content"]
    assert "hunter2" not in content
    payload = json.loads(content)
    assert payload["password"] == "[redacted]"
    assert payload["username"] == "alice"
