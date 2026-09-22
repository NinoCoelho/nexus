"""Integration tests for queue-then-inject: sending messages while a turn
is already processing.

Covers the three lifecycle paths:
- mid-turn injection at a tool-batch boundary (queued ack → user_injected
  → loom restart with the injected user message),
- chaining: the turn finishes before any boundary, the runner starts a
  follow-up turn with the queued message on the same stream,
- queue removal via DELETE /chat/{sid}/queue/{qid}.

Uses the real-uvicorn harness from test_server_sse (ASGITransport buffers
SSE bodies, breaking concurrent-request tests).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from test_server_sse import FakeProvider, _iter_sse_events, _serve

from nexus.agent.llm import ChatResponse, StopReason, ToolCall


class GatedProvider(FakeProvider):
    """FakeProvider whose first N ``chat()`` calls block on an event.

    Lets the test enqueue messages while the turn is genuinely
    in-flight, then release the model. Records the message list of
    every call so assertions can inspect what the model actually saw.
    """

    def __init__(self, responses: list[ChatResponse], gates: int = 1) -> None:
        super().__init__(responses)
        self._gates_remaining = gates
        self.gate = asyncio.Event()
        self.calls: list[list[Any]] = []

    async def chat(self, messages, *, tools=None, model=None, max_tokens=None) -> ChatResponse:  # noqa: ANN001
        self.calls.append(list(messages))
        if self._gates_remaining > 0:
            self._gates_remaining -= 1
            await self.gate.wait()
        return await super().chat(messages, tools=tools, model=model, max_tokens=max_tokens)


def _tool_response(call_id: str = "tc1") -> ChatResponse:
    return ChatResponse(
        content=None,
        stop_reason=StopReason.TOOL_USE,
        tool_calls=[ToolCall(id=call_id, name="nonexistent_probe", arguments={})],
    )


def _final(text: str) -> ChatResponse:
    return ChatResponse(content=text, stop_reason=StopReason.STOP)


async def _drain_events_until(
    response: httpx.Response, predicate, timeout: float = 15.0
) -> list[tuple[str, dict[str, Any]]]:
    """Collect SSE (kind, data) pairs from a response until predicate
    matches on one of them (inclusive), or the stream ends."""
    out: list[tuple[str, dict[str, Any]]] = []
    async def _collect() -> None:
        async for kind, data in _iter_sse_events(response):
            out.append((kind, data))
            if predicate(kind, data):
                return
    await asyncio.wait_for(_collect(), timeout=timeout)
    return out


async def _wait_turn_active(client: httpx.AsyncClient, base: str, sid: str) -> None:
    """Poll GET /chat/{sid}/turn/active until the detached runner registers."""
    for _ in range(100):
        r = await client.get(f"{base}/chat/{sid}/turn/active")
        if r.status_code == 200 and r.json().get("running"):
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("turn did not become active within 5s")


async def test_queued_message_injected_mid_turn(tmp_path: Path) -> None:
    provider = GatedProvider([_tool_response(), _final("done-after-injection")], gates=1)
    async with _serve(provider, tmp_path) as base:
        async with httpx.AsyncClient(timeout=20) as client:
            # Start turn 1 — the first LLM call parks on the gate.
            async with client.stream(
                "POST", f"{base}/chat/stream",
                json={"message": "check the news", "session_id": "s-queue-1"},
            ) as resp1:
                await _wait_turn_active(client, base, "s-queue-1")

                # Queue a follow-up while the turn runs.
                async with client.stream(
                    "POST", f"{base}/chat/stream",
                    json={"message": "write an article about them", "session_id": "s-queue-1"},
                ) as ack:
                    ack_events = [e async for e in _iter_sse_events(ack)]
                kinds = [k for k, _ in ack_events]
                assert "queued" in kinds
                qid = next(dict(d)["qid"] for k, d in ack_events if k == "queued")

                provider.gate.set()  # release the model
                events = await _drain_events_until(resp1, lambda k, d: k == "turn_settled")

    kinds = [k for k, _ in events]
    assert "user_injected" in kinds, f"expected user_injected in {kinds}"
    injected = next(d for k, d in events if k == "user_injected")
    assert injected["qid"] == qid
    assert injected["text"] == "write an article about them"
    # done arrives after the injection.
    assert kinds.index("user_injected") < kinds.index("done")

    # The second LLM call saw the injected message as a USER message.
    assert len(provider.calls) == 2
    second = provider.calls[1]
    injected_roles = [
        m.role for m in second if getattr(m, "content", "") == "write an article about them"
    ]
    assert injected_roles, "injected user message missing from second LLM call"
    assert all(r in ("user", "USER") or str(r).lower() == "user" for r in injected_roles)


async def test_queued_message_chained_after_done(tmp_path: Path) -> None:
    provider = GatedProvider([_final("first turn answer"), _final("second turn answer")], gates=1)
    async with _serve(provider, tmp_path) as base:
        async with httpx.AsyncClient(timeout=20) as client:
            async with client.stream(
                "POST", f"{base}/chat/stream",
                json={"message": "check the news", "session_id": "s-queue-2"},
            ) as resp1:
                await _wait_turn_active(client, base, "s-queue-2")

                async with client.stream(
                    "POST", f"{base}/chat/stream",
                    json={"message": "now summarize", "session_id": "s-queue-2"},
                ) as ack:
                    ack_kinds = [k async for k, _ in _iter_sse_events(ack)]
                assert "queued" in ack_kinds

                provider.gate.set()
                events = await _drain_events_until(resp1, lambda k, d: k == "turn_settled")

    kinds = [k for k, _ in events]
    # Turn 1 done → user_injected → chained turn done → settled.
    assert kinds.count("done") == 2, f"expected two done events, got {kinds}"
    assert "user_injected" in kinds
    assert kinds.index("user_injected") > kinds.index("done")
    assert kinds[-1] == "turn_settled"

    # The chained turn ran with the queued message as its user input.
    assert len(provider.calls) == 2
    second = provider.calls[1]
    assert any(
        getattr(m, "content", "") == "now summarize" for m in second
    ), "queued message missing from chained LLM call"


async def test_remove_queued_message(tmp_path: Path) -> None:
    provider = GatedProvider([_tool_response(), _final("after removal")], gates=1)
    async with _serve(provider, tmp_path) as base:
        async with httpx.AsyncClient(timeout=20) as client:
            async with client.stream(
                "POST", f"{base}/chat/stream",
                json={"message": "check the news", "session_id": "s-queue-3"},
            ) as resp1:
                await _wait_turn_active(client, base, "s-queue-3")

                async with client.stream(
                    "POST", f"{base}/chat/stream",
                    json={"message": "keep this one", "session_id": "s-queue-3"},
                ) as ack1:
                    ev1 = {k: d for k, d in [e async for e in _iter_sse_events(ack1)]}
                async with client.stream(
                    "POST", f"{base}/chat/stream",
                    json={"message": "drop this one", "session_id": "s-queue-3"},
                ) as ack2:
                    ev2 = {k: d for k, d in [e async for e in _iter_sse_events(ack2)]}
                keep_qid = ev1["queued"]["qid"]
                drop_qid = ev2["queued"]["qid"]

                deleted = await client.request(
                    "DELETE", f"{base}/chat/s-queue-3/queue/{drop_qid}"
                )
                assert deleted.status_code == 200

                provider.gate.set()
                events = await _drain_events_until(resp1, lambda k, d: k == "turn_settled")

    injected_texts = [d["text"] for k, d in events if k == "user_injected"]
    assert injected_texts == ["keep this one"], f"unexpected injections: {injected_texts}"
    removed = [d for k, d in events if k == "queue_removed"]
    assert any(r["qid"] == drop_qid for r in removed), f"queue_removed missing: {removed}"
    assert all(r["qid"] != keep_qid for r in removed)
    # keep_qid survives — never removed.
    _ = keep_qid


async def test_no_second_runner_while_turn_active(tmp_path: Path) -> None:
    """A POST during a running turn never spawns a parallel loop — the
    pre-flight context check uses history, so a parallel loop would show
    up as the turn-1 stream containing a *second* user message delta or
    the provider seeing an unexpected extra call."""
    provider = GatedProvider([_final("only turn")], gates=1)
    async with _serve(provider, tmp_path) as base:
        async with httpx.AsyncClient(timeout=20) as client:
            async with client.stream(
                "POST", f"{base}/chat/stream",
                json={"message": "first", "session_id": "s-queue-4"},
            ) as resp1:
                await _wait_turn_active(client, base, "s-queue-4")
                async with client.stream(
                    "POST", f"{base}/chat/stream",
                    json={"message": "queued", "session_id": "s-queue-4"},
                ) as ack:
                    assert "queued" in [k async for k, _ in _iter_sse_events(ack)]
                provider.gate.set()
                await _drain_events_until(resp1, lambda k, d: k == "turn_settled")

    # The queued message chained into a second provider call — never a
    # parallel first-call.
    assert len(provider.calls) == 2
    assert provider.calls[0][0] is not provider.calls[1][0]


async def test_slash_command_during_turn_rejected(tmp_path: Path) -> None:
    provider = GatedProvider([_final("ok")], gates=1)
    async with _serve(provider, tmp_path) as base:
        async with httpx.AsyncClient(timeout=20) as client:
            async with client.stream(
                "POST", f"{base}/chat/stream",
                json={"message": "hello", "session_id": "s-queue-5"},
            ) as resp1:
                await _wait_turn_active(client, base, "s-queue-5")
                rejected = await client.post(
                    f"{base}/chat/stream",
                    json={"message": "/help", "session_id": "s-queue-5"},
                )
                assert rejected.status_code == 409
                provider.gate.set()
                await _drain_events_until(resp1, lambda k, d: k == "turn_settled")
