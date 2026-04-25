"""Tests for the cycle + depth guard used by the lane-change hook.

These exercise DISPATCH_CHAIN propagation directly. The full server-wired
hook is harder to drive without a real Agent + SessionStore, so we
re-implement just the guard logic from app.py here and verify its decisions.
"""

from __future__ import annotations

from nexus.agent.context import DISPATCH_CHAIN

MAX_DEPTH = 5


def _should_dispatch(card_id: str, prompt: str | None) -> str:
    """Mirror of the guard from app._lane_change_hook (decision-only)."""
    if not prompt:
        return "no-prompt"
    chain = DISPATCH_CHAIN.get()
    if card_id in chain:
        return "cycle"
    if len(chain) >= MAX_DEPTH:
        return "too-deep"
    return "ok"


def test_empty_chain_dispatches():
    assert _should_dispatch("c1", "Summarise") == "ok"


def test_no_prompt_means_skip():
    assert _should_dispatch("c1", None) == "no-prompt"
    assert _should_dispatch("c1", "") == "no-prompt"


def test_cycle_detected_when_card_already_in_chain():
    token = DISPATCH_CHAIN.set(("c1", "c2"))
    try:
        assert _should_dispatch("c1", "Summarise") == "cycle"
        assert _should_dispatch("c3", "Summarise") == "ok"
    finally:
        DISPATCH_CHAIN.reset(token)


def test_depth_limit_caps_cascades():
    token = DISPATCH_CHAIN.set(tuple(f"c{i}" for i in range(MAX_DEPTH)))
    try:
        assert _should_dispatch("new", "Summarise") == "too-deep"
    finally:
        DISPATCH_CHAIN.reset(token)


def test_just_under_limit_still_dispatches():
    token = DISPATCH_CHAIN.set(tuple(f"c{i}" for i in range(MAX_DEPTH - 1)))
    try:
        assert _should_dispatch("new", "Summarise") == "ok"
    finally:
        DISPATCH_CHAIN.reset(token)
