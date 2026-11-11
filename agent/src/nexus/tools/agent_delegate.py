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

Two execution modes (``wait`` parameter, default from
``[delegate].default_wait``, which is ``false`` — async by default):

* **sync** (``wait: true``) — block the turn until the run finishes and
  return its output. Only sensible for quick queries.
* **async** (``wait: false``) — fire-and-monitor. Returns immediately with
  ``{job_id, log_ref}``; the subprocess runs as a tracked background job
  (visible in the UI's running-tasks indicator, killable via
  ``/jobs/{id}/kill``). Output streams *live* into
  ``vault/.tool-cache/delegate-<agent>-<ts>.md`` so the orchestrating agent
  can follow progress with ``vault_read`` and poll completion with the
  companion ``delegate_status`` tool. A watchdog (``[delegate].
  max_run_seconds``, default 1800) kills the subprocess only when a run
  genuinely overruns — the turn is never blocked.

Output discipline mirrors the microcompaction contract: transcripts larger
than ``[delegate].max_inline_chars`` are written to
``vault/.tool-cache/delegate-<agent>-<ts>.md`` and the result carries a
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
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..agent.llm import ToolSpec
from ..agent.context import CURRENT_SESSION_ID
from ..home import vault_tool_cache
from ..secrets_substitute import resolve as resolve_secrets

AGENT_DELEGATE_TOOL = ToolSpec(
    name="agent_delegate",
    description=(
        "Delegate a self-contained task to an external coding agent CLI "
        "(e.g. claude, codex, opencode) running headlessly in a directory. "
        "Use for long-running, well-specified work in a repo (refactors, "
        "batch edits, spec implementations) or to get a second model's "
        "implementation. By default the call returns immediately with a "
        "job_id and a live log reference — poll `delegate_status` to follow "
        "progress and collect the result."
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
            "wait": {
                "type": "boolean",
                "description": (
                    "true: block until the run finishes and return its output "
                    "(only for quick queries). false (default): start a "
                    "background job and return immediately with job_id + "
                    "log_ref — monitor with delegate_status."
                ),
            },
            "timeout_s": {
                "type": "integer",
                "description": (
                    "Per-call limit in seconds. sync: kills the run and "
                    "reports a timeout. async: watchdog limit (default "
                    "[delegate].max_run_seconds)."
                ),
            },
        },
        "required": ["agent", "task"],
    },
)

DELEGATE_STATUS_TOOL = ToolSpec(
    name="delegate_status",
    description=(
        "Check the status of an async agent_delegate job: running / done / "
        "failed / killed / timed_out, with the exit code, result excerpt, "
        "and the last lines of the live log. Call without job_id to list "
        "recent delegate jobs."
    ),
    parameters={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Job id returned by agent_delegate (wait=false). Omit to list recent jobs.",
            },
            "tail": {
                "type": "integer",
                "description": "How many trailing log lines to return (default 40).",
            },
        },
    },
)

_APPROVE_ANSWERS = {"yes", "y", "confirm", "approve", "allow", "ok", "true", "1"}

# One delegate process per (agent, cwd) at a time — two agents racing in the
# same tree step on each other. Cross-key global cap bounds total concurrency.
_PER_KEY_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_PER_KEY_GUARD = threading.Lock()
_GLOBAL_SEMAPHORE = asyncio.Semaphore(3)

# Finished async-run results, job_id -> result dict (bounded; oldest evicted).
# Keeps delegate_status cheap: it never re-reads the whole log for a verdict.
_RESULTS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_RESULTS_CAP = 50


def _remember_result(job_id: str, result: dict[str, Any]) -> None:
    _RESULTS[job_id] = result
    while len(_RESULTS) > _RESULTS_CAP:
        _RESULTS.popitem(last=False)


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


def _tail_file(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-max(1, lines):])


def _apply_output_cap(cfg: Any, result: dict[str, Any], agent: str, task: str, cwd: str, argv: list[str]) -> None:
    """Apply the microcompaction inline cap to ``result["output"]`` in place."""
    output = result.get("output", "")
    if len(output) > cfg.max_inline_chars:
        ref = _spill_full_output(agent, task, cwd, argv, output)
        excerpt = output[:2000].rstrip()
        result["output"] = (
            f"{excerpt}\n\n[... output truncated inline — full transcript at {ref} ...]"
        )
        result["output_truncated"] = True
        result["full_output_ref"] = ref
    else:
        result["output_truncated"] = False


async def _validate_request(
    args: dict, cfg: Any
) -> tuple[dict[str, Any] | None, str]:
    """Validate args against config. Returns (error_json, "") on failure,
    or (validated, "") where validated carries agent/task/argv/cwd/entry."""
    agents = cfg.agents

    agent = (args.get("agent") or "").strip()
    task = (args.get("task") or "").strip()
    if not agent:
        return (
            _error("agent is required", available=sorted(agents.keys())),
            "",
        )
    entry = agents.get(agent)
    if entry is None:
        return (
            _error(
                f"agent {agent!r} is not configured under [delegate.agents.*]",
                available=sorted(agents.keys()),
            ),
            "",
        )
    if not entry.command:
        return _error(f"agent {agent!r} has an empty command template"), ""
    if not task:
        return _error("task is required"), ""

    argv = _build_argv(entry.command, task, args.get("model"), entry.default_model)
    if not argv:
        return (
            _error(
                f"agent {agent!r} command needs a model but none was given "
                "(pass `model` or set default_model in config)"
            ),
            "",
        )

    cwd = (args.get("cwd") or "").strip()
    if cwd:
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_dir():
            return _error(f"cwd does not exist: {cwd}"), ""
        cwd = str(cwd_path)
    else:
        cwd = str(Path.cwd())

    return None, json.dumps(
        {"agent": agent, "task": task, "argv": argv, "cwd": cwd, "entry_name": agent}
    )


async def _gate_bypass_flags(
    cfg: Any, argv: list[str], agent: str, ask_user: Any | None
) -> str | None:
    """HITL gate for bypass-permission flags. Returns an error JSON when the
    call must stop, else None."""
    dangerous = _flag_hits(argv, cfg.dangerous_flags)
    if not dangerous:
        return None
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
    return None


async def handle_agent_delegate(
    args: dict,
    ask_user: Any | None,
    *,
    job_tracker: Any | None = None,
    publish_fn: Any | None = None,
    session_id: str | None = None,
) -> str:
    """Run one configured delegate agent headlessly.

    Never raises across the agent boundary — every failure surfaces as a
    JSON ``{"ok": false, ...}`` result, matching the ``acp_call`` contract.
    Async mode (default) registers a :class:`~nexus.server.job_tracker.Job`
    and returns ``{job_id, log_ref}`` immediately.
    """
    cfg = _delegate_cfg()

    error, validated_json = await _validate_request(args, cfg)
    if error is not None:
        return error
    validated = json.loads(validated_json)
    agent: str = validated["agent"]
    task: str = validated["task"]
    argv: list[str] = validated["argv"]
    cwd: str = validated["cwd"]
    entry = cfg.agents[agent]

    bypass_error = await _gate_bypass_flags(cfg, argv, agent, ask_user)
    if bypass_error is not None:
        return bypass_error

    # Inherit the server environment (PATH, HOME, …) and layer the
    # agent-specific $NAME-resolved variables on top.
    env = {**os.environ, **resolve_secrets(entry.env)}

    wait_arg = args.get("wait")
    wait = bool(wait_arg) if wait_arg is not None else bool(cfg.default_wait)
    timeout_s = max(1, int(args.get("timeout_s") or entry.timeout_s or 900))

    if session_id is None:
        try:
            session_id = CURRENT_SESSION_ID.get() or None
        except LookupError:
            session_id = None

    if wait:
        return await _run_sync(
            cfg, agent=agent, task=task, argv=argv, cwd=cwd, env=env,
            timeout_s=timeout_s,
        )
    return await _run_async(
        cfg, agent=agent, task=task, argv=argv, cwd=cwd, env=env,
        timeout_s=timeout_s, job_tracker=job_tracker, publish_fn=publish_fn,
        session_id=session_id,
    )


async def _run_sync(
    cfg: Any,
    *,
    agent: str,
    task: str,
    argv: list[str],
    cwd: str,
    env: dict[str, str],
    timeout_s: int,
) -> str:
    """Blocking mode: hold the turn until the subprocess exits (or times out)."""
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
        "output": output,
    }
    _apply_output_cap(cfg, result, agent, task, cwd, argv)
    return json.dumps(result)


async def _run_async(
    cfg: Any,
    *,
    agent: str,
    task: str,
    argv: list[str],
    cwd: str,
    env: dict[str, str],
    timeout_s: int,
    job_tracker: Any | None,
    publish_fn: Any | None,
    session_id: str | None,
) -> str:
    """Fire-and-monitor mode: return immediately, run in a tracked job.

    The job is registered *before* returning so the response can carry its
    id; the kill callback targets the subprocess once it exists (a kill that
    lands before launch aborts the run before any process is created).
    Output streams live into the log file as the subprocess produces it.
    """
    cache_dir = vault_tool_cache()
    cache_dir.mkdir(parents=True, exist_ok=True)
    fname = f"delegate-{agent}-{int(time.time())}.md"
    log_path = cache_dir / fname
    log_path.write_text(
        f"# agent_delegate (async): {agent}\n\n"
        f"- task: {task[:2000]}\n"
        f"- cwd: `{cwd}`\n"
        f"- command: `{shlex.join(argv)}`\n"
        f"- started: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n\n"
        f"## output\n\n",
        encoding="utf-8",
    )
    log_ref = f"vault://.tool-cache/{fname}"

    holder: dict[str, Any] = {"proc": None, "killed": False}

    async def _kill_job() -> None:
        holder["killed"] = True
        proc: Any = holder["proc"]
        if proc is not None and proc.returncode is None:
            proc.kill()

    if job_tracker is not None:
        job_id = job_tracker.start(
            type="agent_delegate",
            label=f"{agent}: {task[:60]}",
            session_id=session_id,
            kill_fn=_kill_job,
            extra={"agent": agent, "log_ref": log_ref},
            publish_fn=publish_fn,
        )
    else:
        job_id = uuid.uuid4().hex[:12]

    lock = _per_key_lock(agent, cwd)

    async def _append_log(text: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()

    async def _pump(stream: Any, chunks: list[str]) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            chunks.append(text)
            await _append_log(text)

    async def _job() -> None:
        started = time.monotonic()
        result: dict[str, Any]
        async with lock, _GLOBAL_SEMAPHORE:
            if holder["killed"]:
                result = {
                    "ok": False, "agent": agent, "task": task[:200],
                    "status": "killed", "exit_code": None, "duration_s": 0.0,
                    "killed": True, "timed_out": False, "output": "",
                    "log_ref": log_ref,
                }
            else:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                        env=env,
                    )
                except (OSError, ValueError) as exc:
                    await _append_log(f"\n[launch failed: {exc}]\n")
                    result = {
                        "ok": False, "agent": agent, "task": task[:200],
                        "status": "failed", "exit_code": None,
                        "duration_s": round(time.monotonic() - started, 1),
                        "error": f"failed to launch {agent!r}: {exc}",
                        "killed": False, "timed_out": False, "output": "",
                        "log_ref": log_ref,
                    }
                else:
                    holder["proc"] = proc
                    out_chunks: list[str] = []
                    err_chunks: list[str] = []
                    pumps = [
                        asyncio.ensure_future(_pump(proc.stdout, out_chunks)),
                        asyncio.ensure_future(_pump(proc.stderr, err_chunks)),
                    ]
                    timed_out = False
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
                    except TimeoutError:
                        timed_out = True
                        holder["killed"] = True
                        proc.kill()
                    await asyncio.gather(*pumps, return_exceptions=True)

                    duration = round(time.monotonic() - started, 1)
                    output = "".join(out_chunks).strip()
                    stderr_text = "".join(err_chunks).strip()
                    if stderr_text:
                        output = f"{output}\n\n[stderr]\n{stderr_text}".strip()

                    if holder["killed"] and not timed_out:
                        status = "killed"
                    elif timed_out:
                        status = "timed_out"
                    elif proc.returncode == 0:
                        status = "done"
                    else:
                        status = "failed"

                    result = {
                        "ok": status == "done",
                        "agent": agent,
                        "task": task[:200],
                        "status": status,
                        "exit_code": proc.returncode,
                        "duration_s": duration,
                        "killed": holder["killed"],
                        "timed_out": timed_out,
                        "output": output,
                        "log_ref": log_ref,
                    }
                    if timed_out:
                        result["error"] = (
                            f"delegate {agent!r} exceeded {timeout_s}s and was killed"
                        )

        _apply_output_cap(cfg, result, agent, task, cwd, argv)
        _remember_result(job_id, result)

        await _append_log(
            f"\n\n## result\n\n- status: {result['status']}\n"
            f"- exit_code: {result['exit_code']}\n"
            f"- duration_s: {result['duration_s']}\n"
        )
        if job_tracker is not None:
            job_tracker.done(job_id, publish_fn=publish_fn)

    asyncio.ensure_future(_job())

    return json.dumps({
        "ok": True,
        "status": "started",
        "wait": False,
        "agent": agent,
        "job_id": job_id,
        "log_ref": log_ref,
        "timeout_s": timeout_s,
        "hint": (
            "poll delegate_status {job_id}; follow progress with "
            "vault_read on log_ref; kill via the jobs API if needed"
        ),
    })


async def handle_delegate_status(
    args: dict,
    job_tracker: Any | None = None,
) -> str:
    """Report the status of one (or all recent) async delegate jobs."""
    job_id = (args.get("job_id") or "").strip()
    tail = max(1, int(args.get("tail") or 40))

    if not job_id:
        recent = [
            {
                "job_id": jid,
                "agent": r.get("agent"),
                "status": r.get("status"),
                "task": r.get("task"),
                "exit_code": r.get("exit_code"),
                "log_ref": r.get("log_ref"),
            }
            for jid, r in reversed(_RESULTS.items())
        ]
        running: list[dict[str, Any]] = []
        if job_tracker is not None:
            running = [
                {
                    "job_id": j["id"],
                    "agent": (j.get("extra") or {}).get("agent"),
                    "status": "running",
                    "label": j.get("label"),
                    "elapsed_s": j.get("elapsed_seconds"),
                    "log_ref": (j.get("extra") or {}).get("log_ref"),
                }
                for j in job_tracker.list_jobs()
                if j.get("type") == "agent_delegate"
            ]
        return json.dumps({"ok": True, "running": running, "recent": recent})

    finished = _RESULTS.get(job_id)
    if finished is not None:
        payload = dict(finished)
        log_path = _log_path_from_ref(payload.get("log_ref", ""))
        payload["log_tail"] = _tail_file(log_path, tail) if log_path else ""
        return json.dumps({"ok": True, **payload})

    if job_tracker is not None:
        job = job_tracker.get(job_id)
        if job is not None and job.type == "agent_delegate":
            extra = job.extra or {}
            log_path = _log_path_from_ref(extra.get("log_ref", ""))
            return json.dumps({
                "ok": True,
                "status": "running",
                "agent": extra.get("agent"),
                "log_ref": extra.get("log_ref"),
                "elapsed_s": round(time.time() - job.started_at, 1),
                "log_tail": _tail_file(log_path, tail) if log_path else "",
            })

    return _error(
        "unknown job_id (not running, not in the recent-results window)",
        known=sorted(_RESULTS.keys()),
    )


def _log_path_from_ref(ref: str) -> Path | None:
    if not ref.startswith("vault://"):
        return None
    # Derive from vault_tool_cache() (not vault_root()) so tests that
    # monkeypatch the cache dir see the same file the run wrote to.
    return vault_tool_cache() / ref.removeprefix("vault://.tool-cache/")
