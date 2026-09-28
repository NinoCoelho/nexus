"""Telegram gateway tests — in-process router + fake Bot API client.

Covers: allowlist auth, DM chat flow (message → session → streamed reply),
topic binding hints, /project binding + session adoption, /new + /chats +
switch callbacks, /title /usage /id, queue-while-busy ack, HITL forwarding
with inline-button resolution, formatting, bindings CRUD, and the turn
launcher's rejection paths.

Reuses ``FakeProvider``/``GatedProvider`` from the SSE/queue harnesses so
the full agent loop runs against a scripted provider.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

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

    async def send_text_safe(self, chat_id, text, *, thread_id=None, reply_markup=None):
        self.sent.append(
            {"chat_id": chat_id, "text": text, "thread_id": thread_id, "kb": reply_markup}
        )
        return 100 + len(self.sent)

    async def edit_text_safe(self, chat_id, message_id, text, *, reply_markup=None):
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, "kb": reply_markup}
        )
        return True

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
        self.agent = Agent(
            provider=provider, registry=SkillRegistry(tmp_path / "skills")
        )
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
        self, data: str, *, chat_id: int = 1000, thread_id: int = 0, user_id: int = 42
    ) -> None:
        msg: dict[str, Any] = {
            "message_id": 99,
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
    assert any("not authorized" in t for t in h.client.sent_texts())
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


async def test_unbound_topic_gets_hint_no_session(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    await h.message("topic msg", chat_type="supergroup", thread_id=7)
    await asyncio.sleep(0.05)
    assert h.bindings.get(1000, 7) is None
    assert any("isn't linked" in t for t in h.client.sent_texts())
    # Hint shown only once
    h.client.sent.clear()
    await h.message("another", chat_type="supergroup", thread_id=7)
    await asyncio.sleep(0.05)
    assert h.client.sent == []


# ── Commands ────────────────────────────────────────────────────────────


async def test_project_bind_adopts_latest_session(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    from nexus.server.project_store import ProjectStore

    pstore = ProjectStore(h.tmp_path / "sessions.sqlite")
    project = pstore.create(name="Apollo")
    existing = h.store.create(context="seed", project_id=project.id)
    h.store.replace_history(
        existing.id, [ChatMessage(role=Role.USER, content="earlier work")]
    )

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
    assert any("Queued" in t for t in h.client.sent_texts())

    provider.gate.set()
    await h.wait_turns_done()

    all_text = " ".join(h.client.sent_texts() + [e["text"] for e in h.client.edits])
    assert "first reply" in all_text
    assert "second reply" in all_text


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
    await fwd._forward(sid, {
        "request_id": "req1",
        "prompt": "Run this destructive command?",
        "kind": "confirm",
        "choices": None,
        "default": None,
    })
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
    keyboard, reason = h.router.build_hitl_keyboard(
        "sid1", "req9", {"kind": "choice", "choices": ["Red", "Blue"]}
    )
    assert keyboard is not None and reason is None
    labels = [b["text"] for row in keyboard for b in row]
    assert labels == ["Red", "Blue"]
    key = keyboard[0][0]["callback_data"][3:]
    assert h.router._hitl_buttons[key] == ("sid1", "req9", "Red")


async def test_hitl_text_kind_no_buttons(tmp_path: Path) -> None:
    h = Harness(tmp_path, FakeProvider([_final("ok")]))
    keyboard, reason = h.router.build_hitl_keyboard("sid1", "req9", {"kind": "text"})
    assert keyboard is None and reason is not None


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
    b = store.upsert(
        chat_id=1, thread_id=2, kind="topic", project_id="p1", active_session_id="s1"
    )
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
