"""``AskUserResult.to_text()`` must redact secret-flagged fields."""

from __future__ import annotations

import json

from nexus.agent.ask_user_tool import AskUserResult


def test_to_text_redacts_secret_fields() -> None:
    res = AskUserResult(
        ok=True,
        answer={"GITHUB_TOKEN": "ghp_super_secret", "name": "demo"},
        kind="form",
        timed_out=False,
        secret_fields=("GITHUB_TOKEN",),
    )
    payload = json.loads(res.to_text())
    assert payload["answer"]["GITHUB_TOKEN"] == "[redacted]"
    assert payload["answer"]["name"] == "demo"
    # In-memory field still has the raw value for server-side consumers
    assert res.answer == {"GITHUB_TOKEN": "ghp_super_secret", "name": "demo"}


def test_to_text_no_secret_fields_passes_through() -> None:
    res = AskUserResult(
        ok=True,
        answer={"name": "demo"},
        kind="form",
        timed_out=False,
    )
    payload = json.loads(res.to_text())
    assert payload["answer"] == {"name": "demo"}


def test_to_text_string_answer_unaffected() -> None:
    """`secret_fields` only kicks in when answer is a dict."""
    res = AskUserResult(
        ok=True,
        answer="just text",
        kind="text",
        timed_out=False,
        secret_fields=("WONT_MATCH",),
    )
    payload = json.loads(res.to_text())
    assert payload["answer"] == "just text"
