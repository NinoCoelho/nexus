"""agent_delegate — hand a task to an external coding agent CLI (headless).

Wraps configured headless CLIs (``claude``, ``codex``, ``opencode``, …) as a
single tool call. Config lives in ``[delegate.agents.<name>]`` of
``~/.nexus/config.toml``::

    [delegate.agents.claude]
    command = ["claude", "-p", "{task}", "--output-format", "json"]
    env = { ANTHROPIC_API_KEY = "$ANTHROPIC_API_KEY" }
    timeout_s = 900
    default_model = "claude-sonnet-4-5"

``{task}`` and ``{model}`` placeholders are replaced at dispatch. ``$NAME``
values in ``env`` resolve through :func:`nexus.secrets.resolve` (env first,
store fallback) at the subprocess boundary — values never enter tool args,
results, or transcripts.

Output discipline mirrors the microcompaction contract: transcripts larger
than ``[delegate].max_inline_chars`` are written to
``vault/.tool-cache/delegate-<agent>-<ts>.md`` and the tool result carries a
short excerpt plus a ``vault://.tool-cache/...`` pointer instead of the full
text.

Safety: when the built command contains a bypass-permissions flag
(``[delegate].dangerous_flags``), the invocation goes through HITL
``ask_user`` confirm first — YOLO mode auto-approves confirms, so unattended
setups keep working while attended ones see an approval card.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import threading
import time
from pathlib import Path
from typing import Any

from ..agent.llm import ToolSpec
from ..home import vault_tool_cache
from ..secrets_substitute import resolve as resolve_secrets

AGENT_DELEGATE_TOOL = ToolSpec(
    name="agent_delegate",
    description=(
        "Delegate a self-contained task to an external coding agent CLI "
        "(e.g. claude, codex, opencode) running headlessly in a directory. "
        "Use for long-running, well-specified work in a repo (refactors, "
        "batch edits, spec implementations) or to get a second model's "
        "implementation. Returns the delegate's output; large transcripts "
        "are saved to the vault with a vault:// pointer."
    ),
    parameters={
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": "Configured agent name (see [delegate.agents.*] in config).",
            },
            "task": {
                "type": "string",
                "description": (
                    "Self-contained prompt for the delegate — goal, file paths, "
                    "constraints, and what done looks like. The delegate has no "
                    "access to this conversation."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the run (absolute path). Default: server cwd.",
            },
            "model": {
                "type": "string",
                "description": "Model override replacing {model} in the command template.",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Per-call timeout override in seconds.",
            },
        },
        "required": ["agent", "task"],
    },
)

_APPROVE_ANSWERS = {"yes", "y", "confirm", "approve", "allow", "ok", "true", "1"}

# One delegate process per (agent, cwd) at a time — two agents racing in the
# same tree step on each other. Cross-key global cap bounds total concurrency.
_PER_KEY_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_PER_KEY_GUARD = threading.Lock()
_GLOBAL_SEMAPHORE = asyncio.Semaphore(3)


def _delegate_cfg() -> Any:
    from ..config_file import load as load_config

    return load_config().delegate


def agent_delegate_is_configured() -> bool:
    """True iff at least one delegate agent is configured at build time."""
    return bool(_delegate_cfg().agents)


def _build_argv(
    template: list[str],
    task: str,
    model: str | None,
    default_model: str,
) -> list[str]:
    resolved_model = (model or default_model or "").strip()
    argv: list[str] = []
    for part in template:
        if "{task}" in part:
            part = part.replace("{task}", task)
        if "{model}" in part:
            if not resolved_model:
                return []  # caller reports the misconfiguration
            part = part.replace("{model}", resolved_model)
        argv.append(part)
    return argv


def _flag_hits(argv: list[str], flags: list[str]) -> list[str]:
    hits: list[str] = []
    for part in argv:
        for flag in flags:
            if (part == flag or part.startswith(flag + "=")) and flag not in hits:
                hits.append(flag)
    return hits


def _per_key_lock(agent: str, cwd: str) -> asyncio.Lock:
    key = (agent, cwd)
    with _PER_KEY_GUARD:
        lock = _PER_KEY_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _PER_KEY_LOCKS[key] = lock
        return lock


def _spill_full_output(agent: str, task: str, cwd: str, argv: list[str], output: str) -> str:
    """Write the full transcript to the vault tool cache; return its ref."""
    cache_dir = vault_tool_cache()
    cache_dir.mkdir(parents=True, exist_ok=True)
    fname = f"delegate-{agent}-{int(time.time())}.md"
    body = (
        f"# agent_delegate: {agent}\n\n"
        f"- task: {task[:2000]}\n"
        f"- cwd: `{cwd}`\n"
        f"- command: `{shlex.join(argv)}`\n\n"
        f"```\n{output}\n```\n"
    )
    (cache_dir / fname).write_text(body, encoding="utf-8")
    return f"vault://.tool-cache/{fname}"


def _error(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra})


async def handle_agent_delegate(args: dict, ask_user: Any | None) -> str:
    """Run one configured delegate agent headlessly and return its output.

    Never raises across the agent boundary — every failure surfaces as a
    JSON ``{"ok": false, ...}`` result, matching the ``acp_call`` contract.
    """
    cfg = _delegate_cfg()
    agents = cfg.agents

    agent = (args.get("agent") or "").strip()
    task = (args.get("task") or "").strip()
    if not agent:
        return _error(
            "agent is required",
            available=sorted(agents.keys()),
        )
    entry = agents.get(agent)
    if entry is None:
        return _error(
            f"agent {agent!r} is not configured under [delegate.agents.*]",
            available=sorted(agents.keys()),
        )
    if not entry.command:
        return _error(f"agent {agent!r} has an empty command template")
    if not task:
        return _error("task is required")

    argv = _build_argv(entry.command, task, args.get("model"), entry.default_model)
    if not argv:
        return _error(
            f"agent {agent!r} command needs a model but none was given "
            "(pass `model` or set default_model in config)"
        )

    cwd = (args.get("cwd") or "").strip()
    if cwd:
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_dir():
            return _error(f"cwd does not exist: {cwd}")
        cwd = str(cwd_path)
    else:
        cwd = str(Path.cwd())

    timeout_s = int(args.get("timeout_s") or entry.timeout_s or 900)
    timeout_s = max(1, timeout_s)

    # Bypass-permission flags gate on HITL confirm. YOLO mode auto-approves
    # confirms, so unattended runs keep flowing; attended runs get a card.
    dangerous = _flag_hits(argv, cfg.dangerous_flags)
    if dangerous:
        if ask_user is None:
            return _error(
                "command contains bypass-permission flags but no approval "
                "handler is wired — refusing",
                dangerous_flags=dangerous,
            )
        result = await ask_user.invoke({
            "kind": "confirm",
            "message": (
                f"Delegate to `{agent}` with bypass-permission flags "
                f"{dangerous}? Command: `{shlex.join(argv)}`"
            ),
        })
        answer = str(getattr(result, "answer", "") or "").strip().lower()
        if answer not in _APPROVE_ANSWERS:
            return _error("delegate refused: user did not approve the bypass flags")

    # Inherit the server environment (PATH, HOME, …) and layer the
    # agent-specific $NAME-resolved variables on top.
    env = {**os.environ, **resolve_secrets(entry.env)}

    async with _per_key_lock(agent, cwd), _GLOBAL_SEMAPHORE:
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except (OSError, ValueError) as exc:
            return _error(f"failed to launch {agent!r}: {exc}", agent=agent)

        try:
            out_bytes, err_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:  # asyncio.TimeoutError is an alias in 3.11+
            proc.kill()
            try:
                out_bytes, err_bytes = await asyncio.wait_for(proc.communicate(), timeout=10)
            except Exception:  # noqa: BLE001 — kill-path cleanup, never fatal
                out_bytes, err_bytes = b"", b""
            duration = round(time.monotonic() - started, 1)
            return _error(
                f"delegate {agent!r} timed out after {timeout_s}s and was killed",
                agent=agent,
                duration_s=duration,
            )

    duration = round(time.monotonic() - started, 1)
    output = out_bytes.decode("utf-8", errors="replace").strip()
    stderr_text = err_bytes.decode("utf-8", errors="replace").strip()
    if stderr_text:
        output = f"{output}\n\n[stderr]\n{stderr_text}".strip()

    result: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "agent": agent,
        "exit_code": proc.returncode,
        "duration_s": duration,
    }

    if len(output) > cfg.max_inline_chars:
        ref = _spill_full_output(agent, task, cwd, argv, output)
        excerpt = output[:2000].rstrip()
        result["output"] = (
            f"{excerpt}\n\n[... output truncated inline — full transcript at {ref} ...]"
        )
        result["output_truncated"] = True
        result["full_output_ref"] = ref
    else:
        result["output"] = output
        result["output_truncated"] = False

    return json.dumps(result)
