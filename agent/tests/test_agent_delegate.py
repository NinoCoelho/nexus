"""agent_delegate — external coding-agent CLI delegation.

Covers argv building, dangerous-flag HITL gating, timeout kill, output
spilling to ``vault://.tool-cache/``, and the never-raises error contract.
Config is stubbed per-test via monkeypatching ``_delegate_cfg`` so nothing
touches the developer's real ``~/.nexus/config.toml``.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from nexus.config_schema import DelegateAgentConfig, DelegateConfig
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
