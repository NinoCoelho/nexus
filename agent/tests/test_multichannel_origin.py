"""Multi-channel chat reliability + origin tagging.

Covers the 2026-11 fixes:

- SSE serialization hardening: ``done`` events carrying live
  ``ChatMessage`` objects used to reach ``json.dumps`` and kill the
  session events channel exactly when a turn completed — the
  "spinner forever" bug (5 crashes on 2026-11-20).
- Per-message channel origin persistence (``messages.origin``) and
  its survival across full-history rewrites.
- The managed-browser gate for side-panel turns
  (``[browser] extension_policy``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from nexus.agent.context import TURN_SURFACE
from nexus.agent.llm import ChatMessage, Role
from nexus.browser.tool import BrowserHandler
from nexus.server.events import SessionEvent, json_safe
from nexus.server.schemas import ChatRequest
from nexus.server.session_store import SessionStore
from nexus.server.services.chat_turn_runner import ChatTurnRunner


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(db_path=tmp_path / "sessions.sqlite")


def _runner(store: SessionStore, sid: str) -> ChatTurnRunner:
    return ChatTurnRunner(
        agent=None,  # type: ignore[arg-type]
        store=store,
        session_id=sid,
        message="hi",
        context="",
        model_id="",
        pre_turn_history=[],
        attachment_parts=None,
        resume_working_messages=None,
        tracker=SimpleNamespace(),  # type: ignore[arg-type]
        turn_job_id="job",
        publish_job_event=None,
    )


class _Opaque:
    """Anything json.dumps cannot handle."""

    v = 42


# ── SSE serialization hardening ────────────────────────────────────────────


def test_to_sse_survives_opaque_payload() -> None:
    ev = SessionEvent(kind="done", data={"type": "done", "messages": [_Opaque()]})
    frame = ev.to_sse()
    assert frame.startswith(b"event: done\n")
    payload = json.loads(frame.split(b"data: ", 1)[1].split(b"\n\n", 1)[0])
    # Opaque object stringified instead of raising TypeError.
    assert isinstance(payload["messages"][0], str)


def test_json_safe_converts_chat_message() -> None:
    msgs = [
        ChatMessage(role=Role.USER, content="hi"),
        ChatMessage(role=Role.ASSISTANT, content="ok"),
    ]
    out = json_safe({"type": "done", "messages": msgs})
    json.dumps(out)  # must not raise
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][1]["content"] == "ok"


async def test_runner_publish_sanitizes_done_event(tmp_path: Path) -> None:
    """The republished ``done`` event must be JSON-safe on the bus —
    the exact payload that crashed the SSE channel on 2026-11-20."""
    store = _store(tmp_path)
    s = store.create()
    runner = _runner(store, s.id)

    async def consume() -> SessionEvent:
        async for ev in store.subscribe(s.id):
            return ev

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # let the subscriber attach
    runner._publish({
        "type": "done",
        "session_id": s.id,
        "reply": "r",
        "messages": [ChatMessage(role=Role.USER, content="hi")],
    })
    ev = await asyncio.wait_for(task, timeout=2)
    json.dumps(ev.data)  # must not raise
    assert ev.data["messages"][0]["role"] == "user"


# ── Per-message channel origin ─────────────────────────────────────────────


def test_message_origin_roundtrip_and_prefix_survival(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create()

    h1 = [ChatMessage(role=Role.USER, content="from chrome")]
    store.replace_history(s.id, h1, origins=["chrome-extension"])
    got = store.get(s.id)
    assert got.message_origins == {0: "chrome-extension"}

    # Second turn (web) rewrites the WHOLE history (loom's
    # DELETE+INSERT model) — the old tag must survive the rewrite.
    h2 = h1 + [
        ChatMessage(role=Role.ASSISTANT, content="reply"),
        ChatMessage(role=Role.USER, content="from web"),
    ]
    store.replace_history(s.id, h2, origins=[None, None, "web"])
    got = store.get(s.id)
    assert got.message_origins == {0: "chrome-extension", 2: "web"}


def test_persist_stream_turn_tags_turn_messages(tmp_path: Path) -> None:
    from nexus.server.routes.chat_stream_helpers import persist_stream_turn

    store = _store(tmp_path)
    s = store.create()
    pre = [ChatMessage(role=Role.USER, content="earlier")]
    store.replace_history(s.id, pre)  # legacy, untagged

    final = pre + [
        ChatMessage(role=Role.USER, content="go"),
        ChatMessage(role=Role.ASSISTANT, content="done-reply"),
    ]
    persist_stream_turn(
        store=store,
        session_id=s.id,
        final_messages=final,
        pre_turn_history=pre,
        user_message="go",
        accumulated_text="done-reply",
        accumulated_tools=[],
        partial_status="",
        turn_origin="ios",
    )
    got = store.get(s.id)
    assert got.message_origins == {1: "ios", 2: "ios"}


def test_chat_request_accepts_surface() -> None:
    assert ChatRequest(message="hi", surface="chrome-extension").surface == "chrome-extension"
    assert ChatRequest(message="hi").surface == "web"


# ── Managed-browser gate for side-panel turns ──────────────────────────────


class _DummyManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def main_key(self) -> str:
        return "main"

    def project_key(self, pid: str) -> str:
        return f"project:{pid}"

    async def act(self, key: str, action: str, args: dict) -> dict:
        self.calls.append(action)
        return {"ok": True}


def _patch_policy(monkeypatch, policy: str) -> None:
    import nexus.config_file as cf

    monkeypatch.setattr(
        cf,
        "load_cached",
        lambda: SimpleNamespace(
            browser=SimpleNamespace(extension_policy=policy)
        ),
    )


async def test_browser_gate_refuses_on_extension_surface(monkeypatch) -> None:
    _patch_policy(monkeypatch, "refuse")
    handler = BrowserHandler(manager=_DummyManager(), session_store=None)
    token = TURN_SURFACE.set("chrome-extension")
    try:
        out = json.loads(await handler.invoke({"action": "read"}))
        assert out["ok"] is False
        assert out["gate"] == "extension_policy"
        assert "page` tool" in out["error"]
        assert handler._manager.calls == []
    finally:
        TURN_SURFACE.reset(token)


async def test_browser_gate_allows_other_surfaces(monkeypatch) -> None:
    _patch_policy(monkeypatch, "refuse")
    handler = BrowserHandler(manager=_DummyManager(), session_store=None)
    token = TURN_SURFACE.set("web")
    try:
        out = json.loads(await handler.invoke({"action": "read"}))
        assert out["ok"] is True
        assert handler._manager.calls == ["read"]
    finally:
        TURN_SURFACE.reset(token)


async def test_browser_gate_ask_mode_requires_approval(monkeypatch) -> None:
    _patch_policy(monkeypatch, "ask")
    handler = BrowserHandler(manager=_DummyManager(), session_store=None)
    token = TURN_SURFACE.set("chrome-extension")
    try:
        blocked = json.loads(await handler.invoke({"action": "navigate", "url": "https://x.example"}))
        assert blocked["ok"] is False
        assert blocked["policy"] == "ask"
        assert handler._manager.calls == []

        approved = json.loads(
            await handler.invoke({
                "action": "navigate", "url": "https://x.example", "approved": True,
            })
        )
        assert approved["ok"] is True
        assert handler._manager.calls == ["navigate"]
    finally:
        TURN_SURFACE.reset(token)


# ── Display cleaning of the side-panel <context> pointer ───────────────────


from nexus.server.message_display import (  # noqa: E402
    context_preamble_meta,
    display_user_text,
    strip_context_preamble,
    strip_team_pass_marker,
)

_PREAMBLE_MSG = (
    "<context>\n"
    "Session: Nexus Chrome side panel on the user's real browser tab.\n"
    "This block is a POINTER ONLY — it does not contain the page.\n"
    "url: https://example.com/article\n"
    "title: Example Article\n"
    "excerpt (for identification only):\n"
    "Some excerpt text that used to show up raw on iOS…\n"
    "</context>\n\n"
    "resuma isso pra mim"
)


def test_strip_context_preamble() -> None:
    assert strip_context_preamble(_PREAMBLE_MSG) == "resuma isso pra mim"
    assert strip_context_preamble("plain message") == "plain message"
    assert strip_context_preamble("") == ""
    # Mid-text blocks are left alone (only a LEADING block is a preamble).
    assert "<context>" in strip_context_preamble("oi <context>x</context> fim")


def test_context_preamble_meta() -> None:
    meta = context_preamble_meta(_PREAMBLE_MSG)
    assert meta == {"url": "https://example.com/article", "title": "Example Article"}
    assert context_preamble_meta("no block here") is None


def test_display_user_text_team_marker_then_context() -> None:
    """Exact shape of a team-session panel message (the @hype case):
    the pass marker PRECEDES the <context> block, so a leading-only
    context strip would miss it. Order matters."""
    msg = "<!-- nx:team -->@montador\n" + _PREAMBLE_MSG
    assert display_user_text(msg) == "resuma isso pra mim"
    _, handle = strip_team_pass_marker(msg)
    assert handle == "@montador"
    # Pure pass: the handle stays visible instead of an empty bubble.
    assert display_user_text("<!-- nx:team -->@montador\n") == "@montador"
    # Leading-context strip ALONE must fail on this shape (regression
    # guard for the ordering bug).
    assert strip_context_preamble(msg).startswith("<!-- nx:team -->")


def test_title_derivation_skips_preamble(tmp_path: Path) -> None:
    store = _store(tmp_path)
    s = store.create()
    store.replace_history(s.id, [ChatMessage(role=Role.USER, content=_PREAMBLE_MSG)])
    assert store.get(s.id).title == "resuma isso pra mim"
    # Self-heal: an already-polluted title is re-derived on the next write.
    s2 = store.create()
    store._loom.set_title(s2.id, "<context>\nSession: Nexus Chrome side pan")
    store.replace_history(s2.id, [ChatMessage(role=Role.USER, content=_PREAMBLE_MSG)])
    assert store.get(s2.id).title == "resuma isso pra mim"


async def test_turn_started_echo_is_display_clean(tmp_path: Path) -> None:
    """Other channels (Telegram echo, iOS, panel) must never receive the
    raw <context> pointer on turn_started."""
    store = _store(tmp_path)
    s = store.create()
    runner = ChatTurnRunner(
        agent=None,  # type: ignore[arg-type]
        store=store,
        session_id=s.id,
        message=_PREAMBLE_MSG,
        context="",
        model_id="",
        pre_turn_history=[],
        attachment_parts=None,
        resume_working_messages=None,
        tracker=SimpleNamespace(),  # type: ignore[arg-type]
        turn_job_id="job",
        publish_job_event=None,
    )

    async def consume() -> SessionEvent:
        async for ev in store.subscribe(s.id):
            return ev

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    runner.start()
    if runner.task is not None:
        runner.task.cancel()  # start() already published turn_started
    ev = await asyncio.wait_for(task, timeout=2)
    assert ev.kind == "turn_started"
    assert ev.data["message"] == "resuma isso pra mim"
    # The runner keeps the full pointer for the LLM.
    assert runner.message == _PREAMBLE_MSG


async def test_browser_gate_allow_mode_disables_gate(monkeypatch) -> None:
    _patch_policy(monkeypatch, "allow")
    handler = BrowserHandler(manager=_DummyManager(), session_store=None)
    token = TURN_SURFACE.set("chrome-extension")
    try:
        out = json.loads(await handler.invoke({"action": "read"}))
        assert out["ok"] is True
    finally:
        TURN_SURFACE.reset(token)
