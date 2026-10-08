"""The pre-turn context gate must make room, not refuse.

``precheck_context_window`` sits in front of every turn (web, Telegram,
coordinator), so it was the one place a user actually got stuck. The old
version ran a single tool-shrink pass, discarded that pass unless it fully
solved the problem, and then hard-refused with ``retryable: False``. On a
history of mostly prose there was no recovery at all, and when the model's
window was unknown the whole gate silently disabled itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from nexus.agent.llm import ChatMessage, Role
from nexus.agent.loop.overflow import estimate_tokens
from nexus.server.services import turn_launcher


@dataclass
class _FakeModelEntry:
    id: str = "small-model"
    model_name: str = "small-model"
    context_window: int = 0


@dataclass
class _FakeAgentCfg:
    default_model: str = "small-model"


@dataclass
class _FakeCfg:
    agent: _FakeAgentCfg = field(default_factory=_FakeAgentCfg)
    models: list = field(default_factory=list)


@pytest.fixture(autouse=True)
def _isolate_artifact_dirs(tmp_path, monkeypatch):
    """Keep compaction's vault writes out of the developer's real vault."""
    import nexus.agent.loop.compact as compact_mod
    import nexus.agent.loop.summarize as summarize_mod

    monkeypatch.setattr(compact_mod, "_vault_tool_cache_fn", lambda: tmp_path / ".tool-cache")
    monkeypatch.setattr(summarize_mod, "_session_memory_fn", lambda: tmp_path)


def _use_window(monkeypatch, window: int) -> None:
    cfg = _FakeCfg(models=[_FakeModelEntry(context_window=window)])
    monkeypatch.setattr(turn_launcher, "load_config", lambda: cfg)


def _prose_history(turns: int = 40) -> list[ChatMessage]:
    history: list[ChatMessage] = [ChatMessage(role=Role.SYSTEM, content="system")]
    for i in range(turns):
        history.append(ChatMessage(role=Role.USER, content=f"q{i} " + "y" * 3_000))
        history.append(ChatMessage(role=Role.ASSISTANT, content=f"a{i} " + "z" * 3_000))
    return history


async def test_small_history_passes_through_untouched(monkeypatch) -> None:
    _use_window(monkeypatch, 200_000)
    history = [ChatMessage(role=Role.USER, content="hello")]

    out, err = await turn_launcher.precheck_context_window(history, "hi again", "small-model")

    assert err is None
    assert out == history


async def test_long_prose_history_is_rescued_without_a_summarizer(monkeypatch) -> None:
    """The exact case the old gate could not handle: no tool results to
    shrink and no provider to summarize with. It must still not error."""
    _use_window(monkeypatch, 60_000)
    history = _prose_history()
    before = estimate_tokens(history)

    out, err = await turn_launcher.precheck_context_window(
        history, "the next question", "small-model", provider=None, session_id="s1"
    )

    assert err is None, "conversation length must never block a turn"
    assert estimate_tokens(out) < before


async def test_oversized_single_message_still_errors(monkeypatch) -> None:
    """The one remaining failure is actionable by the user: shorten it."""
    _use_window(monkeypatch, 40_000)
    huge = "x" * 400_000

    out, err = await turn_launcher.precheck_context_window([], huge, "small-model")

    assert err is not None
    assert err["reason"] == "message_too_large"
    assert "compact_history" in err["actions"]
    assert out == []


async def test_unknown_model_is_still_checked(monkeypatch) -> None:
    """A model with no configured and no known window used to skip the gate
    entirely, which is how the newest models got the least protection."""
    cfg = _FakeCfg(models=[])
    cfg.agent.default_model = "totally-made-up-model"
    monkeypatch.setattr(turn_launcher, "load_config", lambda: cfg)

    history = _prose_history()
    before = estimate_tokens(history)

    out, err = await turn_launcher.precheck_context_window(
        history, "next", "totally-made-up-model", provider=None, session_id="s2"
    )

    assert err is None
    assert estimate_tokens(out) < before


async def test_attachments_count_against_the_budget(monkeypatch) -> None:
    from nexus.agent.llm import ContentPart

    _use_window(monkeypatch, 40_000)
    # Many images: their wire cost dwarfs the vault paths they carry.
    parts = [
        ContentPart(kind="image", vault_path=f"uploads/{i}.png", mime_type="image/png")
        for i in range(40)
    ]

    _out, err = await turn_launcher.precheck_context_window(
        [], "describe these", "small-model", attachment_parts=parts
    )

    assert err is not None
    assert err["reason"] == "message_too_large"


async def test_internal_failure_degrades_to_no_error(monkeypatch) -> None:
    """A bug in the gate must never block chat."""
    def _boom():
        raise RuntimeError("config exploded")

    monkeypatch.setattr(turn_launcher, "load_config", _boom)
    history = [ChatMessage(role=Role.USER, content="hi")]

    out, err = await turn_launcher.precheck_context_window(history, "hi", "small-model")

    assert err is None
    assert out == history
