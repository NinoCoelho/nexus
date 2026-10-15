"""Server-harness tests for the bot→bot cascade in team (group) chats.

Covers the three behaviours the cascade promises:
- a reply that @mentions a teammate queues that teammate's turn (chained,
  own persona, own turn) and the conversation continues until a reply
  mentions nobody;
- team passes are chain-only: the round-mode fan-out markers are never
  injected mid-turn into the lead's turn (the pre-cascade collapse bug);
- the cascade budget caps the conversation and the continue endpoint can
  relaunch it (bot_cascade_capped + POST /sessions/{sid}/team/continue).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from test_server_sse import FakeProvider, _iter_sse_events, _serve

from nexus.agent.llm import ChatResponse, StopReason
from nexus.server.project_store import ProjectStore
from nexus.server.session_store import SessionStore


@pytest.fixture(autouse=True)
def _no_ocr_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the OCR llama-server bootstrap — the dev config wires a real
    chandra model and the lifespan blocks on its (slow) health check,
    which has nothing to do with the cascade."""
    async def _noop() -> None:
        return None

    monkeypatch.setattr("nexus.server.app_lifespan._startup_ocr", _noop)


def _final(text: str) -> ChatResponse:
    return ChatResponse(content=text, stop_reason=StopReason.STOP)


async def _setup_team(tmp_path: Path, mode: str = "lead") -> tuple[SessionStore, Any, Any, Any]:
    """Bots Alpha (lead) + Beta on the harness's sessions DB."""
    store = SessionStore(db_path=tmp_path / "sessions.sqlite")
    ps = ProjectStore(tmp_path / "sessions.sqlite")
    alpha = ps.create(name="Alpha Cascade")
    beta = ps.create(name="Beta Cascade")
    sess = store.create(project_id=alpha.id)
    store.rename(sess.id, "Cascade Team")
    store.set_team(sess.id, {"name": "Cascade", "members": [alpha.id, beta.id], "mode": mode})
    return store, ps, alpha, beta, sess


async def _run_turn(base: str, sid: str, message: str) -> list[tuple[str, dict[str, Any]]]:
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "POST", f"{base}/chat/stream",
            json={"message": message, "session_id": sid},
        ) as resp:
            return [e async for e in _iter_sse_events(resp)]


async def test_cascade_chains_until_no_mention(tmp_path: Path) -> None:
    store, ps, alpha, beta, sess = await _setup_team(tmp_path)
    provider = FakeProvider([
        _final(f"Draft done. @{beta.handle} can you review it?"),
        _final("Reviewed — looks good, shipping."),
    ])
    async with _serve(provider, tmp_path) as base:
        events = await _run_turn(base, sess.id, "draft the doc")

    kinds = [k for k, _ in events]
    # Beta's pass was queued and chained as its own turn.
    assert "user_injected" in kinds, kinds
    injected = next(d for k, d in events if k == "user_injected")
    assert injected["text"] == f"<!-- nx:team -->@{beta.handle}"
    # Two turns → two dones, settle only after both; the injection sits
    # between the first turn's done and the second turn's done.
    assert kinds.count("done") == 2, kinds
    first_done = kinds.index("done")
    last_done = len(kinds) - 1 - kinds[::-1].index("done")
    assert first_done < kinds.index("user_injected") < last_done
    assert kinds[-1] == "turn_settled"

    # Both replies persisted, in order, with the marker message between.
    history = store.get(sess.id).history
    texts = [m.content for m in history if getattr(m.role, "value", "") == "assistant" and m.content]
    assert texts[0].startswith("Draft done.")
    assert texts[1].startswith("Reviewed")
    assert any(
        getattr(m.role, "value", "") == "user" and (m.content or "").startswith("<!-- nx:team -->")
        for m in history
    )


async def test_round_fanout_is_chain_only(tmp_path: Path) -> None:
    """Round mode: the lead's turn must not swallow the fan-out markers
    (mid-turn injection) — each member answers in its own turn."""
    store, ps, alpha, beta, sess = await _setup_team(tmp_path, mode="round")
    provider = FakeProvider([
        _final("lead answer"),          # lead's turn (no tools → no boundary)
        _final("beta answer"),
    ])
    async with _serve(provider, tmp_path) as base:
        events = await _run_turn(base, sess.id, "everyone report")

    kinds = [k for k, _ in events]
    assert kinds.count("done") == 2, kinds
    injected = [d for k, d in events if k == "user_injected"]
    assert [d["text"] for d in injected] == [f"<!-- nx:team -->@{beta.handle}"]

    # The lead's reply and beta's reply are distinct assistant messages.
    history = store.get(sess.id).history
    texts = [m.content for m in history if getattr(m.role, "value", "") == "assistant" and m.content]
    assert texts == ["lead answer", "beta answer"]


async def test_cascade_cap_and_continue(tmp_path: Path, monkeypatch) -> None:
    store, ps, alpha, beta, sess = await _setup_team(tmp_path)
    # Budget 1: the FIRST cascade pass is allowed, the second mention hits 0.
    # Patched at load_cached level (swapping CONFIG_PATH for a default config
    # would boot the OCR llama-server inside the test harness).
    import nexus.config_file as cf

    real_load_cached = cf.load_cached

    def _budget_one():
        cfg = real_load_cached().model_copy(deep=True)
        cfg.agent.bot_cascade_max = 1
        return cfg

    monkeypatch.setattr(cf, "load_cached", _budget_one)

    provider = FakeProvider([
        _final(f"@{beta.handle} start"),
        _final(f"@{alpha.handle} back to you"),
        _final("resumed after continue"),
    ])
    async with _serve(provider, tmp_path) as base:
        events = await _run_turn(base, sess.id, "go")

        kinds = [k for k, _ in events]
        # Budget 1 → one chained pass, then the cap event, then settle.
        assert "team_cascade_capped" in kinds, kinds
        capped = next(d for k, d in events if k == "team_cascade_capped")
        assert capped["max"] == 1

        # Runner settled → continue relaunches from the last reply's mention
        # (@alpha) on a fresh runner with a fresh budget.
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{base}/sessions/{sess.id}/team/continue")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("continued") is True

            # Wait for the relaunched turn to settle.
            for _ in range(100):
                active = await client.get(f"{base}/chat/{sess.id}/turn/active")
                if active.status_code == 200 and not active.json().get("running"):
                    break
                await asyncio.sleep(0.05)

    history = store.get(sess.id).history
    texts = [m.content for m in history if getattr(m.role, "value", "") == "assistant" and m.content]
    # Third reply (post-continue) landed.
    assert "resumed after continue" in texts
