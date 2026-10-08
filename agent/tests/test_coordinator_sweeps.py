"""Sweep digest log + the sweep's new ephemeral shape.

Sweeps used to append their whole transcript — seed prompt, every tool call,
every tool result, digest — into the one permanent master session, which the
next sweep then re-sent. Cost grew with the number of sweeps ever run. They now
run on a fresh context and keep their only durable record in a vault markdown
log, which also supplies the "what did I already report" seed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nexus import coordinator_sweeps as cs
from nexus.server.services.background_turn import TurnResult


@pytest.fixture(autouse=True)
def _isolate_log(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "vault_coordinator", lambda: tmp_path / ".coordinator")


def test_round_trip() -> None:
    assert cs.load_last_digest() is None
    path = cs.append_digest("Project A: two new chats.", tokens_in=1234, tokens_out=56)
    assert path is not None
    assert cs.load_last_digest() == "Project A: two new chats."


def test_newest_entry_wins() -> None:
    cs.append_digest("older", when=datetime(2026, 1, 1, tzinfo=timezone.utc))
    cs.append_digest("newer", when=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert cs.load_last_digest() == "newer"


def test_entry_records_cost() -> None:
    cs.append_digest("body", tokens_in=9876, tokens_out=120, iterations=4, model="m1")
    text = (cs.vault_coordinator() / "sweeps.md").read_text(encoding="utf-8")
    assert "9,876 in / 120 out" in text
    assert "4 iteration(s)" in text
    assert "`m1`" in text
    # The cost line must not be mistaken for the digest body on read-back.
    assert cs.load_last_digest() == "body"


def test_multiline_digest_survives() -> None:
    body = "- one line\n- another line\n\n**Needs you:** a decision"
    cs.append_digest(body)
    assert cs.load_last_digest() == body


def test_empty_digest_is_not_recorded() -> None:
    assert cs.append_digest("") is None
    assert cs.append_digest("   \n ") is None
    assert cs.load_last_digest() is None


def test_log_is_trimmed() -> None:
    for i in range(cs.MAX_ENTRIES + 10):
        cs.append_digest(
            f"entry {i}", when=datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc)
        )
    text = (cs.vault_coordinator() / "sweeps.md").read_text(encoding="utf-8")
    assert text.count("\n## ") == cs.MAX_ENTRIES
    # Newest is kept, oldest is dropped.
    assert "entry 59" in text
    assert "entry 0\n" not in text


def test_failure_is_swallowed(monkeypatch) -> None:
    def _boom():
        raise OSError("no disk")

    monkeypatch.setattr(cs, "vault_coordinator", _boom)
    assert cs.append_digest("x") is None
    assert cs.load_last_digest() is None


# ── seed construction ──────────────────────────────────────────────────────


def test_seed_without_previous_digest(monkeypatch) -> None:
    from nexus.heartbeat_drivers.coordinator_sweep import driver

    monkeypatch.setattr(
        "nexus.coordinator_sweeps.load_last_digest", lambda: None
    )
    seed = driver._build_seed()
    assert "SWEEP (read-only)" in seed
    assert "Previous digest" not in seed


def test_seed_carries_previous_digest(monkeypatch) -> None:
    from nexus.heartbeat_drivers.coordinator_sweep import driver

    monkeypatch.setattr(
        "nexus.coordinator_sweeps.load_last_digest", lambda: "already told them X"
    )
    seed = driver._build_seed()
    assert "already told them X" in seed
    assert "do not repeat it" in seed


def test_seed_survives_a_broken_log(monkeypatch) -> None:
    from nexus.heartbeat_drivers.coordinator_sweep import driver

    def _boom():
        raise RuntimeError("log unreadable")

    monkeypatch.setattr("nexus.coordinator_sweeps.load_last_digest", _boom)
    assert "SWEEP (read-only)" in driver._build_seed()


# ── digest extraction ──────────────────────────────────────────────────────


class _Msg:
    def __init__(self, role: str, content):
        self.role = role
        self.content = content


def test_final_reply_prefers_the_last_assistant_message() -> None:
    """``accumulated_text`` concatenates deltas from every iteration, so on a
    multi-step turn it leads with intermediate narration."""
    result = TurnResult(
        status="done",
        accumulated_text="Let me check the projects...NOTHING_NEW",
        final_messages=[
            _Msg("user", "sweep"),
            _Msg("assistant", "Let me check the projects..."),
            _Msg("tool", '{"ok": true}'),
            _Msg("assistant", "NOTHING_NEW"),
        ],
    )
    assert result.final_reply() == "NOTHING_NEW"


def test_final_reply_falls_back_to_streamed_text() -> None:
    result = TurnResult(status="done", accumulated_text="digest body")
    assert result.final_reply() == "digest body"


def test_final_reply_skips_empty_and_multipart_assistant_turns() -> None:
    result = TurnResult(
        status="done",
        accumulated_text="fallback",
        final_messages=[
            _Msg("assistant", "real digest"),
            _Msg("assistant", ""),
            _Msg("assistant", None),
        ],
    )
    assert result.final_reply() == "real digest"
