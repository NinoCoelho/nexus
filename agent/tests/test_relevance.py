"""Relevance scoring — deterministic, property-based tests.

These assert *ordering* properties (recent > old, user > tool, entity-overlap
boosts, pinned wins) rather than exact floats, so the test suite stays green
when the weight constants are re-tuned.
"""

from __future__ import annotations

from nexus.agent.llm import ChatMessage, Role
from nexus.agent.loop.relevance import (
    extract_entities,
    score_messages,
)


def _user(text: str) -> ChatMessage:
    return ChatMessage(role=Role.USER, content=text)


def _tool(text: str) -> ChatMessage:
    return ChatMessage(role=Role.TOOL, content=text, name="t")


def _assistant(text: str) -> ChatMessage:
    return ChatMessage(role=Role.ASSISTANT, content=text)


def test_extract_entities_catches_high_signal_tokens() -> None:
    text = "see `process_data` in src/lib/handlers.py and https://x.io/a <!-- nx:id=abc -->"
    ents = extract_entities(text)
    assert "process_data" in ents
    assert "src/lib/handlers.py" in ents
    assert "https://x.io/a" in ents
    assert "nx:id=abc" in ents


def test_extract_entities_empty() -> None:
    assert extract_entities("") == frozenset()
    assert extract_entities("plain english with no symbols") == frozenset()


def test_score_aligned_and_empty() -> None:
    assert score_messages([]) == []
    msgs = [_user("a"), _user("b"), _user("c")]
    scores = score_messages(msgs)
    assert [s.index for s in scores] == [0, 1, 2]


def test_recency_recent_beats_old() -> None:
    msgs = [_user("old")] + [_user(f"filler {i}") for i in range(8)] + [_user("newest")]
    scores = score_messages(msgs)
    assert scores[-1].score > scores[0].score


def test_role_user_beats_tool_at_tail() -> None:
    # A user message right before a tool message: the tool is more recent by
    # one slot, yet user intent should still outweigh it.
    msgs = [_user("question")] * 3 + [_user("the real question"), _tool("raw output blob")]
    scores = score_messages(msgs)
    user_score = scores[-2].score
    tool_score = scores[-1].score
    assert user_score > tool_score


def test_entity_overlap_boosts_score() -> None:
    # Two tool results adjacent; only the first mentions an entity from the
    # query. The entity boost (0.30) dwarfs the one-slot recency gap.
    query = "what does src/lib/handlers.py do"
    msgs = [
        _assistant("thinking"),
        _tool("contents of src/lib/handlers.py: def foo(): ..."),
        _tool("some unrelated large blob with no overlap"),
    ]
    scores = score_messages(msgs, query=query)
    assert scores[1].score > scores[2].score
    assert scores[1].factors["entity"] > 0.0
    assert scores[2].factors["entity"] == 0.0


def test_pinned_message_scores_max_and_highest() -> None:
    msgs = [_user("very old <!-- nx:pin -->")] + [_user(f"f {i}") for i in range(10)]
    scores = score_messages(msgs)
    pinned = scores[0]
    assert pinned.score == 1.0
    assert all(pinned.score >= s.score for s in scores)


def test_query_none_disables_entity_factor() -> None:
    msgs = [_tool("src/lib/x.py"), _tool("no entities here")]
    scores = score_messages(msgs, query=None)
    # No query → entity component is zero for both.
    assert all(s.factors["entity"] == 0.0 for s in scores)


def test_multipart_text_parts_scored() -> None:
    from nexus.agent.llm import ContentPart

    msg = ChatMessage(
        role=Role.USER,
        content=[
            ContentPart(kind="text", text="look at `frobulate`"),
            ContentPart(kind="image", vault_path="/img.png", mime_type="image/png"),
        ],
    )
    scores = score_messages([msg, _user("frobulate")], query="`frobulate`")
    # The multipart message's text part contributed the entity token.
    assert scores[0].factors["entity"] > 0.0
