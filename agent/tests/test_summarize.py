"""summarize_older_turns — relevance-ranked retention integration.

Mocks the summarizer LLM so we can assert which messages survive verbatim vs.
get collapsed into the summary, without depending on a real model.
"""

from __future__ import annotations

import pytest

from nexus.agent.llm import ChatMessage, ChatResponse, Role, StopReason
from nexus.agent.loop.summarize import _compute_semantic_sim, summarize_older_turns


@pytest.fixture(autouse=True)
def _stub_semantic_for_mechanism_tests(monkeypatch):
    # The mechanism tests must be deterministic and not depend on the real
    # fastembed model. Stub the semantic step so summarize_older_turns uses
    # regex-only scoring. The explicit _compute_semantic_sim tests below bind
    # the real function via direct import and are unaffected.
    async def _noop(messages, query, embedder):
        return None

    monkeypatch.setattr("nexus.agent.loop.summarize._compute_semantic_sim", _noop)


class _MockSummarizer:
    """Returns a canned summary; records whether it was called."""

    def __init__(self, summary_text: str = "## Session Memory\n- Goals: test"):
        self._text = summary_text
        self.calls = 0

    async def chat(self, messages, *, tools=None, model=None, max_tokens=None):
        self.calls += 1
        return ChatResponse(content=self._text, stop_reason=StopReason.STOP)


class _FakeEmbedder:
    """Returns canned vectors (caller passes [query, *msgs] order)."""

    def __init__(self, vectors: list[list[float]]):
        self._vectors = vectors

    async def embed(self, texts):
        return self._vectors


def _u(text: str) -> ChatMessage:
    return ChatMessage(role=Role.USER, content=text)


async def test_short_history_is_noop() -> None:
    provider = _MockSummarizer()
    msgs = [_u(f"m{i}") for i in range(10)]
    summary, kept = await summarize_older_turns(
        msgs, provider, keep_recent_n=20
    )
    assert summary == ""
    assert [m.content for m in kept] == [m.content for m in msgs]
    assert provider.calls == 0


async def test_long_history_invokes_summarizer_and_shrinks() -> None:
    provider = _MockSummarizer()
    msgs = [_u(f"message number {i}") for i in range(30)]
    summary, kept = await summarize_older_turns(
        msgs, provider, keep_recent_n=20
    )
    assert provider.calls == 1
    assert summary.startswith("## Session Memory")
    # Kept set is strictly smaller than the input — the low-relevance head was
    # collapsed into the summary (caller prepends it).
    assert len(kept) < 30


async def test_empty_summary_falls_back_to_full_history() -> None:
    provider = _MockSummarizer(summary_text="")
    msgs = [_u(f"m{i}") for i in range(30)]
    summary, kept = await summarize_older_turns(
        msgs, provider, keep_recent_n=20
    )
    assert summary == ""
    # Degradation: nothing dropped, full history returned.
    assert len(kept) == 30


async def test_entity_relevant_head_message_survives_verbatim() -> None:
    """A head message whose entities overlap the latest user message is kept
    verbatim, not summarized — the relevance-ranking payoff."""
    provider = _MockSummarizer()
    msgs = [_u(f"filler {i}") for i in range(2)]
    msgs.append(_u("I was working on src/lib/handlers.py earlier"))  # index 2
    msgs += [_u(f"more filler {i}") for i in range(20)]
    msgs.append(_u("let's update src/lib/handlers.py"))  # last user / query

    _summary, kept = await summarize_older_turns(
        msgs, provider, keep_recent_n=20
    )
    kept_texts = [getattr(m, "content", "") for m in kept]
    assert any("src/lib/handlers.py" in (t or "") for t in kept_texts), (
        "entity-relevant head message should survive verbatim"
    )


async def test_scrape_garbage_in_head_is_dropped() -> None:
    provider = _MockSummarizer()
    garbage = (
        "function() { document.addEventListener('click', f); var x=1; const y=2; "
        "let z=3; color:#fff; background:red; margin:0; padding:0; "
        "font-family:arial; font-size:12px; display:flex; window.location='/'; } "
        "are you a robot? just a moment"
    )
    msgs = [
        ChatMessage(role=Role.USER, content="q"),
        ChatMessage(role=Role.ASSISTANT, content="go", tool_calls=[]),
        ChatMessage(role=Role.TOOL, content=garbage, tool_call_id="tc1", name="scrape"),
        *[_u(f"f{i}") for i in range(25)],
        ChatMessage(role=Role.ASSISTANT, content="ok"),  # so garbage pair isn't last-asst
    ]
    _summary, kept = await summarize_older_turns(msgs, provider, keep_recent_n=20)
    # The garbage blob must not appear anywhere in the survivors.
    for m in kept:
        assert "addEventListener" not in (m.content or "")


def test_persist_summary_part_journals_dropped_messages(tmp_path, monkeypatch) -> None:
    """Summarized messages are recoverable — nothing is a black hole."""
    import json as _json

    from nexus.agent.loop import summarize as sm

    monkeypatch.setattr(sm, "_session_memory_fn", lambda: tmp_path)

    msgs = [_u("lost to summary")] + [_u(f"f{i}") for i in range(3)]
    path = sm.persist_summary_part("sess-xyz", msgs)
    assert path is not None
    archive = tmp_path / ".parts" / "sess-xyz.jsonl"
    assert archive.exists()
    records = [_json.loads(line) for line in archive.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["count"] == 4
    assert records[0]["messages"][0]["content"] == "lost to summary"


def test_persist_summary_part_empty_is_noop(tmp_path, monkeypatch) -> None:
    from nexus.agent.loop import summarize as sm

    monkeypatch.setattr(sm, "_session_memory_fn", lambda: tmp_path)
    assert sm.persist_summary_part("s", []) is None
    assert not (tmp_path / ".parts").exists()


# ── semantic similarity (Phase 4) ──────────────────────────────────────────


async def test_compute_semantic_sim_cosine_ranking() -> None:
    # query vector ~parallel to msg0, orthogonal to msg1.
    embedder = _FakeEmbedder([[1.0, 0.0], [0.95, 0.05], [0.0, 1.0]])
    msgs = [_u("similar intent"), _u("unrelated")]
    sim = await _compute_semantic_sim(msgs, "query", embedder)
    assert sim is not None
    assert sim[0] > 0.9
    assert sim[1] < 0.1


async def test_compute_semantic_sim_skips_empty_messages() -> None:
    # Only one non-empty message → embedder gets query + 1 text.
    embedder = _FakeEmbedder([[1.0, 0.0], [0.95, 0.05]])
    msgs = [ChatMessage(role=Role.USER, content=None), _u("has text")]
    sim = await _compute_semantic_sim(msgs, "q", embedder)
    assert sim is not None
    assert 1 in sim
    assert 0 not in sim


async def test_compute_semantic_sim_embed_failure_returns_none() -> None:
    class _Boom:
        async def embed(self, texts):
            raise RuntimeError("embedder unavailable")

    sim = await _compute_semantic_sim([_u("x")], "q", _Boom())
    assert sim is None


async def test_compute_semantic_sim_all_empty_returns_none() -> None:
    embedder = _FakeEmbedder([[1.0, 0.0]])
    msgs = [ChatMessage(role=Role.USER, content=None)]
    sim = await _compute_semantic_sim(msgs, "q", embedder)
    assert sim is None
