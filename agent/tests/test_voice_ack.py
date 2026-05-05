"""Voice acknowledgment generators.

Validates the *gating* logic — the parts most likely to break silently:
  - Disabled toggles short-circuit before any LLM call
  - The progress trace is formatted correctly
  - Synthesized audio bytes flow through to the publish payload
  - The completion-ack prompt sees the full agent reply (so trailing
    follow-up questions can be quoted)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nexus.config_file import default_config
from nexus.tts.base import SynthResult
from nexus.voice_ack import (
    _AckTrigger,
    emit_completion_ack,
    emit_start_ack,
)


@dataclass
class _StubProvider:
    content: str = "Reading the news now."

    async def chat(self, messages: list[Any], *, model: Any = None, max_tokens: int = 0, extra_payload: Any = None):
        from nexus.agent.llm import ChatResponse, StopReason

        return ChatResponse(
            content=self.content,
            tool_calls=[],
            stop_reason=StopReason.STOP,
            usage={},
        )


@dataclass
class _StubAgent:
    provider: _StubProvider = field(default_factory=_StubProvider)
    _provider_registry: Any = "any-truthy-value"

    def _resolve_provider(self, model_id: str | None):
        return self.provider, "stub-model"


class _StubStore:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict[str, Any]]] = []

    def publish(self, session_id: str, event: Any) -> None:
        self.published.append((session_id, event.kind, dict(event.data)))


def _trigger() -> _AckTrigger:
    return _AckTrigger(
        user_text="give me the latest news",
        session_id="sess-1",
        full_reply="Done — saved a summary to your vault. Want me to schedule a daily run?",
    )


def _disabled_cfg(**overrides: Any):
    cfg = default_config()
    for key, value in overrides.items():
        setattr(cfg.tts, key, value)
    return cfg


def _patch_synth_to_silent(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """voice_ack now always tries to synthesize via Piper. Patch it to
    return a no-audio result so tests don't hit the real model."""
    from nexus import voice_ack
    fake = AsyncMock(return_value=SynthResult(audio=None, mime=""))
    monkeypatch.setattr(voice_ack, "synthesize", fake)
    return fake


# ── Gating: disabled toggles must short-circuit ────────────────────────────


async def test_start_ack_skipped_when_master_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = _disabled_cfg(enabled=False)
    await emit_start_ack(agent=_StubAgent(), store=store, trigger=_trigger(), cfg=cfg)
    assert store.published == []


async def test_start_ack_skipped_when_ack_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = _disabled_cfg(ack_enabled=False)
    await emit_start_ack(agent=_StubAgent(), store=store, trigger=_trigger(), cfg=cfg)
    assert store.published == []


# ── Happy path: publish event with synthesized audio bytes ─────────────────


async def test_start_ack_publishes_event_with_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nexus import voice_ack
    fake_synth = AsyncMock(return_value=SynthResult(audio=b"\x00\x01\x02", mime="audio/wav"))
    monkeypatch.setattr(voice_ack, "synthesize", fake_synth)

    store = _StubStore()
    cfg = default_config()
    await emit_start_ack(agent=_StubAgent(), store=store, trigger=_trigger(), cfg=cfg)
    fake_synth.assert_awaited_once()
    assert len(store.published) == 1
    sid, kind, data = store.published[0]
    assert sid == "sess-1"
    assert kind == "voice_ack"
    assert data["kind"] == "start"
    assert data["transcript"] == "Reading the news now."
    assert data["audio_b64"] is not None
    assert data["audio_mime"] == "audio/wav"


async def test_completion_ack_passes_full_reply_into_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The completion prompt must include the full assistant reply so the
    summarizer can quote any trailing follow-up question."""
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    seen_prompts: list[str] = []

    async def _capturing_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        seen_prompts.append(messages[0].content)
        from nexus.agent.llm import ChatResponse, StopReason
        return ChatResponse(
            content="Done. Let me know if you want a daily run.",
            tool_calls=[],
            stop_reason=StopReason.STOP,
            usage={},
        )

    agent = _StubAgent()
    agent.provider.chat = _capturing_chat  # type: ignore[assignment]
    await emit_completion_ack(agent=agent, store=store, trigger=_trigger(), cfg=cfg)
    assert len(seen_prompts) == 1
    assert "schedule a daily run" in seen_prompts[0]


async def test_start_ack_uses_portuguese_prompt_for_portuguese_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start prompt is localized — Portuguese user_text must produce
    a Portuguese prompt to the LLM, not the English one. Local models
    follow language cues in the *instructions* much more reliably than
    a `match the user's language` directive at the end of an English
    prompt."""
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    seen_prompts: list[str] = []

    async def _capturing_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        seen_prompts.append(messages[0].content)
        from nexus.agent.llm import ChatResponse, StopReason
        return ChatResponse(
            content="Tô olhando, um momento.",
            tool_calls=[],
            stop_reason=StopReason.STOP,
            usage={},
        )

    agent = _StubAgent()
    agent.provider.chat = _capturing_chat  # type: ignore[assignment]
    trigger = _AckTrigger(
        user_text="Por favor, busque as últimas notícias sobre o mercado financeiro hoje.",
        session_id="sess-1",
    )
    await emit_start_ack(agent=agent, store=store, trigger=trigger, cfg=cfg)
    assert len(seen_prompts) == 1
    prompt = seen_prompts[0]
    # Portuguese-only marker phrases that must appear in a localized prompt.
    assert "PORTUGUÊS" in prompt
    assert "RESPONDA EM PORTUGUÊS BRASILEIRO" in prompt
    # The English template's marker must NOT appear.
    assert "REPLY IN ENGLISH" not in prompt


async def test_completion_ack_uses_portuguese_prompt_for_portuguese_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    seen_prompts: list[str] = []

    async def _capturing_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        seen_prompts.append(messages[0].content)
        from nexus.agent.llm import ChatResponse, StopReason
        return ChatResponse(
            content="Resumo aqui. Próximos passos aqui.",
            tool_calls=[],
            stop_reason=StopReason.STOP,
            usage={},
        )

    agent = _StubAgent()
    agent.provider.chat = _capturing_chat  # type: ignore[assignment]
    trigger = _AckTrigger(
        user_text="Resuma os principais pontos do relatório financeiro.",
        session_id="sess-1",
        full_reply="Done — saved a summary to your vault. Want me to schedule a daily run for you?",
    )
    await emit_completion_ack(agent=agent, store=store, trigger=trigger, cfg=cfg)
    assert len(seen_prompts) == 1
    prompt = seen_prompts[0]
    # New minimal completion prompt for PT input.
    assert "Português brasileiro falado" in prompt
    # English-template marker must not appear.
    assert "Plain spoken English" not in prompt


# ── Fallback: when LLM returns empty, never go silent ──────────────────────


async def test_completion_ack_falls_back_to_snippet_with_preamble_when_llm_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH the structured and simple prompts return empty, we read
    a truncated version of the actual reply with a clear 'Brief content:'
    preamble so the user knows it's raw content (not a polished summary)
    rather than hearing 'the answer is on the chat' which conveys nothing."""
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    agent = _StubAgent(provider=_StubProvider(content=""))  # LLM always empty
    await emit_completion_ack(agent=agent, store=store, trigger=_trigger(), cfg=cfg)
    assert len(store.published) == 1
    _, _, data = store.published[0]
    transcript = data["transcript"]
    assert "Brief content" in transcript
    # The actual reply content must reach the listener — that's the whole
    # point of the snippet preamble.
    assert "saved a summary to your vault" in transcript


async def test_completion_ack_uses_simple_retry_when_structured_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First attempt uses the structured prompt; if empty, retry with a
    simpler 'summarize this' prompt that weak local models handle better."""
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    seen_prompts: list[str] = []

    async def _capturing_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        seen_prompts.append(messages[0].content)
        from nexus.agent.llm import ChatResponse, StopReason
        # First call (structured prompt) returns empty; second (simple) works.
        content = "" if len(seen_prompts) == 1 else "Vault was updated with the news."
        return ChatResponse(
            content=content, tool_calls=[],
            stop_reason=StopReason.STOP, usage={},
        )

    agent = _StubAgent()
    agent.provider.chat = _capturing_chat  # type: ignore[assignment]
    await emit_completion_ack(agent=agent, store=store, trigger=_trigger(), cfg=cfg)
    assert len(seen_prompts) == 2
    # Second prompt is the SIMPLE one — much shorter than structured.
    assert "Summarize this text" in seen_prompts[1] or "Resuma este texto" in seen_prompts[1]
    _, _, data = store.published[0]
    assert "Vault was updated" in data["transcript"]


async def test_start_ack_skips_llm_for_empty_user_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure-voice messages reach the start ack with empty user_text
    (transcription hasn't run yet). Don't bother the LLM with thin air —
    go straight to the language template."""
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    chat_calls = 0
    async def _counting_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        nonlocal chat_calls
        chat_calls += 1
        from nexus.agent.llm import ChatResponse, StopReason
        return ChatResponse(content="should not be used", tool_calls=[],
                            stop_reason=StopReason.STOP, usage={})

    agent = _StubAgent()
    agent.provider.chat = _counting_chat  # type: ignore[assignment]
    trigger = _AckTrigger(user_text="", session_id="sess-1")
    await emit_start_ack(agent=agent, store=store, trigger=trigger, cfg=cfg)
    assert chat_calls == 0
    assert len(store.published) == 1


async def test_completion_ack_uses_ui_language_when_user_text_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When user_text is empty, the language picker should consult the
    user's UI language config — not silently default to English."""
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()
    cfg.ui.language = "pt-BR"

    seen_prompts: list[str] = []
    async def _capturing_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        seen_prompts.append(messages[0].content)
        from nexus.agent.llm import ChatResponse, StopReason
        return ChatResponse(content="Resumo OK.", tool_calls=[],
                            stop_reason=StopReason.STOP, usage={})

    agent = _StubAgent()
    agent.provider.chat = _capturing_chat  # type: ignore[assignment]
    trigger = _AckTrigger(
        user_text="",  # empty — this is the bug we're fixing
        session_id="sess-1",
        full_reply="Done. Saved to vault. Want me to schedule a daily run for you?",
    )
    await emit_completion_ack(agent=agent, store=store, trigger=trigger, cfg=cfg)
    # The chosen prompt should be the Portuguese one despite empty user_text.
    assert "Português brasileiro falado" in seen_prompts[0]


async def test_start_ack_falls_back_to_template_when_llm_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    agent = _StubAgent(provider=_StubProvider(content=""))
    # Portuguese trigger → Portuguese template.
    trigger = _AckTrigger(
        user_text="Por favor, busque as notícias mais recentes para mim hoje.",
        session_id="sess-1",
    )
    await emit_start_ack(agent=agent, store=store, trigger=trigger, cfg=cfg)
    assert len(store.published) == 1
    _, _, data = store.published[0]
    # Hardcoded PT template is "Tô olhando isso, um momento."
    assert "olhando" in data["transcript"].lower()


async def test_completion_ack_skips_entirely_for_empty_reply() -> None:
    """Empty full_reply → no LLM call, no audio, no event published."""
    store = _StubStore()
    cfg = default_config()

    chat_calls = 0
    async def _counting_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        nonlocal chat_calls
        chat_calls += 1
        from nexus.agent.llm import ChatResponse, StopReason
        return ChatResponse(content="should not be used", tool_calls=[],
                            stop_reason=StopReason.STOP, usage={})

    agent = _StubAgent()
    agent.provider.chat = _counting_chat  # type: ignore[assignment]
    trigger = _AckTrigger(
        user_text="what is 2+2?",
        session_id="sess-1",
        full_reply="",
    )
    await emit_completion_ack(agent=agent, store=store, trigger=trigger, cfg=cfg)
    assert chat_calls == 0
    assert len(store.published) == 0


async def test_completion_ack_speaks_short_reply_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replies ≤10 words are cleaned and published without LLM summarization."""
    _patch_synth_to_silent(monkeypatch)
    store = _StubStore()
    cfg = default_config()

    chat_calls = 0
    async def _counting_chat(messages, *, model=None, max_tokens=0, extra_payload=None):
        nonlocal chat_calls
        chat_calls += 1
        from nexus.agent.llm import ChatResponse, StopReason
        return ChatResponse(content="should not be used", tool_calls=[],
                            stop_reason=StopReason.STOP, usage={})

    agent = _StubAgent()
    agent.provider.chat = _counting_chat  # type: ignore[assignment]
    trigger = _AckTrigger(
        user_text="what is 2+2?",
        session_id="sess-1",
        full_reply="**The answer is 42.**",
    )
    await emit_completion_ack(agent=agent, store=store, trigger=trigger, cfg=cfg)
    assert chat_calls == 0
    assert len(store.published) == 1
    _, _, data = store.published[0]
    # Markdown stripped, spoken directly.
    assert data["transcript"] == "The answer is 42."
