"""agent_delegate — external coding-agent CLI delegation.

Covers argv building, dangerous-flag HITL gating, timeout kill, output
spilling to ``vault://.tool-cache/``, and the never-raises error contract.
Config is stubbed per-test via monkeypatching ``_delegate_cfg`` so nothing
touches the developer's real ``~/.nexus/config.toml``.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from nexus.config_schema import DelegateAgentConfig, DelegateConfig
from nexus.server.job_tracker import JobTracker
from nexus.tools import agent_delegate as ad


def _cfg(**overrides: object) -> DelegateConfig:
    agents = overrides.pop("agents", None) or {
        "echo": DelegateAgentConfig(
            command=["python", "-c", "import sys; print('delegated:', sys.argv[1])", "{task}"],
        ),
        "claude": DelegateAgentConfig(
            command=["claude", "-p", "{task}", "--output-format", "json"],
            default_model="claude-sonnet-4-5",
        ),
        "sleeper": DelegateAgentConfig(command=["sleep", "30"]),
        "spiller": DelegateAgentConfig(command=["python", "-c", "print('x' * 500)"]),
    }
    # These tests cover the sync (blocking) path; async-by-default is
    # exercised separately below.
    overrides.setdefault("default_wait", True)
    return DelegateConfig(agents=agents, **overrides)


@pytest.fixture(autouse=True)
def _stub_cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setattr(ad, "_delegate_cfg", _cfg)
    monkeypatch.setattr(ad, "vault_tool_cache", lambda: tmp_path)


async def test_missing_agent_and_unknown_agent() -> None:
    assert json.loads(await ad.handle_agent_delegate({"task": "x"}, None))["error"]
    result = json.loads(await ad.handle_agent_delegate({"agent": "nope", "task": "x"}, None))
    assert result["ok"] is False
    assert "echo" in result["available"]


async def test_empty_task() -> None:
    result = json.loads(await ad.handle_agent_delegate({"agent": "echo", "task": "  "}, None))
    assert result["ok"] is False
    assert "task" in result["error"]


async def test_success_run_inline_output() -> None:
    result = json.loads(
        await ad.handle_agent_delegate({"agent": "echo", "task": "hello world"}, None)
    )
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert "delegated: hello world" in result["output"]
    assert result["output_truncated"] is False


async def test_missing_cwd() -> None:
    result = json.loads(
        await ad.handle_agent_delegate({"agent": "echo", "task": "x", "cwd": "/no/such/dir"}, None)
    )
    assert result["ok"] is False
    assert "cwd" in result["error"]


async def test_timeout_kills_process() -> None:
    result = json.loads(
        await ad.handle_agent_delegate({"agent": "sleeper", "task": "x", "timeout_s": 1}, None)
    )
    assert result["ok"] is False
    assert "timed out" in result["error"]


async def test_model_placeholder_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(
        agents={
            "needsmodel": DelegateAgentConfig(command=["x-cli", "{task}", "--model", "{model}"]),
        }
    )
    monkeypatch.setattr(ad, "_delegate_cfg", lambda: cfg)
    # No model arg and no default_model → refuse instead of launching.
    result = json.loads(await ad.handle_agent_delegate({"agent": "needsmodel", "task": "x"}, None))
    assert result["ok"] is False
    assert "model" in result["error"]


async def test_launch_failure_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(
        agents={
            "ghost": DelegateAgentConfig(command=["definitely-not-a-real-cli-xyz", "{task}"]),
        }
    )
    monkeypatch.setattr(ad, "_delegate_cfg", lambda: cfg)
    result = json.loads(await ad.handle_agent_delegate({"agent": "ghost", "task": "x"}, None))
    assert result["ok"] is False
    assert "failed to launch" in result["error"]


def test_flag_hits() -> None:
    assert ad._flag_hits(["codex", "exec", "--full-auto"], ["--full-auto", "--yolo"]) == [
        "--full-auto"
    ]
    assert ad._flag_hits(["codex", "exec"], ["--yolo"]) == []
    assert ad._flag_hits(
        ["claude", "--dangerously-skip-permissions=1"], ["--dangerously-skip-permissions"]
    ) == ["--dangerously-skip-permissions"]


async def test_dangerous_flag_requires_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bypass flags live in the configured template (the task is injected as a
    # single argv element, so a flag hidden inside the task text never counts).
    cfg = _cfg(
        agents={
            "codex": DelegateAgentConfig(
                command=["python", "-c", "print('ran')", "--full-auto", "{task}"]
            )
        },
        dangerous_flags=["--full-auto"],
    )
    monkeypatch.setattr(ad, "_delegate_cfg", lambda: cfg)

    # No ask_user handler wired → refuse.
    result = json.loads(await ad.handle_agent_delegate({"agent": "codex", "task": "x"}, None))
    assert result["ok"] is False
    assert "handler" in result["error"]

    # User declines → refuse, subprocess never runs.
    refusing = AsyncMock()
    refusing.invoke.return_value = type("R", (), {"answer": "no"})()
    result = json.loads(await ad.handle_agent_delegate({"agent": "codex", "task": "x"}, refusing))
    assert result["ok"] is False
    assert "refused" in result["error"]

    # User approves → runs (the template borrows the codex *name* only for
    # the flag scan; the actual command is python).
    approving = AsyncMock()
    approving.invoke.return_value = type("R", (), {"answer": "yes"})()
    result = json.loads(await ad.handle_agent_delegate({"agent": "codex", "task": "x"}, approving))
    assert result["ok"] is True
    assert "ran" in result["output"]
    assert approving.invoke.await_count == 1


async def test_large_output_spills_to_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    cfg.max_inline_chars = 100
    monkeypatch.setattr(ad, "_delegate_cfg", lambda: cfg)

    result = json.loads(await ad.handle_agent_delegate({"agent": "spiller", "task": "x"}, None))
    assert result["output_truncated"] is True
    assert result["full_output_ref"].startswith("vault://.tool-cache/delegate-spiller-")
    assert len(result["output"]) < 2500


def test_build_argv_placeholders() -> None:
    argv = ad._build_argv(["claude", "-p", "{task}", "--model", "{model}"], "do it", None, "sonnet")
    assert argv == ["claude", "-p", "do it", "--model", "sonnet"]
    # Missing model with {model} placeholder → empty argv (misconfiguration).
    assert ad._build_argv(["claude", "{model}"], "t", None, "") == []


# ── async mode (wait=false) ──────────────────────────────────────────────────


async def _poll_status(
    job_id: str, tracker: JobTracker, want_running: bool = True
) -> dict:
    """Poll delegate_status until the job leaves `running` (bounded).

    Right after a kill the job is already gone from the tracker but its
    result has not landed in the results window yet — treat the transient
    "unknown job_id" error as still-running and keep polling.
    """
    status: dict = {}
    for _ in range(200):
        status = json.loads(await ad.handle_delegate_status({"job_id": job_id}, tracker))
        pending = status.get("status") == "running" or (
            status.get("ok") is False and "error" in status
        )
        if not (want_running and pending):
            return status
        await asyncio.sleep(0.05)
    return status


async def test_async_returns_immediately_then_completes() -> None:
    tracker = JobTracker()
    result = json.loads(
        await ad.handle_agent_delegate(
            {"agent": "echo", "task": "hello async", "wait": False}, None, job_tracker=tracker
        )
    )
    assert result["ok"] is True
    assert result["status"] == "started"
    assert result["wait"] is False
    assert result["job_id"] and result["log_ref"].startswith("vault://.tool-cache/delegate-echo-")

    status = await _poll_status(result["job_id"], tracker)
    assert status["status"] == "done"
    assert status["exit_code"] == 0
    assert "delegated: hello async" in status["output"]

    # The live log was appended and carries the result footer.
    log_path = ad._log_path_from_ref(status["log_ref"])
    assert log_path is not None and log_path.exists()
    log_text = log_path.read_text()
    assert "delegated: hello async" in log_text
    assert "## result" in log_text and "status: done" in log_text

    # The job left the tracker (done) but stays in the results window.
    assert tracker.get(result["job_id"]) is None


async def test_async_default_wait_false_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(default_wait=False)  # explicit: async even without wait arg
    monkeypatch.setattr(ad, "_delegate_cfg", lambda: cfg)
    tracker = JobTracker()
    result = json.loads(
        await ad.handle_agent_delegate({"agent": "echo", "task": "x"}, None, job_tracker=tracker)
    )
    assert result["status"] == "started"
    await _poll_status(result["job_id"], tracker)


async def test_async_job_is_killable() -> None:
    tracker = JobTracker()
    result = json.loads(
        await ad.handle_agent_delegate(
            {"agent": "sleeper", "task": "x", "wait": False}, None, job_tracker=tracker
        )
    )
    job_id = result["job_id"]

    # Registered in the tracker with a live-log pointer.
    job = tracker.get(job_id)
    assert job is not None and job.type == "agent_delegate"
    assert job.extra["log_ref"] == result["log_ref"]

    status = json.loads(await ad.handle_delegate_status({"job_id": job_id}, tracker))
    assert status["status"] == "running"
    assert status["elapsed_s"] >= 0

    killed = await tracker.kill(job_id)
    assert killed is True
    status = await _poll_status(job_id, tracker)
    assert status["status"] == "killed"
    assert status["ok"] is False


async def test_async_watchdog_kills_overrun(monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = JobTracker()
    result = json.loads(
        await ad.handle_agent_delegate(
            {"agent": "sleeper", "task": "x", "wait": False, "timeout_s": 1},
            None,
            job_tracker=tracker,
        )
    )
    status = await _poll_status(result["job_id"], tracker)
    assert status["status"] == "timed_out"
    assert status["timed_out"] is True
    assert "exceeded" in status["error"]


async def test_delegate_status_lists_recent_jobs() -> None:
    tracker = JobTracker()
    first = json.loads(
        await ad.handle_agent_delegate(
            {"agent": "echo", "task": "one", "wait": False}, None, job_tracker=tracker
        )
    )
    await _poll_status(first["job_id"], tracker)
    listing = json.loads(await ad.handle_delegate_status({}, tracker))
    ids = [j["job_id"] for j in listing["recent"]]
    assert first["job_id"] in ids
    assert any(
        r["status"] == "done" and r["agent"] == "echo" for r in listing["recent"]
    )


async def test_delegate_status_unknown_job() -> None:
    result = json.loads(await ad.handle_delegate_status({"job_id": "nope"}, None))
    assert result["ok"] is False
    assert "unknown job_id" in result["error"]


def test_save_roundtrip_preserves_delegate_section(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: save() used to drop [delegate] entirely, silently wiping
    the user's configured agents on every settings save (and unregistering
    agent_delegate on the next restart)."""
    import tomllib

    from nexus import config_file as cf

    cfg = cf.load()
    cfg.delegate.agents = {
        "opencode": DelegateAgentConfig(command=["opencode", "run", "{task}", "--auto"]),
    }
    cfg.delegate.dangerous_flags = ["--auto"]
    cfg.delegate.max_run_seconds = 600

    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(cf, "CONFIG_PATH", config_path)
    cf.save(cfg)

    raw = tomllib.loads(config_path.read_text())
    assert "delegate" in raw
    assert raw["delegate"]["agents"]["opencode"]["command"] == ["opencode", "run", "{task}", "--auto"]
    assert raw["delegate"]["dangerous_flags"] == ["--auto"]
    assert raw["delegate"]["max_run_seconds"] == 600
    # Round-trips through _parse without losing anything.
    reparsed = cf._parse(raw)
    assert list(reparsed.delegate.agents) == ["opencode"]
    assert reparsed.delegate.dangerous_flags == ["--auto"]
    assert reparsed.delegate.max_run_seconds == 600


async def test_async_launch_failure_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(
        agents={
            "ghost": DelegateAgentConfig(command=["definitely-not-a-real-cli-xyz", "{task}"]),
        },
        default_wait=False,
    )
    monkeypatch.setattr(ad, "_delegate_cfg", lambda: cfg)
    tracker = JobTracker()
    result = json.loads(
        await ad.handle_agent_delegate({"agent": "ghost", "task": "x"}, None, job_tracker=tracker)
    )
    status = await _poll_status(result["job_id"], tracker)
    assert status["status"] == "failed"
    assert "failed to launch" in status["error"]


async def test_async_dangerous_flag_still_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg(
        agents={
            "codex": DelegateAgentConfig(
                command=["python", "-c", "print('ran')", "--full-auto", "{task}"]
            )
        },
        dangerous_flags=["--full-auto"],
        default_wait=False,
    )
    monkeypatch.setattr(ad, "_delegate_cfg", lambda: cfg)
    # No ask_user wired → refuse before any job is created.
    result = json.loads(await ad.handle_agent_delegate({"agent": "codex", "task": "x"}, None))
    assert result["ok"] is False
    assert "handler" in result["error"]

    approving = AsyncMock()
    approving.invoke.return_value = type("R", (), {"answer": "yes"})()
    tracker = JobTracker()
    result = json.loads(
        await ad.handle_agent_delegate(
            {"agent": "codex", "task": "x"}, approving, job_tracker=tracker
        )
    )
    assert result["status"] == "started"
    status = await _poll_status(result["job_id"], tracker)
    assert status["status"] == "done"
    assert "ran" in status["output"]
