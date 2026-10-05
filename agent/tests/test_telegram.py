"""Telegram gateway tests — in-process router + fake Bot API client.

Covers: allowlist auth, DM chat flow (message → session → streamed reply),
standalone topic chats (no project required), /project binding + session
adoption, /new + /chats + switch callbacks, /title /usage /id,
queue-while-busy ack, HITL forwarding with inline-button resolution,
formatting, bindings CRUD, and the turn launcher's rejection paths.

Reuses ``FakeProvider``/``GatedProvider`` from the SSE/queue harnesses so
the full agent loop runs against a scripted provider.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from nexus.agent.llm import ChatMessage, ChatResponse, Role, StopReason
from nexus.agent.loop import Agent
from nexus.config_schema import TelegramConfig
from nexus.server.job_tracker import JobTracker
from nexus.server.session_store import SessionStore
from nexus.skills.registry import SkillRegistry
from nexus.telegram.bindings import TelegramBindingStore
from nexus.telegram.formatting import md_to_telegram_html, split_for_telegram, tlen
from nexus.telegram.router import TelegramRouter

from test_server_sse import FakeProvider
from test_chat_queue import GatedProvider


# ── Fakes ───────────────────────────────────────────────────────────────


class FakeTGClient:
    """Records Bot API calls; message ids increment from 100."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.actions: list[tuple[int, int]] = []
        self.callback_answers: list[str] = []
        self.reactions: list[tuple[int, int, str]] = []
        self.voices: list[dict[str, Any]] = []
        self.audios: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []

    async def send_text_safe(
        self, chat_id, text, *, thread_id=None, reply_markup=None, reply_to_message_id=None
    ):
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "thread_id": thread_id,
                "kb": reply_markup,
                "reply_to": reply_to_message_id,
            }
        )
        return 100 + len(self.sent)

    async def send_document(self, chat_id, data, filename, *, thread_id=None, caption=""):
        self.documents.append(
            {
                "chat_id": chat_id,
                "data": data,
                "filename": filename,
                "thread_id": thread_id,
                "caption": caption,
            }
        )
        return 100 + len(self.documents)

    async def edit_text_safe(self, chat_id, message_id, text, *, reply_markup=None):
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, "kb": reply_markup}
        )
        return True

    async def set_message_reaction(self, chat_id, message_id, emoji=""):
        self.reactions.append((chat_id, message_id, emoji))

    async def get_file(self, file_id):
        return {"file_id": file_id, "file_size": 8, "file_path": f"files/{file_id}"}

    async def download_file(self, file_path):
        return b"FAKEDATA"

    async def send_voice(self, chat_id, audio, filename, *, thread_id=None, caption=""):
        self.voices.append(
            {"chat_id": chat_id, "audio": audio, "filename": filename, "thread_id": thread_id}
        )
        return 1

    async def send_audio(self, chat_id, audio, filename, mime, *, thread_id=None):
        self.audios.append({"chat_id": chat_id, "audio": audio, "filename": filename, "mime": mime})
        return 1

    async def send_chat_action(self, chat_id, action="typing", *, thread_id=None):
        self.actions.append((chat_id, thread_id or 0))

    async def answer_callback_query(self, callback_query_id, text=""):
        self.callback_answers.append(text)

    async def aclose(self) -> None:
        pass

    def sent_texts(self) -> list[str]:
        return [s["text"] for s in self.sent]


def _final(text: str) -> ChatResponse:
    return ChatResponse(content=text, stop_reason=StopReason.STOP)


def _msg(
    text: str,
    *,
    chat_id: int = 1000,
    thread_id: int = 0,
    chat_type: str = "private",
    user_id: int = 42,
    username: str = "owner",
) -> dict:
    m: dict[str, Any] = {
        "message_id": 1,
        "text": text,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": user_id, "first_name": "Owner", "username": username},
    }
    if thread_id:
        m["message_thread_id"] = thread_id
    return m


class Harness:
    def __init__(self, tmp_path: Path, provider: FakeProvider) -> None:
        self.tmp_path = tmp_path
        self.store = SessionStore(db_path=tmp_path / "sessions.sqlite")
        self.agent = Agent(provider=provider, registry=SkillRegistry(tmp_path / "skills"))
        self.client = FakeTGClient()
        self.bindings = TelegramBindingStore(tmp_path / "sessions.sqlite")
        self.cfg = TelegramConfig(enabled=True, allowed_user_ids=[42])
        self.router = TelegramRouter(
            client=self.client,
            agent=self.agent,
            store=self.store,
            tracker=JobTracker(),
            bindings=self.bindings,
            cfg=self.cfg,
        )

    async def message(self, text: str, **kwargs: Any) -> None:
        await self.router.handle_update({"update_id": 1, "message": _msg(text, **kwargs)})

    async def callback(
        self,
        data: str,
        *,
        chat_id: int = 1000,
        thread_id: int = 0,
        user_id: int = 42,
        message_override: int = 0,
    ) -> None:
        msg: dict[str, Any] = {
            "message_id": message_override or 99,
            "chat": {"id": chat_id, "type": "supergroup"},
        }
        if thread_id:
            msg["message_thread_id"] = thread_id
        await self.router.handle_update(
            {
                "update_id": 2,
                "callback_query": {
                    "id": "cq1",
                    "from": {"id": user_id},
                    "data": data,
                    "message": msg,
                },
            }
        )

    async def wait_turns_done(self, timeout: float = 15.0) -> None:
        """Wait until every runner finished and replies are finalized.

        The reply streamer idles open by design (long-lived per session);
        after the turn settles we cancel the idle streamers so the test
        loop closes clean.
        """
        from nexus.server.services import chat_turn_runner as ctr

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not ctr._running_turns:
                break
            await asyncio.sleep(0.02)
        else:
            raise TimeoutError("turn runners did not finish")

        # Let the streamer drain its queue + run the finalize edit.
        await asyncio.sleep(0.2)
        for t in list(self.router._streamers.values()):
            if not t.done():
                t.cancel()
        for t in list(self.router._streamers.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


# ── Auth ────────────────────────────────────────────────────────────────


async def test_unauthorized_user_ignored_with_deny_message(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("hi")]))
    await h.message("hello", user_id=666)
    await asyncio.sleep(0.05)
    denies = [t for t in h.client.sent_texts() if "not authorized" in t]
    assert len(denies) == 1
    # The deny message teaches the owner their id for the allowlist.
    assert "<code>666</code>" in denies[0]
    assert h.bindings.get(1000, 0) is None  # no session/binding created


async def test_deny_message_throttled(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("hi")]))
    await h.message("hello", user_id=666)
    await h.message("again", user_id=666)
    await asyncio.sleep(0.05)
    assert sum("not authorized" in t for t in h.client.sent_texts()) == 1


async def test_deny_message_disabled(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("hi")]))
    h.router.cfg.deny_message = False
    await h.message("hello", user_id=666)
    await asyncio.sleep(0.05)
    assert h.client.sent == []


# ── DM chat flow ────────────────────────────────────────────────────────


async def test_dm_message_creates_session_and_streams_reply(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("Hello **world**!")]))
    await h.message("hi there")
    await h.wait_turns_done()

    binding = h.bindings.get(1000, 0)
    assert binding is not None and binding.kind == "dm"
    session = h.store.get(binding.active_session_id)
    assert session is not None
    roles = [m.role.value for m in session.history]
    assert "user" in roles and "assistant" in roles

    texts = " ".join(h.client.sent_texts())
    assert "Hello" in texts and "<b>world</b>" in texts

    # Ack lifecycle: 👀 on receipt, upgraded to 👍 once the turn settles.
    assert (1000, 1, "👀") in h.client.reactions
    assert (1000, 1, "👍") in h.client.reactions
    assert h.client.reactions.index((1000, 1, "👀")) < h.client.reactions.index((1000, 1, "👍"))
    assert h.router._pending_acks.get(binding.active_session_id) in (None, [])


async def test_dm_sender_prefix_not_added(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    await h.message("plain dm message")
    await h.wait_turns_done()
    session = h.store.get(h.bindings.get(1000, 0).active_session_id)
    user_msgs = [m for m in session.history if m.role.value == "user"]
    assert "plain dm message" in user_msgs[0].content


async def test_group_message_carries_sender_prefix(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    await h.message("group hello", chat_type="supergroup")
    await h.wait_turns_done()
    binding = h.bindings.get(1000, 0)
    assert binding is not None and binding.kind == "group"
    session = h.store.get(binding.active_session_id)
    user_msgs = [m for m in session.history if m.role.value == "user"]
    assert "From Owner (@owner)" in user_msgs[0].content
    assert "group hello" in user_msgs[0].content


async def test_unbound_topic_auto_creates_standalone_chat(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    await h.message("topic msg", chat_type="supergroup", thread_id=7)
    await h.wait_turns_done()

    # No project needed: binding + session auto-created, silently (topics
    # never see a "bind a project" notice — chatting starts right away).
    binding = h.bindings.get(1000, 7)
    assert binding is not None and binding.kind == "topic"
    assert binding.project_id is None
    session = h.store.get(binding.active_session_id)
    assert session is not None and session.project_id is None
    texts = " ".join(h.client.sent_texts())
    assert "isn't linked" not in texts
    assert "isn't bound" not in texts
    roles = [m.role.value for m in session.history]
    assert "user" in roles and "assistant" in roles  # turn ran


async def test_new_in_unbound_topic_starts_projectless_chat(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    # /new before any message — no binding exists yet; one is created with
    # a fresh unprojected chat on the same topic.
    await h.message("/new scratchpad", chat_type="supergroup", thread_id=9)
    binding = h.bindings.get(1000, 9)
    assert binding is not None and binding.kind == "topic"
    assert binding.project_id is None
    session = h.store.get(binding.active_session_id)
    assert session is not None
    assert session.project_id is None
    assert session.title == "scratchpad"

    # A follow-up /new starts another chat on the same topic.
    await h.message("/new second", chat_type="supergroup", thread_id=9)
    new_sid = h.bindings.get(1000, 9).active_session_id
    assert new_sid != session.id
    assert h.store.get(new_sid).project_id is None


# ── Commands ────────────────────────────────────────────────────────────


async def test_project_bind_adopts_latest_session(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    from nexus.server.project_store import ProjectStore

    pstore = ProjectStore(h.tmp_path / "sessions.sqlite")
    project = pstore.create(name="Apollo")
    existing = h.store.create(context="seed", project_id=project.id)
    h.store.replace_history(existing.id, [ChatMessage(role=Role.USER, content="earlier work")])

    await h.message("/project Apollo", chat_type="supergroup", thread_id=5)
    binding = h.bindings.get(1000, 5)
    assert binding is not None
    assert binding.project_id == project.id
    assert binding.active_session_id == existing.id  # adopted main chat
    assert any("Apollo" in t for t in h.client.sent_texts())


async def test_new_chats_and_switch_callback(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    from nexus.server.project_store import ProjectStore

    pstore = ProjectStore(h.tmp_path / "sessions.sqlite")
    project = pstore.create(name="Apollo")

    await h.message("/project Apollo", chat_type="supergroup", thread_id=5)
    binding = h.bindings.get(1000, 5)

    await h.message("/new side investigation", chat_type="supergroup", thread_id=5)
    new_sid = h.bindings.get(1000, 5).active_session_id
    assert new_sid != binding.active_session_id
    new_session = h.store.get(new_sid)
    assert new_session.project_id == project.id
    assert new_session.title == "side investigation"

    await h.message("/chats", chat_type="supergroup", thread_id=5)
    kb = h.client.sent[-1]["kb"]
    assert kb is not None
    flat = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert any("side investigation" in t for t in flat)

    # Switch back to the first chat via its button.
    first_btn = None
    for row in kb["inline_keyboard"]:
        for b in row:
            if b["callback_data"] == f"sw:{binding.active_session_id}":
                first_btn = b
    assert first_btn is not None
    await h.callback(f"sw:{binding.active_session_id}", chat_id=1000, thread_id=5)
    assert h.bindings.get(1000, 5).active_session_id == binding.active_session_id
    assert any("Switched" in a for a in h.client.callback_answers)


async def test_title_usage_id_commands(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    await h.message("hello", chat_id=555)  # establish DM binding
    await h.wait_turns_done()
    binding = h.bindings.get(555, 0)

    await h.message("/title My DM chat", chat_id=555)
    assert h.store.get(binding.active_session_id).title == "My DM chat"

    await h.message("/id", chat_id=555)
    assert any("chat id: <code>555</code>" in t for t in h.client.sent_texts())

    await h.message("/usage", chat_id=555)
    assert any("Usage" in t for t in h.client.sent_texts())


async def test_unknown_command(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    await h.message("/frobnicate")
    await asyncio.sleep(0.05)
    assert any("Unknown command" in t for t in h.client.sent_texts())


async def test_command_with_bot_suffix(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    await h.message("hello")  # establish DM binding
    await h.wait_turns_done()
    binding = h.bindings.get(1000, 0)
    await h.message("/title@nexus_bot renamed")
    await asyncio.sleep(0.05)
    assert h.store.get(binding.active_session_id).title == "renamed"


# ── Queue-while-busy ────────────────────────────────────────────────────


async def test_message_while_turn_running_is_queued(tmp_path: Path) -> None:
    provider = GatedProvider([_final("first reply"), _final("second reply")])
    h = Harness(tmp_path, provider)

    await h.message("first")
    await asyncio.sleep(0.05)  # let the turn start and block on the gate
    assert any(not t.done() for t in h.router._streamers.values())

    await h.message("second while busy")
    await asyncio.sleep(0.05)
    # Queued messages are acked with a reaction, not a bubble.
    assert not any("Queued" in t for t in h.client.sent_texts())
    assert h.client.reactions.count((1000, 1, "👀")) == 2

    provider.gate.set()
    await h.wait_turns_done()

    # Both replies eventually delivered (chained turn gets its own message).
    all_text = " ".join(h.client.sent_texts() + [e["text"] for e in h.client.edits])
    assert "first reply" in all_text
    assert "second reply" in all_text
    # After the (single) settle, the 👀 acks were upgraded to 👍.
    assert (1000, 1, "👍") in h.client.reactions


async def test_no_reactions_when_ack_disabled(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    h.router.cfg.ack_reaction = ""
    await h.message("hi")
    await h.wait_turns_done()
    assert h.client.reactions == []
    # The reply still arrives as a bubble.
    assert any("ok" in t for t in h.client.sent_texts())


async def test_upgrade_acks_deferred_while_runner_alive(tmp_path: Path) -> None:
    """_upgrade_acks keeps pending entries when another turn is already
    running — never a premature 👍."""
    from nexus.server.services.chat_turn_runner import ChatTurnRunner, _running_turns

    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    sid = h.store.create().id
    h.router._pending_acks[sid] = [(1000, 7)]
    fake_runner = ChatTurnRunner(
        agent=h.agent,
        store=h.store,
        session_id=sid,
        message="m",
        context="",
        model_id="",
        pre_turn_history=[],
        attachment_parts=None,
        resume_working_messages=None,
        tracker=None,
        turn_job_id="",
        publish_job_event=lambda *a: None,
    )
    fake_runner.task = asyncio.get_running_loop().create_task(asyncio.sleep(10))
    _running_turns[sid] = fake_runner
    try:
        await h.router._upgrade_acks(sid)
        assert h.router._pending_acks[sid] == [(1000, 7)]  # kept, not upgraded
        assert (1000, 7, "👍") not in h.client.reactions
    finally:
        _running_turns.pop(sid, None)
        fake_runner.task.cancel()
    # With no live runner the list drains and the upgrade fires.
    await h.router._upgrade_acks(sid)
    assert h.router._pending_acks.get(sid) is None
    assert (1000, 7, "👍") in h.client.reactions


# ── HITL ────────────────────────────────────────────────────────────────


async def test_hitl_prompt_forwarded_and_button_resolves(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    binding = h.bindings.get(1000, 0)
    sid = binding.active_session_id

    fut = h.store.register_pending(sid, "req1")

    from nexus.telegram.hitl import HitlForwarder

    fwd = HitlForwarder(h.router)
    await fwd._forward(
        sid,
        {
            "request_id": "req1",
            "prompt": "Run this destructive command?",
            "kind": "confirm",
            "choices": None,
            "default": None,
        },
    )
    assert any("destructive" in t for t in h.client.sent_texts())
    kb = h.client.sent[-1]["kb"]
    buttons = [b for row in kb["inline_keyboard"] for b in row]
    labels = [b["text"] for b in buttons]
    assert labels == ["yes", "no"]

    yes_btn = buttons[0]
    await h.callback(yes_btn["callback_data"])
    assert fut.done() and fut.result() == "yes"
    assert any("Answered" in e["text"] for e in h.client.edits)


async def test_hitl_choice_kind_buttons(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    markup, note, slot = h.router.build_hitl_interaction(
        "sid1", "req9", {"kind": "choice", "choices": ["Red", "Blue"]}
    )
    assert markup is not None and note is None and slot is None
    labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert labels == ["Red", "Blue"]
    key = markup["inline_keyboard"][0][0]["callback_data"][3:]
    assert h.router._hitl_buttons[key] == ("sid1", "req9", "Red")


async def test_hitl_text_kind_force_reply_and_slot(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    markup, note, slot = h.router.build_hitl_interaction("sid1", "req9", {"kind": "text"})
    assert markup is not None and "force_reply" in markup and note is None
    assert slot is not None and slot.kind == "text" and slot.session_id == "sid1"


# ── Free-form HITL answers (text / form prompts) ────────────────────────


async def _forward_request(h: Harness, sid: str, data: dict[str, Any]) -> None:
    from nexus.telegram.hitl import HitlForwarder

    await HitlForwarder(h.router)._forward(sid, data)


async def test_text_prompt_next_message_answers(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    fut = h.store.register_pending(sid, "reqT")
    await _forward_request(
        h, sid, {"request_id": "reqT", "prompt": "What's your name?", "kind": "text"}
    )
    last = h.client.sent[-1]
    assert "What's your name?" in last["text"]
    assert last["kb"] is not None and "force_reply" in last["kb"]
    assert h.router._pending_answers[(1000, 0)].request_id == "reqT"

    await h.message("Nino")
    assert fut.done() and fut.result() == "Nino"
    assert (1000, 0) not in h.router._pending_answers
    assert any("Answered" in e["text"] for e in h.client.edits)
    # The answer never became a chat turn: no runner started.
    from nexus.server.services import chat_turn_runner as ctr

    assert not ctr._running_turns


async def test_answer_binds_to_quoted_prompt_only(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("a"), _final("b")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    fut = h.store.register_pending(sid, "reqQ")
    await _forward_request(
        h, sid, {"request_id": "reqQ", "prompt": "Answer me", "kind": "text"}
    )
    prompt_msg_id = 100 + len(h.client.sent)

    # Reply quoting a DIFFERENT message → normal chat turn, not an answer.
    msg = _msg("side comment")
    msg["reply_to_message"] = {"message_id": prompt_msg_id - 1}
    await h.router.handle_update({"update_id": 3, "message": msg})
    await h.wait_turns_done()
    assert not fut.done()

    # Reply quoting the prompt bubble → the answer.
    msg = _msg("the real answer")
    msg["reply_to_message"] = {"message_id": prompt_msg_id}
    await h.router.handle_update({"update_id": 4, "message": msg})
    assert fut.done() and fut.result() == "the real answer"


async def test_form_prompt_named_lines_answer(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    fields = [
        {"name": "city", "label": "City", "kind": "text", "required": True},
        {"name": "days", "kind": "number", "required": True},
        {"name": "notes", "kind": "textarea"},
    ]
    fut = h.store.register_pending(sid, "reqF")
    await _forward_request(
        h,
        sid,
        {
            "request_id": "reqF",
            "prompt": "Trip details?",
            "kind": "form",
            "fields": fields,
            "form_title": "Trip",
        },
    )
    body = h.client.sent[-1]["text"]
    assert "<b>Trip</b>" in body and "city" in body
    assert h.client.sent[-1]["kb"] is not None and "force_reply" in h.client.sent[-1]["kb"]

    await h.message("City: Lisbon\ndays: 3")
    assert fut.done()
    assert json.loads(fut.result()) == {"city": "Lisbon", "days": 3}
    assert (1000, 0) not in h.router._pending_answers


async def test_form_invalid_reply_keeps_slot_for_retry(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    fields = [
        {"name": "city", "kind": "text", "required": True},
        {"name": "days", "kind": "number", "required": True},
    ]
    fut = h.store.register_pending(sid, "reqR")
    await _forward_request(
        h,
        sid,
        {"request_id": "reqR", "prompt": "Trip?", "kind": "form", "fields": fields},
    )

    await h.message("days: not-a-number")
    assert not fut.done()
    assert (1000, 0) in h.router._pending_answers
    assert any("still waiting" in t for t in h.client.sent_texts())

    await h.message("city: Porto\ndays: 2")
    assert fut.done()
    assert json.loads(fut.result()) == {"city": "Porto", "days": 2}


async def test_secret_form_stays_ui_only(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    fields = [
        {"name": "username", "kind": "text", "required": True},
        {"name": "password", "kind": "text", "required": True, "secret": True},
    ]
    h.store.register_pending(sid, "reqS")
    await _forward_request(
        h,
        sid,
        {
            "request_id": "reqS",
            "prompt": "Site login for example.com",
            "kind": "form",
            "fields": fields,
        },
    )
    assert h.client.sent[-1]["kb"] is None
    assert "Nexus UI" in h.client.sent[-1]["text"]
    assert not h.router._pending_answers


async def test_single_select_form_reply_keyboard(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    fields = [
        {"name": "mode", "kind": "select", "choices": ["Fast", "Slow"], "required": True}
    ]
    fut = h.store.register_pending(sid, "reqK")
    await _forward_request(
        h,
        sid,
        {"request_id": "reqK", "prompt": "Mode?", "kind": "form", "fields": fields},
    )
    kb = h.client.sent[-1]["kb"]
    assert kb is not None and "keyboard" in kb
    values = [b["text"] for row in kb["keyboard"] for b in row]
    assert values == ["Fast", "Slow"]
    assert kb.get("one_time_keyboard") is True

    # Tapping a keyboard button sends the bare value — still an answer.
    await h.message("Fast")
    assert fut.done()
    assert json.loads(fut.result()) == {"mode": "Fast"}
    assert any(e.get("kb") == {"remove_keyboard": True} for e in h.client.edits)


async def test_cancel_command_dismisses_pending_answer(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    fut = h.store.register_pending(sid, "reqC")
    await _forward_request(
        h, sid, {"request_id": "reqC", "prompt": "Answer?", "kind": "text"}
    )
    assert (1000, 0) in h.router._pending_answers

    await h.message("/cancel")
    assert fut.cancelled()
    assert (1000, 0) not in h.router._pending_answers
    assert any("Cancelled" in e["text"] for e in h.client.edits)
    assert any("Dismissed the pending question" in t for t in h.client.sent_texts())


async def test_cancelled_event_drops_answer_slot(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    await _forward_request(
        h, sid, {"request_id": "reqX", "prompt": "Old prompt", "kind": "text"}
    )
    assert (1000, 0) in h.router._pending_answers

    from nexus.telegram.hitl import HitlForwarder

    await HitlForwarder(h.router)._cancelled({"request_id": "reqX"})
    assert (1000, 0) not in h.router._pending_answers
    assert any("Expired" in e["text"] for e in h.client.edits)


async def test_parked_text_answer_resumes_turn(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("done")]))
    await h.message("hi")
    await h.wait_turns_done()
    sid = h.bindings.get(1000, 0).active_session_id

    # Parked row: the turn already ended waiting for this request.
    h.store.persist_hitl_pending(
        session_id=sid,
        request_id="reqP",
        tool_call_id="tc1",
        kind="text",
        prompt="Deferred question?",
        choices=None,
        fields=None,
        form_title=None,
        form_description=None,
        default=None,
        timeout_seconds=300,
    )
    h.store.update_hitl_pending_snapshot(
        "reqP", json.dumps([{"role": "user", "content": "hi"}])
    )

    async def fake_continue(*, session_id: str, request_id: str, answer: Any):
        assert answer == "later answer"
        yield {"type": "delta", "text": "resumed reply"}
        yield {
            "type": "done",
            "session_id": session_id,
            "reply": "resumed reply",
            "messages": [ChatMessage(role=Role.ASSISTANT, content="resumed reply")],
            "usage": {"model": "m", "input_tokens": 1, "output_tokens": 2, "tool_calls": 0},
        }

    h.agent.continue_after_hitl = fake_continue  # type: ignore[method-assign]

    await _forward_request(
        h, sid, {"request_id": "reqP", "prompt": "Deferred question?", "kind": "text"}
    )
    await h.message("later answer")

    task = h.router._resume_tasks.get(sid)
    assert task is not None
    await asyncio.wait_for(task, timeout=5.0)

    row = h.store.get_hitl_pending("reqP")
    assert row is not None and row["status"] == "answered"
    # Terminal marker published so the reply streamer finalizes.
    replay = h.store._replay.get(sid, [])
    assert any(ev.kind == "turn_settled" for _, ev in replay)
    # The resumed reply was persisted onto the session.
    hist = h.store.get(sid).history
    assert hist and hist[-1].content == "resumed reply"
    assert any("Answered" in e["text"] for e in h.client.edits)


# ── Formatting ──────────────────────────────────────────────────────────


def test_formatting_basics() -> None:
    html = md_to_telegram_html(
        "# Head\n**b** *i* `c` [x](https://e.com)\n- item\n> quote\n\n```py\ncode<x>\n```"
    )
    assert "<b>Head</b>" in html
    assert "<b>b</b>" in html and "<i>i</i>" in html and "<code>c</code>" in html
    assert '<a href="https://e.com">x</a>' in html
    assert "• item" in html
    assert "<blockquote>" in html
    assert "<pre>\ncode&lt;x&gt;\n</pre>" in html


def test_formatting_escapes_raw_html() -> None:
    html = md_to_telegram_html("use <script>alert(1)</script> & <b>not markup")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html


def test_formatting_partial_fence_closed() -> None:
    html = md_to_telegram_html("text\n```\nopen ended")
    assert html.count("<pre>") == html.count("</pre>") == 1


def test_formatting_table_renders_aligned_pre() -> None:
    md = "| Nome | Valor |\n|---|---|\n| a | 1 |\n| bb | 22 |"
    html = md_to_telegram_html(md)
    assert "<pre>" in html and "</pre>" in html
    assert "|---|" not in html  # separator row never rendered literally
    assert "┼" in html
    rows = [ln for ln in html.split("\n") if "│" in ln]
    assert len(rows) == 3  # header + 2 body rows
    assert len({ln.index("│") for ln in rows}) == 1  # columns aligned


def test_formatting_table_escapes_cells_and_strips_markers() -> None:
    md = "| a<b> | & |\n|---|---|\n| **bold** | `c` |"
    html = md_to_telegram_html(md)
    assert "&lt;b&gt;" in html and "&amp;" in html
    assert "**" not in html  # markdown markers can't render inside <pre>


def test_formatting_table_wide_glyphs_pad_double() -> None:
    from nexus.telegram.formatting import _dw

    md = "| 名前 | v |\n|---|---|\n| x | 1 |"
    html = md_to_telegram_html(md)
    rows = [ln for ln in html.split("\n") if "│" in ln]
    assert len(rows) == 2
    # junctions align in display columns (CJK padded as width 2), not codepoints
    cols = [_dw(ln[: ln.index("│")]) for ln in rows]
    assert cols[0] == cols[1]


def test_formatting_table_partial_before_separator() -> None:
    # Streaming partial: header row arrived, separator hasn't — stays plain.
    html = md_to_telegram_html("| a | b |\n| c")
    assert html.count("<pre>") == html.count("</pre>") == 0


def test_formatting_table_block_boundaries() -> None:
    md = (
        "# Title\n"
        "- bullet\n"
        "\n"
        "| h1 | h2 |\n"
        "|---|---|\n"
        "| a | b |\n"
        "\n"
        "plain text after | a stray pipe\n"
    )
    html = md_to_telegram_html(md)
    assert "<b>Title</b>" in html
    assert "• bullet" in html
    assert "<pre>" in html
    assert "plain text after | a stray pipe" in html  # not swallowed


def test_split_table_chunks_balance_pre() -> None:
    md = (
        "| col | val |\n|---|---|\n"
        + "\n".join(f"| row{i} | {'x' * 60} |" for i in range(150))
    )
    chunks = split_for_telegram(md_to_telegram_html(md), limit=4000)
    assert len(chunks) > 1
    for c in chunks:
        assert tlen(c) <= 4000
        assert c.count("<pre>") == c.count("</pre>")


def test_split_respects_limit_and_balances_pre() -> None:
    body = (
        "# T\n\n"
        + ("\n".join(f"line {i} with some words" for i in range(600)))
        + "\n\n```py\n"
        + ("x" * 9000)
        + "\n```\n"
    )
    chunks = split_for_telegram(md_to_telegram_html(body), limit=4000)
    assert len(chunks) > 1
    for c in chunks:
        assert tlen(c) <= 4000
        assert c.count("<pre>") == c.count("</pre>")


# ── Bindings store ──────────────────────────────────────────────────────


async def test_bindings_crud(tmp_path: Path) -> None:
    # SessionStore init creates the telegram_bindings table.
    SessionStore(db_path=tmp_path / "s.sqlite")
    store = TelegramBindingStore(tmp_path / "s.sqlite")

    assert store.get(1, 0) is None
    b = store.upsert(chat_id=1, thread_id=2, kind="topic", project_id="p1", active_session_id="s1")
    assert b.kind == "topic"
    got = store.get(1, 2)
    assert got is not None and got.project_id == "p1" and got.active_session_id == "s1"

    store.set_active_session(1, 2, "s2")
    assert store.get(1, 2).active_session_id == "s2"
    store.set_project(1, 2, None)
    assert store.get(1, 2).project_id is None
    assert store.find_by_session("s2").chat_id == 1

    store.delete(1, 2)
    assert store.get(1, 2) is None


# ── Attachments & voice ─────────────────────────────────────────────────


async def test_photo_attachment_ingested_to_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus import vault as vault_module

    monkeypatch.setattr(vault_module, "_VAULT_ROOT", tmp_path / "vault")

    h = Harness(tmp_path, FakeProvider([_final("it's a photo")]))
    msg = _msg(
        "",
    )
    msg.pop("text")
    msg["caption"] = "what is this?"
    msg["photo"] = [
        {"file_id": "small", "file_size": 10},
        {"file_id": "big", "width": 1280, "file_size": 500},
    ]
    await h.router.handle_update({"update_id": 3, "message": msg})
    await h.wait_turns_done()

    binding = h.bindings.get(1000, 0)
    session = h.store.get(binding.active_session_id)
    user_msgs = [m for m in session.history if m.role.value == "user"]
    content = user_msgs[-1].content
    assert isinstance(content, list), "attachment turns persist multipart content"

    text_parts = [p for p in content if p.kind == "text"]
    assert text_parts and "what is this?" in text_parts[0].text

    media_parts = [p for p in content if p.kind != "text"]
    assert len(media_parts) == 1
    part = media_parts[0]
    assert part.kind == "image" and part.mime_type == "image/jpeg"
    assert part.vault_path.startswith("uploads/telegram/")
    # Largest photo size was fetched and stored.
    assert (tmp_path / "vault" / part.vault_path).read_bytes() == b"FAKEDATA"

    assert any("it's a photo" in t for t in h.client.sent_texts())


async def test_document_without_caption_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus import vault as vault_module

    monkeypatch.setattr(vault_module, "_VAULT_ROOT", tmp_path / "vault")

    h = Harness(tmp_path, FakeProvider([_final("read it")]))
    msg = _msg(
        "",
    )
    msg.pop("text")
    msg["document"] = {
        "file_id": "doc1",
        "file_name": "report.pdf",
        "mime_type": "application/pdf",
        "file_size": 2048,
    }
    await h.router.handle_update({"update_id": 3, "message": msg})
    await h.wait_turns_done()

    binding = h.bindings.get(1000, 0)
    session = h.store.get(binding.active_session_id)
    user_msgs = [m for m in session.history if m.role.value == "user"]
    content = user_msgs[-1].content
    parts = [p for p in content if p.kind != "text"]
    assert len(parts) == 1 and parts[0].kind == "document"
    assert parts[0].mime_type == "application/pdf"
    assert any("read it" in t for t in h.client.sent_texts())


async def test_oversized_file_rejected_before_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus import vault as vault_module

    monkeypatch.setattr(vault_module, "_VAULT_ROOT", tmp_path / "vault")

    h = Harness(tmp_path, FakeProvider([_final("nope")]))
    msg = _msg(
        "check this",
    )
    msg["document"] = {
        "file_id": "huge",
        "file_name": "big.zip",
        "mime_type": "application/zip",
        "file_size": 25 * 1024 * 1024,  # over the 20 MB bot cap
    }
    await h.router.handle_update({"update_id": 3, "message": msg})
    await asyncio.sleep(0.1)

    assert any("Couldn't fetch" in t for t in h.client.sent_texts())
    # No turn ran: the session exists (DM auto-binding) but stays empty.
    binding = h.bindings.get(1000, 0)
    assert binding is not None
    assert h.store.get(binding.active_session_id).history == []
    assert not any("nope" in t for t in h.client.sent_texts())


async def test_voice_message_transcribed_with_voice_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus.config_schema import TTSConfig

    h = Harness(tmp_path, FakeProvider([_final("spoken answer")]))
    monkeypatch.setattr(
        "nexus.multimodal.transcribe_bytes",
        lambda data, mime: _async_value("hello from voice"),
    )

    from nexus.tts import SynthResult

    async def fake_synth(text, *, voice=None, speed=None, cfg=None):
        return SynthResult(b"RIFFWAV", "audio/wav")

    monkeypatch.setattr("nexus.tts.synthesize", fake_synth)
    monkeypatch.setattr("nexus.telegram.voice.wav_to_ogg_opus", lambda w: b"OGGBYTES")
    h.router._tts_cfg = lambda: TTSConfig(enabled=True)

    msg = _msg(
        "",
    )
    msg.pop("text")
    msg["voice"] = {"file_id": "vf1", "duration": 3, "mime_type": "audio/ogg"}
    await h.router.handle_update({"update_id": 3, "message": msg})
    await h.wait_turns_done()

    # Transcript became the turn text.
    binding = h.bindings.get(1000, 0)
    session = h.store.get(binding.active_session_id)
    user_msgs = [m for m in session.history if m.role.value == "user"]
    assert "hello from voice" in user_msgs[0].content
    assert any("spoken answer" in t for t in h.client.sent_texts())

    # Voice reply delivered after settle (transcoded to OGG/Opus).
    for _ in range(100):
        if h.client.voices:
            break
        await asyncio.sleep(0.02)
    assert h.client.voices, "voice reply was not sent"
    assert h.client.voices[0]["audio"] == b"OGGBYTES"
    assert h.client.voices[0]["thread_id"] is None  # DM: no thread


async def _async_value(value):
    return value


async def test_voice_reply_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus.config_schema import TTSConfig

    h = Harness(tmp_path, FakeProvider([_final("text only")]))
    monkeypatch.setattr(
        "nexus.multimodal.transcribe_bytes",
        lambda data, mime: _async_value("transcript"),
    )
    h.router._tts_cfg = lambda: TTSConfig(enabled=True)
    h.router.cfg.voice_replies = False

    msg = _msg(
        "",
    )
    msg.pop("text")
    msg["voice"] = {"file_id": "vf1", "duration": 2}
    await h.router.handle_update({"update_id": 3, "message": msg})
    await h.wait_turns_done()
    await asyncio.sleep(0.1)
    assert h.client.voices == []
    assert any("text only" in t for t in h.client.sent_texts())


async def test_voice_transcription_failure_notifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(tmp_path, FakeProvider([_final("unused")]))
    monkeypatch.setattr("nexus.multimodal.transcribe_bytes", lambda data, mime: _async_value(""))
    msg = _msg(
        "",
    )
    msg.pop("text")
    msg["voice"] = {"file_id": "vf1", "duration": 2}
    await h.router.handle_update({"update_id": 3, "message": msg})
    await asyncio.sleep(0.1)
    assert any("transcribe" in t for t in h.client.sent_texts())
    assert h.bindings.get(1000, 0) is None  # no turn started


# ── Turn launcher guards ────────────────────────────────────────────────


async def test_launch_turn_rejects_oversized(tmp_path: Path) -> None:
    from nexus.server.services.turn_launcher import launch_turn

    h = Harness(tmp_path, FakeProvider([_final("x")]))
    session = h.store.create()
    outcome = await launch_turn(
        agent=h.agent,
        store=h.store,
        tracker=JobTracker(),
        session=session,
        message="x" * 60_000,
    )
    assert outcome.runner is None and outcome.error is not None
    assert "too long" in outcome.error


# ── Poller loop ─────────────────────────────────────────────────────────


class PollerMockClient(FakeTGClient):
    """Blocks in get_updates like a real long-poll; releases on push()."""

    def __init__(self) -> None:
        super().__init__()
        self._updates: list[dict] = []
        self._data = asyncio.Event()

    def push(self, update: dict) -> None:
        self._updates.append(update)
        self._data.set()

    async def get_me(self) -> dict:
        return {"username": "testbot", "id": 1}

    async def get_updates(self, *, offset: int, timeout_seconds: int) -> list[dict]:
        while not self._updates:
            self._data.clear()
            await self._data.wait()
        out = self._updates.pop(0)
        return [out] if int(out.get("update_id", 0)) >= offset else []


async def test_poller_dispatches_updates_and_stops(tmp_path: Path) -> None:
    from nexus.telegram.poller import TelegramPoller

    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    agent = Agent(
        provider=FakeProvider([_final("ok")]), registry=SkillRegistry(tmp_path / "skills")
    )
    client = PollerMockClient()
    poller = TelegramPoller(
        client=client,
        agent=agent,
        store=store,
        tracker=JobTracker(),
        cfg=TelegramConfig(enabled=True, allowed_user_ids=[42]),
    )
    handled: list[int] = []

    async def fake_handle(update: dict) -> None:
        handled.append(update["update_id"])

    poller.router.handle_update = fake_handle  # type: ignore[method-assign]
    poller.start()
    client.push({"update_id": 5, "message": _msg("one")})
    client.push({"update_id": 6, "message": _msg("two", chat_id=2000)})

    deadline = time.monotonic() + 5.0
    while len(handled) < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert sorted(handled) == [5, 6]

    await poller.stop()
    assert not poller.running


# ── Config section ──────────────────────────────────────────────────────


def test_config_telegram_section_roundtrip() -> None:
    from nexus.config_file import _cfg_to_dict, _parse

    raw = {
        "telegram": {
            "enabled": True,
            "allowed_user_ids": [42, 7],
            "proxy_url": "http://127.0.0.1:7890",
            "poll_timeout_seconds": 15,
        }
    }
    cfg = _parse(raw)
    assert cfg.telegram.enabled is True
    assert cfg.telegram.allowed_user_ids == [42, 7]
    assert cfg.telegram.proxy_url == "http://127.0.0.1:7890"
    assert cfg.telegram.poll_timeout_seconds == 15

    d = _cfg_to_dict(cfg)
    assert d["telegram"]["allowed_user_ids"] == [42, 7]

    cfg2 = _parse(d)
    assert cfg2.telegram == cfg.telegram


def test_config_telegram_defaults() -> None:
    from nexus.config_file import _parse

    cfg = _parse({})
    assert cfg.telegram.enabled is False
    assert cfg.telegram.bot_token_env == "TELEGRAM_BOT_TOKEN"
    assert cfg.telegram.allowed_user_ids == []
    assert cfg.telegram.stream_edits is True


# ── Voice speechify ─────────────────────────────────────────────────────


def test_needs_speechify_heuristic() -> None:
    from nexus.telegram.voice import needs_speechify

    assert needs_speechify("Agora: 31°C, **sol** ✅")
    assert needs_speechify("18–33°C na terça")
    assert needs_speechify("chance de 50%")
    assert needs_speechify("**bold** and `code`")
    assert not needs_speechify("Tudo bem por aqui, obrigado por perguntar.")


async def test_deliver_speechify_auto_messy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus.config_schema import TTSConfig
    from nexus.telegram.voice import deliver_voice_note
    from nexus.tts import SynthResult

    h = Harness(tmp_path, FakeProvider([_final("x")]))
    synth_calls: list[str] = []

    async def fake_synth(text, *, voice=None, speed=None, cfg=None):
        synth_calls.append(text)
        return SynthResult(b"RIFF", "audio/wav")

    llm_calls: list[str] = []

    async def fake_llm(agent, cfg, prompt, **kwargs):
        llm_calls.append(prompt)
        return "resposta reescrita para fala"

    monkeypatch.setattr("nexus.tts.synthesize", fake_synth)
    monkeypatch.setattr("nexus.voice_ack._generate_text", fake_llm)
    monkeypatch.setattr("nexus.telegram.voice.wav_to_ogg_opus", lambda w: b"OGG")

    ok = await deliver_voice_note(
        h.client,
        1000,
        0,
        "Agora: **31°C** ✅ com 50% de chance",
        tts_cfg=TTSConfig(enabled=True),
        agent=object(),
        speechify_mode="auto",
    )
    assert ok is True
    assert len(llm_calls) == 1 and "31°C" in llm_calls[0]
    assert synth_calls == ["resposta reescrita para fala"]
    assert h.client.voices and h.client.voices[0]["audio"] == b"OGG"


async def test_deliver_speechify_auto_clean_skips_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus.config_schema import TTSConfig
    from nexus.telegram.voice import deliver_voice_note
    from nexus.tts import SynthResult

    h = Harness(tmp_path, FakeProvider([_final("x")]))
    synth_calls: list[str] = []

    async def fake_synth(text, *, voice=None, speed=None, cfg=None):
        synth_calls.append(text)
        return SynthResult(b"RIFF", "audio/wav")

    async def fail_llm(*a, **k):  # pragma: no cover — must not be reached
        raise AssertionError("LLM should not be called for clean text")

    monkeypatch.setattr("nexus.tts.synthesize", fake_synth)
    monkeypatch.setattr("nexus.voice_ack._generate_text", fail_llm)

    ok = await deliver_voice_note(
        h.client,
        1000,
        0,
        "Tudo certo, resolvi o seu pedido.",
        tts_cfg=TTSConfig(enabled=True),
        agent=object(),
        speechify_mode="auto",
    )
    assert ok is True
    assert synth_calls and "Tudo certo" in synth_calls[0]


async def test_deliver_speechify_off_never_calls_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus.config_schema import TTSConfig
    from nexus.telegram.voice import deliver_voice_note
    from nexus.tts import SynthResult

    h = Harness(tmp_path, FakeProvider([_final("x")]))

    async def fake_synth(text, *, voice=None, speed=None, cfg=None):
        return SynthResult(b"RIFF", "audio/wav")

    async def fail_llm(*a, **k):  # pragma: no cover
        raise AssertionError("LLM should not be called when off")

    monkeypatch.setattr("nexus.tts.synthesize", fake_synth)
    monkeypatch.setattr("nexus.voice_ack._generate_text", fail_llm)

    ok = await deliver_voice_note(
        h.client,
        1000,
        0,
        "bagunçado **31°C** ✅",
        tts_cfg=TTSConfig(enabled=True),
        agent=object(),
        speechify_mode="off",
    )
    assert ok is True


async def test_speechify_timeout_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus.telegram.voice import speechify

    async def slow_llm(agent, cfg, prompt, **kwargs):
        await asyncio.sleep(2.0)
        return "never"

    monkeypatch.setattr("nexus.voice_ack._generate_text", slow_llm)
    out = await speechify(object(), "texto original", timeout=0.05)
    assert out == "texto original"


async def test_speechify_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus.telegram.voice import speechify

    async def boom(agent, cfg, prompt, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("nexus.voice_ack._generate_text", boom)
    out = await speechify(object(), "texto original", timeout=1.0)
    assert out == "texto original"


async def test_topics_lists_bindings(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    from nexus.server.project_store import ProjectStore

    pstore = ProjectStore(h.tmp_path / "sessions.sqlite")
    apollo = pstore.create(name="Apollo")
    borea = pstore.create(name="Borea")
    a_session = h.store.create(context="Telegram: topic 1000/5", project_id=apollo.id)
    h.store.rename(a_session.id, "main thread")
    h.bindings.upsert(
        chat_id=1000,
        thread_id=5,
        kind="topic",
        project_id=apollo.id,
        active_session_id=a_session.id,
    )
    h.bindings.upsert(
        chat_id=2000,
        thread_id=0,
        kind="group",
        project_id=borea.id,
        active_session_id=h.store.create().id,
    )

    # Asked from inside topic 1000/5 — that row gets the "here" marker.
    await h.message("/topics", chat_type="supergroup", thread_id=5)
    text = h.client.sent_texts()[-1]
    assert "Apollo" in text and "Borea" in text
    assert "main thread" in text
    assert "✅" in text
    assert str(1000) in text and str(5) in text and str(2000) in text

    # Empty state.
    h2 = Harness(tmp_path / "empty", FakeProvider([_final("ok")]))
    await h2.message("/topics", chat_type="supergroup", thread_id=9)
    assert "No chats or topics are linked" in h2.client.sent_texts()[-1]


async def test_group_turn_titles_from_raw_text(tmp_path: Path, monkeypatch: Any) -> None:
    """Group messages are prefixed with the sender for the agent, but the
    autotitle must receive the raw text (no "From <sender>:" prefix)."""
    import nexus.server.services.turn_launcher as tl

    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    captured: dict[str, Any] = {}

    class _Outcome:
        error = None
        queued = False
        runner = None

    async def _spy_launch_turn(**kwargs):
        captured.update(kwargs)
        return _Outcome()

    monkeypatch.setattr(tl, "launch_turn", _spy_launch_turn)
    monkeypatch.setattr("nexus.telegram.router.launch_turn", _spy_launch_turn)
    from nexus.server.project_store import ProjectStore

    ProjectStore(h.tmp_path / "sessions.sqlite").create(name="Apollo")
    await h.message("/project Apollo", chat_type="supergroup", thread_id=7)
    await h.message(
        "what is celon",
        chat_type="supergroup",
        thread_id=7,
    )
    assert "From " in captured["message"]
    assert captured["autotitle_message"] == "what is celon"


async def test_set_message_reaction_payload_shape() -> None:
    """Regression: setMessageReaction must send reaction=[{type: emoji, …}]
    — the old `emoji` param was silently ignored by Telegram (a missing
    `reaction` clears reactions), so acks succeeded invisibly."""
    from nexus.telegram.api import TelegramClient

    captured: list[tuple[str, dict]] = []

    class _Cap(TelegramClient):
        async def _call(self, method, payload, *, retries=2, files=None):
            captured.append((method, payload))
            return True

    client = _Cap.__new__(_Cap)
    await client.set_message_reaction(42, 7, "\U0001f440")
    await client.set_message_reaction(42, 7, "")
    method, payload = captured[0]
    assert method == "setMessageReaction"
    assert payload["reaction"] == [{"type": "emoji", "emoji": "\U0001f440"}]
    assert "emoji" not in payload  # the old broken param
    assert captured[1][1]["reaction"] == []  # empty clears


async def test_vault_link_renders_as_code_and_extracts() -> None:
    from nexus.telegram.formatting import extract_vault_links, md_to_telegram_html

    md = "Salvo: [projects/p/energia.md](vault://projects/p/energia.md) — [docs](https://x.com)"
    html = md_to_telegram_html(md)
    assert "<code>projects/p/energia.md</code>" in html
    assert "vault://" not in html
    assert '<a href="https://x.com">docs</a>' in html
    assert extract_vault_links(md) == ["projects/p/energia.md"]
    assert extract_vault_links("a [x](vault://a.md) [x](vault://a.md) [y](vault://b.md)") == [
        "a.md",
        "b.md",
    ]


async def test_finalize_reply_attaches_vault_buttons(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    text = "Salvo: [projects/p/nota.md](vault://projects/p/nota.md)"
    await h.router._finalize_reply(1000, 0, 0, text)
    kb = h.client.sent[-1]["kb"]
    assert kb is not None
    flat = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert any("nota.md" in t for t in flat)
    msg_id = 100 + len(h.client.sent)
    assert h.router._vault_menus[(1000, msg_id)] == ["projects/p/nota.md"]

    # No links → no keyboard.
    await h.router._finalize_reply(1000, 0, 0, "plain reply")
    assert h.client.sent[-1]["kb"] is None


async def test_vf_callback_sends_document(tmp_path: Path, monkeypatch: Any) -> None:
    from nexus import vault as vault_mod

    root = tmp_path / "vault"
    (root / "projects/p").mkdir(parents=True)
    (root / "projects/p/nota.md").write_text("# Nota\nconteúdo")
    monkeypatch.setattr(vault_mod, "_VAULT_ROOT", root)

    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    h.router._remember_vault_menu(1000, 55, ["projects/p/nota.md"])
    await h.callback("vf:0", message_override=55)
    assert len(h.client.documents) == 1
    doc = h.client.documents[0]
    assert doc["filename"] == "nota.md"
    assert doc["caption"] == "projects/p/nota.md"
    assert "conteúdo".encode() in doc["data"]


async def test_vault_command_browsing_and_file(tmp_path: Path, monkeypatch: Any) -> None:
    from nexus import vault as vault_mod

    root = tmp_path / "vault"
    (root / "projects/p1").mkdir(parents=True)
    (root / "projects/p1/um.md").write_text("# Um")
    (root / "soltos").mkdir()
    (root / "soltos/dois.md").write_text("# Dois")
    monkeypatch.setattr(vault_mod, "_VAULT_ROOT", root)

    h = Harness(tmp_path, FakeProvider([_final("ok")]))

    # Root listing: folders as buttons, no keyboard-less fallback.
    await h.message("/vault")
    kb = h.client.sent[-1]["kb"]
    flat = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert any("projects" in t for t in flat) and any("soltos" in t for t in flat)
    root_msg = 100 + len(h.client.sent)
    listing = h.router._vault_menus[(1000, root_msg)]

    # Enter projects/ → its folder p1 as a button.
    proj_idx = listing.index("projects")
    await h.callback(f"vd:{proj_idx}", message_override=root_msg)
    flat2 = [b["text"] for row in h.client.sent[-1]["kb"]["inline_keyboard"] for b in row]
    assert any("p1" in t for t in flat2)
    proj_msg = 100 + len(h.client.sent)
    listing2 = h.router._vault_menus[(1000, proj_msg)]

    # Enter projects/p1/ → file um.md; clicking it sends the document.
    p1_idx = listing2.index("projects/p1")
    await h.callback(f"vd:{p1_idx}", message_override=proj_msg)
    flat3 = [b["text"] for row in h.client.sent[-1]["kb"]["inline_keyboard"] for b in row]
    assert any("um.md" in t for t in flat3)
    p1_msg = 100 + len(h.client.sent)
    listing3 = h.router._vault_menus[(1000, p1_msg)]

    file_idx = listing3.index("projects/p1/um.md")
    await h.callback(f"vd:{file_idx}", message_override=p1_msg)
    assert h.client.documents[-1]["filename"] == "um.md"

    # Direct file path in the command sends immediately.
    await h.message("/vault soltos/dois.md")
    assert h.client.documents[-1]["filename"] == "dois.md"


async def test_finalize_reply_edit_path_registers_menu(tmp_path: Path) -> None:
    """Regression: on the normal streaming path the reply message is EDITED
    (not resent) — the 📂 menu must be registered under the edited message's
    id, else the button answers 'Menu expired'."""
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    text = "Salvo: [projects/p/nota.md](vault://projects/p/nota.md)"
    await h.router._finalize_reply(1000, 0, 777, text)
    # Keyboard attached to the edit of message 777…
    edit = [e for e in h.client.edits if e["message_id"] == 777][-1]
    assert edit["kb"] is not None
    # …and the menu registered under 777, not under a fresh send id.
    assert h.router._vault_menus[(1000, 777)] == ["projects/p/nota.md"]
