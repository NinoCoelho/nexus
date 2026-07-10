"""Retention partition — invariant tests.

Focuses on the guarantees the partitioner must uphold (coverage, no overlaps,
tool-pair integrity, protected members) rather than exact bucket membership,
which shifts with the relevance weights.
"""

from __future__ import annotations

from nexus.agent.llm import ChatMessage, Role, ToolCall
from nexus.agent.loop.relevance import score_messages
from nexus.agent.loop.retention import (
    _build_units,
    partition,
)


def _u(text: str) -> ChatMessage:
    return ChatMessage(role=Role.USER, content=text)


def _a(text: str, tool_calls=None) -> ChatMessage:
    return ChatMessage(role=Role.ASSISTANT, content=text, tool_calls=tool_calls or [])


def _tool(text: str, tcid: str = "tc1") -> ChatMessage:
    return ChatMessage(role=Role.TOOL, content=text, tool_call_id=tcid, name="t")


def _tc(id_: str = "tc1", name: str = "search") -> ToolCall:
    return ToolCall(id=id_, name=name, arguments={})


def _pair(asst_text: str, tool_text: str, tcid: str = "tc1") -> list[ChatMessage]:
    return [_a(asst_text, [_tc(tcid)]), _tool(tool_text, tcid)]


# ── units ──────────────────────────────────────────────────────────────────


def test_build_units_groups_assistant_with_tools() -> None:
    msgs = [_u("q"), *_pair("calling", "result"), _u("next")]
    units = _build_units(msgs)
    assert units == [[0], [1, 2], [3]]


def test_build_units_singleton_assistant_without_toolcalls() -> None:
    msgs = [_u("q"), _a("plain reply"), _u("again")]
    units = _build_units(msgs)
    assert units == [[0], [1], [2]]


# ── partition coverage ─────────────────────────────────────────────────────


def test_partition_covers_all_indices_no_overlaps() -> None:
    msgs = [_u(f"msg {i}") for i in range(30)]
    plan = partition(msgs, score_messages(msgs))
    all_buckets = plan.protected + plan.recent + plan.relevant + plan.summarize + plan.drop
    assert sorted(all_buckets) == list(range(30))
    # no index appears in two buckets
    assert len(all_buckets) == 30


def test_partition_empty() -> None:
    plan = partition([], [])
    assert plan.kept_indices() == []
    assert plan.is_noop()


# ── protected members ──────────────────────────────────────────────────────


def test_last_user_always_protected_even_in_head() -> None:
    # A one-message recent window forces everything but the final assistant
    # into the head; the LAST user message must still be protected, not
    # summarized away.
    msgs = [*[_u(f"u{i}") for i in range(25)], _a("final reply")]
    plan = partition(msgs, score_messages(msgs), recent_k=1)
    assert 24 in plan.protected


def test_system_messages_protected() -> None:
    msgs = [
        ChatMessage(role=Role.SYSTEM, content="existing summary"),
        *[_u(f"u{i}") for i in range(25)],
    ]
    plan = partition(msgs, score_messages(msgs), recent_k=4)
    assert 0 in plan.protected


def test_pinned_message_protected_anywhere() -> None:
    msgs = [_u("a"), _u("keep me <!-- nx:pin -->"), *[_u(f"f{i}") for i in range(25)]]
    plan = partition(msgs, score_messages(msgs), recent_k=4)
    assert 1 in plan.protected


# ── tool-pair integrity ────────────────────────────────────────────────────


def test_tool_pair_in_kept_set_stays_whole() -> None:
    """If an assistant-with-tool_calls survives verbatim, so do ALL its tool
    results — never an orphan tool_call that a provider would reject."""
    msgs = [
        _u("q"),
        *_pair("go", "big result blob"),
        *[_u(f"filler {i}") for i in range(25)],
    ]
    plan = partition(msgs, score_messages(msgs), recent_k=4)
    kept = set(plan.kept_indices())
    for i, m in enumerate(msgs):
        if m.role == Role.ASSISTANT and m.tool_calls and i in kept:
            # every following TOOL until a non-tool must be kept too
            j = i + 1
            while j < len(msgs) and msgs[j].role == Role.TOOL:
                assert j in kept, f"tool result {j} dropped while caller {i} kept"
                j += 1


def test_tool_pair_summarized_together() -> None:
    """When a low-relevance tool result is in the head, the whole pair
    (assistant caller included) goes to summarize — the prose summary doesn't
    need the structured assistant->tool shape, so collapsing both is safe."""
    msgs = [
        _u("q"),
        *_pair("calling tool", "low relevance result"),
        *[_u(f"filler {i}") for i in range(25)],
    ]
    plan = partition(msgs, score_messages(msgs, query="q"), recent_k=4)
    # The pair is indices 1 (assistant) + 2 (tool). At most one is allowed in
    # kept; if either is summarized the other must NOT be in kept.
    kept = set(plan.kept_indices())
    summarized = set(plan.summarize)
    if 1 in summarized or 2 in summarized:
        assert 1 not in kept and 2 not in kept


def test_recent_boundary_is_unit_aligned() -> None:
    """A tool pair straddling the recent_k boundary stays whole in recent —
    the boundary backs up to the unit edge rather than splitting the pair."""
    msgs = [
        *[_u(f"u{i}") for i in range(18)],  # 0..17
        *_pair("caller", "result"),          # 18 (asst), 19 (tool)
        _u("final"),                         # 20
    ]
    plan = partition(msgs, score_messages(msgs), recent_k=3)
    # The pair (18,19) must be entirely in recent or entirely not — never split.
    recent_set = set(plan.recent)
    assert (18 in recent_set) == (19 in recent_set)


# ── relevance-driven bucketing ─────────────────────────────────────────────


def test_relevant_head_user_message_is_kept() -> None:
    """An older USER message whose entities overlap the current query should
    land in `relevant` (kept verbatim), not `summarize` — the whole point of
    relevance ranking over a pure positional split."""
    query = "follow up on src/lib/handlers.py"
    msgs = [
        *_pair("call", "tool junk"),
        _u("earlier I asked about src/lib/handlers.py"),
        *[_u(f"filler {i}") for i in range(25)],
    ]
    plan = partition(msgs, score_messages(msgs, query=query), recent_k=4)
    # index 2 is the entity-relevant early user message.
    assert 2 in set(plan.relevant)


def test_garbage_tool_result_is_dropped() -> None:
    """Scrape-noise TOOL results route to `drop`, not `summarize` — there's
    nothing worth summarizing, just discard. A later assistant ensures the
    garbage pair isn't the 'last assistant' (which would be protected)."""
    garbage = (
        "function() { document.addEventListener('click', f); "
        "var x = 1; const y = 2; let z = 3; color: #fff; background: red; "
        "margin: 0; padding: 0; font-family: arial; font-size: 12px; "
        "display: flex; window.location = '/'; } are you a robot? just a moment"
    )
    msgs = [_u("q"), *_pair("scrape", garbage), *[_u(f"f{i}") for i in range(25)], _a("ok")]
    plan = partition(msgs, score_messages(msgs), recent_k=4)
    # The garbage tool result is index 2.
    assert 2 in plan.drop


def test_is_noop_when_everything_recent_or_protected() -> None:
    msgs = [_u("only a few"), _u("messages here"), _u("short")]
    plan = partition(msgs, score_messages(msgs), recent_k=20)
    assert plan.is_noop()
