"""Tests for the manage_triggers / trigger / trigger_info / delete_trigger
agent tools. Uses a fake workflow engine and a temp vault directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from nexus.tools.trigger_tool import (
    handle_delete_trigger,
    handle_trigger,
    handle_trigger_info,
    set_engine_getter,
)

# ── Fake engine ───────────────────────────────────────────────────────


class _FakeRun:
    def __init__(self, run_id: str, path: str):
        self.id = run_id
        self.workflow_path = path
        self.status = type("S", (), {"value": "pending"})()


class _FakeEngine:
    def __init__(self) -> None:
        self.runs: list[tuple[str, str, dict]] = []

    async def run_workflow(self, *, workflow_path, trigger_id, trigger_type, trigger_payload):
        run_id = "run-" + trigger_id
        self.runs.append((workflow_path, trigger_id, trigger_payload))
        return _FakeRun(run_id, workflow_path)


# ── Helpers ───────────────────────────────────────────────────────────


def _write_vault_file(vault_dir: Path, rel_path: str, content: str) -> None:
    full = vault_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _wf_md(title: str, triggers: list[dict] | None = None, enabled: bool = True) -> str:
    import yaml

    fm = {"workflow-plugin": "basic", "enabled": enabled, "title": title}
    if triggers:
        fm["triggers"] = triggers
    return f"---\n{yaml.dump(fm, default_flow_style=False)}---\n# {title}\n\nbody\n"


def _entries(vault_dir: Path):
    from nexus.vault import Entry

    out = []
    for root, _dirs, files in os.walk(vault_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, vault_dir)
            out.append(Entry(path=rel, type="file"))
    return out


def _read(vault_dir: Path, rel_path: str):
    full = vault_dir / rel_path
    content = full.read_text(encoding="utf-8")
    return {"content": content}


def _write(vault_dir: Path, rel_path: str, content: str):
    full = vault_dir / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


# ── Tests ─────────────────────────────────────────────────────────────


async def test_trigger_info_lists_triggers(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir,
        "workflows/digest.md",
        _wf_md(
            "Daily Digest",
            [
                {"id": "t1", "type": "schedule", "cron": "0 9 * * 1-5"},
                {"id": "t2", "type": "webhook", "token": "secret123"},
            ],
        ),
    )
    _write_vault_file(
        vault_dir,
        "workflows/notes.md",
        _wf_md(
            "Notes Watch",
            [{"id": "t3", "type": "fs_watch", "path": "notes/**", "events": ["created"]}],
        ),
    )
    _write_vault_file(
        vault_dir, "not-a-workflow.md", "# Just a note\n\nno frontmatter plugin\n"
    )

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))

    result = json.loads(await handle_trigger_info({}, None))
    assert result["ok"] is True
    ids = {(t["workflow_path"], t["id"]) for t in result["triggers"]}
    assert ("workflows/digest.md", "t1") in ids
    assert ("workflows/digest.md", "t2") in ids
    assert ("workflows/notes.md", "t3") in ids

    # Webhook token must not be leaked.
    t2 = [t for t in result["triggers"] if t["id"] == "t2"][0]
    assert t2.get("has_token") is True
    assert "token" not in t2
    assert "secret" not in t2

    # Schedule cron is visible.
    t1 = [t for t in result["triggers"] if t["id"] == "t1"][0]
    assert t1["cron"] == "0 9 * * 1-5"


async def test_trigger_create_schedule(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir, "workflows/digest.md", _wf_md("Daily Digest", enabled=False)
    )

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.write_file", lambda p, c: _write(vault_dir, p, c))

    result = json.loads(
        await handle_trigger(
            {
                "action": "create",
                "workflow_path": "workflows/digest.md",
                "trigger": {"type": "schedule", "cron": "0 9 * * 1-5"},
            },
            None,
        )
    )
    assert result["ok"] is True
    assert result["action"] == "create"
    assert "trigger_id" in result
    assert result["type"] == "schedule"

    # Verify the file was written with the new trigger.
    content = (vault_dir / "workflows/digest.md").read_text(encoding="utf-8")
    assert "0 9 * * 1-5" in content
    assert "enabled: true" in content  # create enables the workflow


async def test_trigger_create_webhook_no_token_leak(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir, "workflows/hook.md", _wf_md("Webhook Receiver", enabled=False)
    )

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.write_file", lambda p, c: _write(vault_dir, p, c))

    result = json.loads(
        await handle_trigger(
            {
                "action": "create",
                "workflow_path": "workflows/hook.md",
                "trigger": {"type": "webhook"},
            },
            None,
        )
    )
    assert result["ok"] is True
    # Token must NOT be returned to the agent.
    assert "token" not in result
    assert "secret" not in result
    assert result.get("note") is not None


async def test_trigger_pause_and_resume(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir,
        "workflows/digest.md",
        _wf_md("Daily Digest", [{"id": "t1", "type": "schedule", "cron": "0 9 * * 1-5"}]),
    )

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.write_file", lambda p, c: _write(vault_dir, p, c))

    # Pause.
    r1 = json.loads(
        await handle_trigger(
            {"action": "pause", "workflow_path": "workflows/digest.md", "trigger": {"id": "t1"}},
            None,
        )
    )
    assert r1["ok"] is True
    assert r1["workflow_enabled"] is False

    # Resume.
    r2 = json.loads(
        await handle_trigger(
            {"action": "resume", "workflow_path": "workflows/digest.md", "trigger": {"id": "t1"}},
            None,
        )
    )
    assert r2["ok"] is True
    assert r2["workflow_enabled"] is True


async def test_trigger_update_cron(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir,
        "workflows/digest.md",
        _wf_md("Daily Digest", [{"id": "t1", "type": "schedule", "cron": "0 9 * * 1-5"}]),
    )

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.write_file", lambda p, c: _write(vault_dir, p, c))

    result = json.loads(
        await handle_trigger(
            {
                "action": "update",
                "workflow_path": "workflows/digest.md",
                "trigger": {"id": "t1", "cron": "0 10 * * 1-5"},
            },
            None,
        )
    )
    assert result["ok"] is True

    content = (vault_dir / "workflows/digest.md").read_text(encoding="utf-8")
    assert "0 10 * * 1-5" in content
    assert "0 9 * * 1-5" not in content


async def test_trigger_run_now(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(vault_dir, "workflows/digest.md", _wf_md("Daily Digest"))

    engine = _FakeEngine()
    set_engine_getter(lambda: engine)

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))

    result = json.loads(
        await handle_trigger(
            {
                "action": "run_now",
                "workflow_path": "workflows/digest.md",
                "trigger": {"id": "manual"},
            },
            lambda: engine,
        )
    )
    assert result["ok"] is True
    assert result["action"] == "run_now"
    assert result["run_id"] == "run-manual"
    assert len(engine.runs) == 1


async def test_delete_trigger(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(
        vault_dir,
        "workflows/digest.md",
        _wf_md(
            "Daily Digest",
            [
                {"id": "t1", "type": "schedule", "cron": "0 9 * * 1-5"},
                {"id": "t2", "type": "webhook", "token": "abc"},
            ],
        ),
    )

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.write_file", lambda p, c: _write(vault_dir, p, c))

    result = json.loads(
        await handle_delete_trigger(
            {"workflow_path": "workflows/digest.md", "trigger": {"id": "t2"}},
            None,
        )
    )
    assert result["ok"] is True
    assert result["action"] == "delete"

    content = (vault_dir / "workflows/digest.md").read_text(encoding="utf-8")
    assert "t2" not in content
    assert "t1" in content  # other trigger preserved


async def test_trigger_missing_path(monkeypatch, tmp_path: Path):
    result = json.loads(
        await handle_trigger(
            {"action": "create", "trigger": {"type": "schedule", "cron": "0 9 * * 1-5"}},
            None,
        )
    )
    assert result["ok"] is False
    assert "workflow_path required" in result["error"]


async def test_trigger_unknown_type(monkeypatch, tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    _write_vault_file(vault_dir, "workflows/digest.md", _wf_md("Daily Digest"))

    monkeypatch.setattr("nexus.tools.trigger_tool._vault.list_tree", lambda: _entries(vault_dir))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.read_file", lambda p: _read(vault_dir, p))
    monkeypatch.setattr("nexus.tools.trigger_tool._vault.write_file", lambda p, c: _write(vault_dir, p, c))

    result = json.loads(
        await handle_trigger(
            {
                "action": "create",
                "workflow_path": "workflows/digest.md",
                "trigger": {"type": "not_a_type"},
            },
            None,
        )
    )
    assert result["ok"] is False
    assert "unknown trigger type" in result["error"]
