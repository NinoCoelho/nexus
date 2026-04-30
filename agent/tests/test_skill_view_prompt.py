"""``skill_view`` opens a credential prompt for missing keys.

The prompt is delivered via :class:`AskUserHandler`. We don't spin up a real
Loom broker — instead we substitute a stub handler and verify the wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nexus.agent.ask_user_tool import AskUserResult
from nexus.skills.registry import SkillRegistry
from nexus.tools.state_tool import StateToolHandler


class _StubAskUser:
    """Returns a pre-canned answer dict."""

    def __init__(self, answer: dict | None) -> None:
        self.answer = answer
        self.calls: list[dict] = []

    async def invoke(self, args: dict[str, Any]) -> AskUserResult:
        self.calls.append(args)
        if self.answer is None:
            return AskUserResult(
                ok=True, answer=None, kind="form", timed_out=True,
            )
        secret_names = tuple(
            f.get("name", "") for f in args.get("fields", []) if f.get("secret")
        )
        return AskUserResult(
            ok=True,
            answer=self.answer,
            kind="form",
            timed_out=False,
            secret_fields=secret_names,
        )


def _seed_skill(skills_dir: Path, name: str, frontmatter: str, body: str = "body") -> None:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n{frontmatter}\n---\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexus import secrets as _s

    monkeypatch.setattr(_s, "SECRETS_PATH", tmp_path / "secrets.toml")
    skills_dir = tmp_path / "skills"
    monkeypatch.setenv("NEXUS_BUILTIN_SKILLS_DIR", str(tmp_path / "_empty"))
    return skills_dir


async def test_no_requires_keys_returns_body(setup) -> None:
    skills_dir: Path = setup
    _seed_skill(skills_dir, "plain", 'name: plain\ndescription: "x"')
    registry = SkillRegistry(skills_dir)
    handler = StateToolHandler(registry)
    res = await handler.invoke("skill_view", {"name": "plain"})
    assert res.ok
    assert res.data["body"].startswith("body")
    # No credentials section when nothing is declared
    assert "credentials" not in res.data


async def test_credentials_metadata_emitted_for_skill_with_keys(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LLM needs to know it can use `$NAME` placeholders. Without the
    metadata, models fall back to asking the user for the env var even
    though we already stored it."""
    skills_dir: Path = setup
    _seed_skill(
        skills_dir,
        "withcreds",
        'name: withcreds\ndescription: "x"\nrequires_keys:\n  - SOME_KEY',
    )
    registry = SkillRegistry(skills_dir)
    monkeypatch.setenv("SOME_KEY", "from-env")
    handler = StateToolHandler(registry)
    res = await handler.invoke("skill_view", {"name": "withcreds"})
    assert res.ok
    assert res.data["credentials"]["available"] == ["SOME_KEY"]
    usage = res.data["credentials"]["usage"]
    assert "$NAME" in usage or "$SOME_KEY" in usage or "placeholder" in usage.lower()
    assert "do not" in usage.lower() or "don't" in usage.lower()


async def test_env_var_satisfies_requirement(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the env has the value, no prompt fires."""
    skills_dir: Path = setup
    _seed_skill(
        skills_dir,
        "envskill",
        'name: envskill\ndescription: "x"\nrequires_keys:\n  - PRESET_KEY',
    )
    registry = SkillRegistry(skills_dir)
    monkeypatch.setenv("PRESET_KEY", "from-env")
    stub = _StubAskUser(answer=None)
    handler = StateToolHandler(registry, ask_user=stub)
    res = await handler.invoke("skill_view", {"name": "envskill"})
    assert res.ok, res.error
    assert stub.calls == []


async def test_missing_key_triggers_form_and_persists(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus import secrets

    skills_dir: Path = setup
    _seed_skill(
        skills_dir,
        "needskey",
        (
            'name: needskey\ndescription: "x"\n'
            "requires_keys:\n"
            "  - name: NEEDS_KEY\n"
            "    help: a help message\n"
            "    url: https://example.com\n"
        ),
    )
    registry = SkillRegistry(skills_dir)
    monkeypatch.delenv("NEEDS_KEY", raising=False)
    stub = _StubAskUser(answer={"NEEDS_KEY": "user-typed-value"})
    handler = StateToolHandler(registry, ask_user=stub)

    res = await handler.invoke("skill_view", {"name": "needskey"})
    assert res.ok
    assert res.data["body"]
    # Form was opened with a secret field carrying the help/url metadata
    assert len(stub.calls) == 1
    fields = stub.calls[0]["fields"]
    assert fields[0]["secret"] is True
    assert fields[0]["name"] == "NEEDS_KEY"
    assert fields[0]["help"] == "a help message"
    assert fields[0]["help_url"] == "https://example.com"
    # Value was persisted with kind=skill
    assert secrets.get("NEEDS_KEY") == "user-typed-value"
    # Second call must NOT re-prompt
    res2 = await handler.invoke("skill_view", {"name": "needskey"})
    assert res2.ok
    assert len(stub.calls) == 1


async def test_user_dismissed_form_returns_error(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_dir: Path = setup
    _seed_skill(
        skills_dir,
        "needskey2",
        'name: needskey2\ndescription: "x"\nrequires_keys:\n  - DISMISSED_KEY',
    )
    registry = SkillRegistry(skills_dir)
    monkeypatch.delenv("DISMISSED_KEY", raising=False)
    stub = _StubAskUser(answer=None)  # answer=None → timed_out=True
    handler = StateToolHandler(registry, ask_user=stub)
    res = await handler.invoke("skill_view", {"name": "needskey2"})
    assert not res.ok
    assert res.error and "timed out" in res.error.lower()


async def test_no_ask_user_handler_returns_clear_error(
    setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills_dir: Path = setup
    _seed_skill(
        skills_dir,
        "needskey3",
        'name: needskey3\ndescription: "x"\nrequires_keys:\n  - NO_HITL_KEY',
    )
    registry = SkillRegistry(skills_dir)
    monkeypatch.delenv("NO_HITL_KEY", raising=False)
    handler = StateToolHandler(registry, ask_user=None)
    res = await handler.invoke("skill_view", {"name": "needskey3"})
    assert not res.ok
    assert "NO_HITL_KEY" in (res.error or "")
