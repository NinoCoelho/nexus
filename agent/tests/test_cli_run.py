"""Tests for the headless CLI run command (nexus run / nexus bots list).

Monkeypatches the module's _request to avoid needing a live daemon.
"""

from __future__ import annotations

import json

import pytest
import typer

from nexus.cli import run_cmd
from nexus.cli.run_cmd import _resolve_bot, bots_list, run


class _Exit(Exception):
    def __init__(self, code: int):
        self.code = code


@pytest.fixture
def no_exit(monkeypatch):
    """Turn typer.Exit into a catchable exception so exit codes can be asserted."""
    def _raise(code):
        raise _Exit(code)

    monkeypatch.setattr(run_cmd.typer, "Exit", _raise)
    # typer.echo still works normally.


# ── _resolve_bot ──────────────────────────────────────────────────────


def test_resolve_bot_by_handle(monkeypatch):
    bots = [
        {"id": "abc123", "handle": "research", "name": "Research", "kind": "bot"},
        {"id": "main", "handle": "nico", "name": "Nico", "kind": "coordinator"},
    ]
    monkeypatch.setattr(run_cmd, "_request", lambda m, p: bots)  # bare list, real shape
    found = _resolve_bot("@research")
    assert found["id"] == "abc123"


def test_resolve_bot_tolerates_dict_wrapper(monkeypatch):
    monkeypatch.setattr(
        run_cmd, "_request", lambda m, p: {"bots": [{"id": "abc123", "handle": "research", "name": "Research"}]}
    )
    assert _resolve_bot("abc123")["handle"] == "research"


def test_bot_rows_malformed(monkeypatch):
    monkeypatch.setattr(run_cmd, "_request", lambda m, p: {"unexpected": 1})
    assert run_cmd._bot_rows() == []


def test_resolve_bot_by_id(monkeypatch):
    monkeypatch.setattr(run_cmd, "_request", lambda m, p: [{"id": "abc123", "handle": "research", "name": "Research"}])
    assert _resolve_bot("abc123")["handle"] == "research"


def test_resolve_bot_missing(monkeypatch):
    monkeypatch.setattr(run_cmd, "_request", lambda m, p: [])
    assert _resolve_bot("@nope") == {}


# ── run command ───────────────────────────────────────────────────────


def test_run_missing_bot_exits_2(monkeypatch, no_exit):
    monkeypatch.setattr(run_cmd, "_resolve_bot", lambda ref: {})
    try:
        run(bot="ghost", message="hi")
        raise AssertionError("should have exited")
    except _Exit as e:
        assert e.code == 2


def test_run_prints_reply(monkeypatch, no_exit, capsys):
    monkeypatch.setattr(
        run_cmd, "_resolve_bot", lambda ref: {"id": "abc123", "handle": "research", "name": "Research", "kind": "bot"}
    )
    monkeypatch.setattr(
        run_cmd,
        "_request",
        lambda m, p, body=None, timeout=120: (
            {"sessions": [{"id": "s1"}]} if m == "GET" else {"session_id": "s1", "reply": "done!", "iterations": 1}
        ),
    )

    run(bot="research", message="do the thing", json_out=False, new=False, timeout=600, model="")
    out = capsys.readouterr().out
    assert "done!" in out


def test_run_json_output(monkeypatch, no_exit, capsys):
    monkeypatch.setattr(
        run_cmd, "_resolve_bot", lambda ref: {"id": "abc123", "handle": "research", "name": "Research", "kind": "bot"}
    )
    captured: dict = {}

    def fake_request(m, p, body=None, timeout=120):
        if m == "GET":
            return {"sessions": [{"id": "s1"}]}
        captured["body"] = body
        return {"session_id": "s1", "reply": "done!", "iterations": 2, "skills_touched": ["web"]}

    monkeypatch.setattr(run_cmd, "_request", fake_request)

    run(bot="research", message="do it", json_out=True, new=False, timeout=600, model="")
    doc = json.loads(capsys.readouterr().out)
    assert doc["session_id"] == "s1"
    assert doc["reply"] == "done!"
    assert doc["bot"]["handle"] == "research"
    # project_id rides the POST body so the turn lands in the bot's chat
    assert captured["body"]["project_id"] == "abc123"
    assert captured["body"]["session_id"] == "s1"  # continues most recent chat


def test_run_new_skips_session_reuse(monkeypatch, no_exit, capsys):
    monkeypatch.setattr(
        run_cmd, "_resolve_bot", lambda ref: {"id": "abc123", "handle": "research", "name": "Research", "kind": "bot"}
    )
    captured: dict = {}

    def fake_request(m, p, body=None, timeout=120):
        if m == "GET":
            return {"sessions": [{"id": "s1"}]}
        captured["body"] = body
        return {"session_id": "s-new", "reply": "ok", "iterations": 1}

    monkeypatch.setattr(run_cmd, "_request", fake_request)

    run(bot="research", message="fresh start", json_out=True, new=True, timeout=600, model="")
    doc = json.loads(capsys.readouterr().out)
    assert doc["session_id"] == "s-new"
    assert "session_id" not in captured["body"]  # --new sends no session id


# ── bots list ─────────────────────────────────────────────────────────


def test_bots_list_json(monkeypatch, capsys):
    rows = [
        {"id": "abc123", "handle": "research", "name": "Research", "kind": "bot", "description": "d"},
        {"id": "main", "handle": "nico", "name": "Nico", "kind": "coordinator", "description": ""},
    ]
    monkeypatch.setattr(run_cmd, "_request", lambda m, p: rows)  # bare list
    bots_list(json_out=True)
    out = json.loads(capsys.readouterr().out)
    assert len(out) == 2
    assert out[0]["handle"] == "research"


def test_bots_list_table(monkeypatch, capsys):
    rows = [
        {"id": "abc123def456", "handle": "research", "name": "Research", "kind": "bot", "description": "finds info"},
    ]
    monkeypatch.setattr(run_cmd, "_request", lambda m, p: rows)
    bots_list(json_out=False)
    out = capsys.readouterr().out
    assert "@research" in out
    assert "Research" in out
