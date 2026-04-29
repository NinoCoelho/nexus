"""Wizard state-machine tests with a stubbed LLM provider."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.agent.folder_graph import _wizard


def _parse_events(frames: list[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for frame in frames:
        lines = frame.strip().split("\n")
        event = lines[0].split(": ", 1)[1]
        data = json.loads(lines[1].split(": ", 1)[1])
        out.append((event, data))
    return out


class _StubResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = []


class _StubProvider:
    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self.calls = 0

    async def chat(self, messages, *, tools=None, model=None):
        i = self.calls
        self.calls += 1
        return _StubResponse(self._replies[i])


@pytest.fixture
def cfg() -> SimpleNamespace:
    return SimpleNamespace(
        agent=SimpleNamespace(default_model="stub-model"),
        graphrag=SimpleNamespace(extraction_model_id=""),
    )


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, replies: list[str]):
    provider = _StubProvider(replies)
    monkeypatch.setattr(
        _wizard, "_resolve_chat_llm", lambda cfg: (provider, "stub-upstream"),
    )
    return provider


@pytest.mark.asyncio
async def test_wizard_finishes_immediately_when_no_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: SimpleNamespace
) -> None:
    (tmp_path / "a.md").write_text("alpha content body", encoding="utf-8")
    (tmp_path / "b.md").write_text("beta content body", encoding="utf-8")

    final_reply = json.dumps({
        "ontology": {
            "entity_types": ["person", "concept"],
            "relations": ["mentions"],
            "allow_custom_relations": True,
        },
        "next_question": None,
        "rationale": "Looks like meeting notes.",
    })
    _patch_resolver(monkeypatch, [final_reply])

    frames = []
    async for f in _wizard.start_wizard(str(tmp_path), cfg):
        frames.append(f)

    events = _parse_events(frames)
    types = [e for e, _ in events]
    assert types[0] == "wizard_id"
    assert "proposal" in types
    assert types[-1] == "done"
    # Done payload carries the final ontology
    done = next(d for e, d in events if e == "done")
    assert done["ontology"]["entity_types"] == ["person", "concept"]


@pytest.mark.asyncio
async def test_wizard_completes_after_user_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: SimpleNamespace
) -> None:
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")

    first = json.dumps({
        "ontology": {"entity_types": ["x"], "relations": ["y"],
                     "allow_custom_relations": True},
        "next_question": {"text": "Medical or prescription?",
                          "choices": ["medical records", "prescription guides"]},
        "rationale": "Ambiguous.",
    })
    second = json.dumps({
        "ontology": {"entity_types": ["patient", "diagnosis"],
                     "relations": ["has", "treated_with"],
                     "allow_custom_relations": True},
        "next_question": None,
        "rationale": "User said medical records.",
    })
    _patch_resolver(monkeypatch, [first, second])

    # Drive the wizard manually so we can answer mid-stream.
    gen = _wizard.start_wizard(str(tmp_path), cfg)
    collected: list[str] = []

    async def _consume_until_question() -> str:
        wid = ""
        async for frame in gen:
            collected.append(frame)
            event, data = _parse_events([frame])[0]
            if event == "wizard_id":
                wid = data["wizard_id"]
            if event == "question":
                return wid
        raise AssertionError("never saw a question event")

    async def _drive() -> None:
        wid = await _consume_until_question()
        # Answer the question — this should unblock the generator.
        ok = _wizard.answer_wizard(wid, "medical records")
        assert ok is True
        async for frame in gen:
            collected.append(frame)

    await _drive()

    events = _parse_events(collected)
    types = [e for e, _ in events]
    assert types[-1] == "done"
    # Final ontology is the second-turn ontology
    done = next(d for e, d in events if e == "done")
    assert "patient" in done["ontology"]["entity_types"]


@pytest.mark.asyncio
async def test_wizard_errors_when_llm_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: SimpleNamespace
) -> None:
    (tmp_path / "a.md").write_text("alpha", encoding="utf-8")
    monkeypatch.setattr(_wizard, "_resolve_chat_llm", lambda cfg: (None, None))

    frames = []
    async for f in _wizard.start_wizard(str(tmp_path), cfg):
        frames.append(f)

    types = [e for e, _ in _parse_events(frames)]
    assert "error" in types


@pytest.mark.asyncio
async def test_wizard_errors_on_missing_folder(
    tmp_path: Path, cfg: SimpleNamespace
) -> None:
    frames = []
    async for f in _wizard.start_wizard(str(tmp_path / "ghost"), cfg):
        frames.append(f)

    events = _parse_events(frames)
    assert events[0][0] == "error"


def test_answer_wizard_unknown_id_returns_false() -> None:
    assert _wizard.answer_wizard("does-not-exist", "x") is False
