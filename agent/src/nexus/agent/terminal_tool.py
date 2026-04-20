"""The ``terminal`` tool — run a shell command after the user approves.

Built on top of ``ask_user`` so every shell invocation flows through
the same HITL primitive as every other sensitive action. YOLO mode in
Settings auto-approves (because ``ask_user`` with ``kind=confirm``
already honors YOLO); skills that have already confirmed the action
with the user can pass ``require_approval=False`` to skip a redundant
second prompt.

Security posture:
  * Always asks the user by default — no silent exec.
  * ``asyncio.create_subprocess_shell`` with a required timeout;
    processes that exceed it are terminated, not left orphaned.
  * Output truncation on each stream (stdout/stderr) keeps the
    tool-result envelope bounded the same way http_call does.

Not in scope for MVP: per-command "always allow" memory (the user
approves each call, or flips YOLO for the whole session).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from .ask_user_tool import AskUserHandler
from .llm import ToolSpec

TERMINAL_TOOL = ToolSpec(
    name="terminal",
    description=(
        "Run a shell command on the user's local machine and return its "
        "output. Requires the user to approve each run (or YOLO mode). "
        "Prefer purpose-built tools when they fit — `vault_write` for "
        "notes, `http_call` for remote HTTP. Use `terminal` when the "
        "action needs a local CLI (e.g. `git log`, `jq`, `ls ~/.nexus/vault/`)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Shell command as a single string. Runs through "
                    "the user's default shell so pipes and redirects "
                    "work."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Working directory (absolute or ``~``-prefixed). "
                    "Defaults to the server's current directory. Bad "
                    "paths return a clear error instead of silent "
                    "fallback."
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    "Kill the process if it runs longer than this. "
                    "Default 60. Max 600 (10 minutes) — anything longer "
                    "should be a background job, not a tool call."
                ),
            },
        },
        "required": ["command"],
    },
)

_DEFAULT_TIMEOUT_SECONDS = 60
_MAX_TIMEOUT_SECONDS = 600
_STREAM_CHAR_LIMIT = 4000  # stdout + stderr each, so ~8KB envelope


@dataclass(frozen=True)
class TerminalResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int
    timed_out: bool
    denied: bool
    error: str | None = None

    def to_text(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "exit_code": self.exit_code,
                "stdout": self.stdout,
                "stderr": self.stderr,
                "stdout_truncated": self.stdout_truncated,
                "stderr_truncated": self.stderr_truncated,
                "duration_ms": self.duration_ms,
                "timed_out": self.timed_out,
                "denied": self.denied,
                "error": self.error,
            },
            ensure_ascii=False,
        )


class TerminalHandler:
    """Needs an ``AskUserHandler`` at construction so every shell call
    flows through the approval primitive. Skills that have already
    asked the user elsewhere can set ``require_approval=False`` in the
    tool args to skip a redundant second dialog — the agent is trusted
    to pass that accurately because the cost of a spurious extra
    prompt is cheap."""

    def __init__(
        self,
        *,
        ask_user_handler: AskUserHandler,
        default_timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._ask = ask_user_handler
        self._default_timeout = default_timeout

    async def invoke(self, args: dict[str, Any]) -> TerminalResult:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return _error(
                message="`command` is required and must be a non-empty string",
            )
        command = command.strip()

        cwd_raw = args.get("cwd")
        if cwd_raw is not None and not isinstance(cwd_raw, str):
            return _error(message="`cwd` must be a string if provided")
        cwd = os.path.expanduser(cwd_raw) if cwd_raw else None
        if cwd and not os.path.isdir(cwd):
            return _error(message=f"cwd does not exist: {cwd!r}")

        timeout = args.get("timeout_seconds", self._default_timeout)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            return _error(message="`timeout_seconds` must be a positive number")
        timeout = min(float(timeout), float(_MAX_TIMEOUT_SECONDS))

        require_approval = args.get("require_approval", True)
        if not isinstance(require_approval, bool):
            return _error(message="`require_approval` must be a boolean")

        if require_approval:
            ask = await self._ask.invoke(
                {
                    "prompt": _approval_prompt(command, cwd),
                    "kind": "confirm",
                    # A 5-minute window for the user to react. Shell
                    # commands are the highest-stakes tool we have;
                    # short timeouts here would auto-deny destructive
                    # actions when the user is just slow to look.
                    "timeout_seconds": 300,
                }
            )
            if not ask.ok:
                return _error(
                    message=f"approval flow failed: {ask.error or 'unknown error'}"
                )
            if ask.timed_out or ask.answer != "yes":
                return TerminalResult(
                    ok=False,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    stdout_truncated=False,
                    stderr_truncated=False,
                    duration_ms=0,
                    timed_out=ask.timed_out,
                    denied=True,
                    error=(
                        "user did not approve the command"
                        + (" (timeout)" if ask.timed_out else "")
                    ),
                )

        return await _run_command(command, cwd=cwd, timeout=timeout)


def _approval_prompt(command: str, cwd: str | None) -> str:
    """Compact, unambiguous prompt. Shown in the UI dialog verbatim —
    so we lean on quoted shell + a single extra line for the dir."""
    dir_str = cwd or os.getcwd()
    return (
        "Agent wants to run this shell command:\n\n"
        f"    {command}\n\n"
        f"Working directory: {dir_str}"
    )


async def _run_command(
    command: str, *, cwd: str | None, timeout: float
) -> TerminalResult:
    start = asyncio.get_running_loop().time()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return _error(message=f"failed to launch subprocess: {exc}")

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        timed_out = True
        # Best-effort termination — we don't hang the tool result on
        # the subprocess being gone, since orphaned shells eventually
        # exit. kill() is async-safe and non-blocking.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        stdout_bytes, stderr_bytes = await proc.communicate()

    duration_ms = int((asyncio.get_running_loop().time() - start) * 1000)
    stdout, stdout_truncated = _truncate_stream(stdout_bytes)
    stderr, stderr_truncated = _truncate_stream(stderr_bytes)

    return TerminalResult(
        ok=not timed_out and (proc.returncode == 0),
        exit_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        duration_ms=duration_ms,
        timed_out=timed_out,
        denied=False,
        error=None if not timed_out else "command timed out",
    )


def _truncate_stream(raw: bytes | None) -> tuple[str, bool]:
    if not raw:
        return "", False
    text = raw.decode("utf-8", errors="replace")
    if len(text) > _STREAM_CHAR_LIMIT:
        return text[:_STREAM_CHAR_LIMIT], True
    return text, False


def _error(*, message: str) -> TerminalResult:
    return TerminalResult(
        ok=False,
        exit_code=None,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=0,
        timed_out=False,
        denied=False,
        error=message,
    )
