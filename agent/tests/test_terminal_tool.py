"""Tests for TerminalHandler — shell execution gated on ``ask_user``
approval.

We exercise three layers:

* Guardrails — bad args, bad cwd, approval denied, timeout kill.
* Happy path — real subprocess executes and stdout/stderr flow back.
* Composition — skipping approval, YOLO auto-approve,
  ``require_approval=False``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.agent.ask_user_tool import AskUserHandler
from nexus.agent.context import CURRENT_SESSION_ID
from nexus.agent.terminal_tool import (
    TERMINAL_TOOL,
    TerminalHandler,
    TerminalResult,
)
from nexus.server.session_store import SessionStore


# ── helpers ──────────────────────────────────────────────────────────


class _ScriptedAskHandler:
    """Mimics the AskUserHandler surface the TerminalHandler depends on,
    with a scripted answer. Tests that need to inspect the args the
    terminal passed can read ``.last_args``."""

    def __init__(
        self,
        answer: str,
        *,
        ok: bool = True,
        timed_out: bool = False,
    ) -> None:
        self._answer = answer
        self._ok = ok
        self._timed_out = timed_out
        self.last_args: dict | None = None
        self.invocations = 0

    async def invoke(self, args):  # signature-compatible with AskUserHandler
        from nexus.agent.ask_user_tool import AskUserResult

        self.invocations += 1
        self.last_args = dict(args)
        return AskUserResult(
            ok=self._ok,
            answer=self._answer if self._ok else None,
            kind=args.get("kind", "confirm"),
            timed_out=self._timed_out,
            error=None if self._ok else "scripted failure",
        )


def _handler(ask) -> TerminalHandler:
    return TerminalHandler(ask_user_handler=ask)


# ── tool spec regression ────────────────────────────────────────────


def test_tool_spec_shape() -> None:
    """Guard against accidental rename / schema drift that would
    break every deployed agent without a code diff on the call sites."""
    assert TERMINAL_TOOL.name == "terminal"
    props = TERMINAL_TOOL.parameters["properties"]
    assert set(props.keys()) == {"command", "cwd", "timeout_seconds"}
    assert TERMINAL_TOOL.parameters["required"] == ["command"]


# ── approval flow ───────────────────────────────────────────────────


async def test_denial_does_not_execute() -> None:
    ask = _ScriptedAskHandler("no")
    h = _handler(ask)
    r = await h.invoke({"command": "echo hi"})
    assert not r.ok
    assert r.denied is True
    assert r.stdout == "" and r.stderr == ""
    assert ask.invocations == 1


async def test_timeout_on_approval_counts_as_denial() -> None:
    """User walked away. We don't execute — surfacing timeout vs
    explicit-deny matters because the agent may retry later for
    timeout but should NOT retry for deny."""
    ask = _ScriptedAskHandler("__timeout__", timed_out=True)
    h = _handler(ask)
    r = await h.invoke({"command": "echo hi"})
    assert not r.ok
    assert r.denied is True
    assert r.timed_out is True


async def test_approval_flow_error_is_surfaced() -> None:
    """ask_user itself errored (e.g. no session context). We return
    the underlying reason rather than pretending the command ran."""
    ask = _ScriptedAskHandler("nope", ok=False)
    h = _handler(ask)
    r = await h.invoke({"command": "ls"})
    assert not r.ok
    assert r.denied is False  # didn't even reach the deny path
    assert r.error is not None and "approval flow" in r.error


async def test_require_approval_false_skips_ask() -> None:
    """Skills that already confirmed the action shouldn't prompt
    twice. The agent is trusted to set this correctly."""
    ask = _ScriptedAskHandler("no")  # would deny if asked
    h = _handler(ask)
    r = await h.invoke({"command": "echo skip", "require_approval": False})
    assert ask.invocations == 0
    assert r.ok
    assert "skip" in r.stdout


async def test_approval_prompt_contains_command_and_cwd(tmp_path: Path) -> None:
    """The user has to see the exact command + directory in the
    dialog to make an informed decision. This test locks that
    contract down."""
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    await h.invoke({"command": "ls -la", "cwd": str(tmp_path)})
    prompt = ask.last_args["prompt"]
    assert "ls -la" in prompt
    assert str(tmp_path) in prompt
    assert ask.last_args["kind"] == "confirm"


# ── happy path execution ────────────────────────────────────────────


async def test_echo_roundtrip() -> None:
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    r = await h.invoke({"command": "echo hello"})
    assert r.ok and r.exit_code == 0
    assert r.stdout.strip() == "hello"
    assert r.stderr == ""
    assert r.duration_ms >= 0


async def test_non_zero_exit_is_not_ok() -> None:
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    # `false` is a POSIX builtin that always exits 1.
    r = await h.invoke({"command": "false"})
    assert not r.ok
    assert r.exit_code == 1
    assert r.denied is False  # not a user denial


async def test_cwd_is_honored(tmp_path: Path) -> None:
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    # Create a marker file so we can prove the process ran there.
    (tmp_path / "marker.txt").write_text("ok")
    r = await h.invoke({"command": "ls marker.txt", "cwd": str(tmp_path)})
    assert r.ok
    assert "marker.txt" in r.stdout


async def test_stderr_captured_separately() -> None:
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    # Print to stderr via shell redirection.
    r = await h.invoke({"command": "echo oops 1>&2"})
    assert r.ok
    assert r.stdout == ""
    assert "oops" in r.stderr


async def test_stream_truncated_for_large_output() -> None:
    """Keeps the tool envelope bounded — mirrors http_call's 8000-char
    budget, split across two streams."""
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    # Emit ~5KB to stdout; truncation kicks in at 4000 chars.
    r = await h.invoke({"command": "python3 -c \"print('x' * 5000)\""})
    assert r.ok
    assert r.stdout_truncated is True
    assert len(r.stdout) == 4000


async def test_timeout_kills_long_running_process() -> None:
    """10 second sleep with a 1s timeout — the process gets killed
    and the result flags ``timed_out``. Guards against the agent
    hanging the whole turn on a runaway shell."""
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    r = await h.invoke({"command": "sleep 10", "timeout_seconds": 1})
    assert not r.ok
    assert r.timed_out is True
    # duration should be roughly the timeout, not 10s
    assert r.duration_ms < 3000


# ── input validation ────────────────────────────────────────────────


async def test_empty_command_rejected() -> None:
    h = _handler(_ScriptedAskHandler("yes"))
    r = await h.invoke({"command": "  "})
    assert not r.ok and r.error is not None


async def test_non_string_command_rejected() -> None:
    h = _handler(_ScriptedAskHandler("yes"))
    r = await h.invoke({"command": 42})
    assert not r.ok


async def test_bad_cwd_rejected_before_approval() -> None:
    """Don't bother the user with a dialog for a doomed command."""
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    r = await h.invoke({"command": "ls", "cwd": "/nowhere/does/not/exist"})
    assert not r.ok and r.error is not None
    assert ask.invocations == 0


async def test_invalid_timeout_rejected() -> None:
    h = _handler(_ScriptedAskHandler("yes"))
    r = await h.invoke({"command": "ls", "timeout_seconds": -5})
    assert not r.ok


async def test_timeout_capped_at_ceiling() -> None:
    """10-minute hard cap so a misbehaving agent can't schedule a
    12-hour sleep and tie up resources. The cap only matters when the
    command actually hits it; we just prove the request isn't rejected
    outright when over the cap."""
    ask = _ScriptedAskHandler("yes")
    h = _handler(ask)
    r = await h.invoke({"command": "echo quick", "timeout_seconds": 3600})
    assert r.ok


# ── YOLO composition ────────────────────────────────────────────────


async def test_yolo_auto_approves_terminal(tmp_path: Path) -> None:
    """YOLO is wired through the AskUserHandler, not the terminal. If
    YOLO is on, ask_user returns 'yes' instantly; the command runs
    without a dialog. Proves the composition works."""
    sessions = SessionStore(db_path=tmp_path / "sessions.sqlite")
    session = sessions.create()
    token = CURRENT_SESSION_ID.set(session.id)
    try:
        ask = AskUserHandler(
            session_store=sessions, yolo_mode_getter=lambda: True
        )
        h = TerminalHandler(ask_user_handler=ask)
        r = await h.invoke({"command": "echo yolo"})
        assert r.ok
        assert r.stdout.strip() == "yolo"
    finally:
        CURRENT_SESSION_ID.reset(token)


# ── serialization ───────────────────────────────────────────────────


def test_to_text_is_json() -> None:
    r = TerminalResult(
        ok=True,
        exit_code=0,
        stdout="hi",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=42,
        timed_out=False,
        denied=False,
    )
    parsed = json.loads(r.to_text())
    assert parsed["ok"] is True and parsed["stdout"] == "hi"
    assert parsed["exit_code"] == 0
