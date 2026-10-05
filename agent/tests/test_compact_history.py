"""History compaction — replace oversized tool results with structured summaries.

The motivating scenario: a single ``vault_read`` returned a 1MB CSV which
poisoned the session, every retry replaying the overflow. ``compact_history``
must shrink that CSV to a header + sample without disturbing surrounding
assistant/user messages or the tool_call_id linkage.
"""

from __future__ import annotations

import json

from nexus.agent.llm.types import ChatMessage, Role
from nexus.agent.loop.compact import (
    DEFAULT_COMPACT_THRESHOLD_BYTES,
    compact_history,
    auto_compact,
    _AUTO_COMPACT_THRESHOLD_BYTES,
    _AUTO_COMPACT_HEAD_KEEP,
)


def _tool_msg(content: str, *, name: str = "vault_read", tcid: str = "call_x") -> ChatMessage:
    return ChatMessage(role=Role.TOOL, content=content, tool_call_id=tcid, name=name)


def test_small_messages_are_left_alone() -> None:
    history = [
        ChatMessage(role=Role.USER, content="hi"),
        ChatMessage(role=Role.ASSISTANT, content="hello"),
        _tool_msg('{"ok": true, "content": "small"}'),
    ]
    out, report = compact_history(history)
    assert out == history
    assert report.compacted == 0
    assert report.inspected == 1


def test_user_and_assistant_never_touched_even_when_huge() -> None:
    huge = "x" * (DEFAULT_COMPACT_THRESHOLD_BYTES * 2)
    history = [
        ChatMessage(role=Role.USER, content=huge),
        ChatMessage(role=Role.ASSISTANT, content=huge),
    ]
    out, report = compact_history(history)
    assert out == history
    assert report.compacted == 0


def test_csv_tool_result_summarized_with_header_and_sample() -> None:
    rows = ["a,b,c"] + [f"{i},{i*2},{i*3}" for i in range(8000)]
    body = "\n".join(rows)
    payload = json.dumps({"ok": True, "path": "data.csv", "content": body})
    assert len(payload) > DEFAULT_COMPACT_THRESHOLD_BYTES
    history = [_tool_msg(payload, tcid="call_csv")]

    out, report = compact_history(history, sample_rows=5)
    assert report.compacted == 1
    assert report.bytes_after < report.bytes_before // 50  # massive shrink

    # Linkage preserved
    assert out[0].role == Role.TOOL
    assert out[0].tool_call_id == "call_csv"
    assert out[0].name == "vault_read"

    summary = json.loads(out[0].content)
    assert summary["compacted"] is True
    assert summary["format"] == "csv"
    assert summary["header"] == "a,b,c"
    assert summary["total_lines"] == 8001
    assert len(summary["sample_rows"]) == 5
    assert summary["original_size"] == len(body)


def test_unstructured_payload_falls_back_to_head_truncation() -> None:
    blob = "single line of garbage " * 5000  # no newlines, no JSON
    history = [_tool_msg(blob)]
    out, report = compact_history(history, head_keep=512)
    assert report.compacted == 1
    assert "nx:compacted" in out[0].content
    # head budget honored (a few bytes of slack for the marker tail)
    assert len(out[0].content) < 1024


def test_compaction_is_idempotent() -> None:
    rows = ["a,b"] + [f"row_{i},val_{i}" for i in range(8000)]
    body = "\n".join(rows)
    payload = json.dumps({"ok": True, "content": body})
    history = [_tool_msg(payload, tcid="t1")]
    # Force a low threshold so the compacted form is *still* over threshold —
    # otherwise the second pass exits early via the size short-circuit and we
    # can't see the "already compacted" branch fire.
    pass1, report1 = compact_history(history, threshold_bytes=64)
    pass2, report2 = compact_history(pass1, threshold_bytes=64)
    assert pass1 == pass2
    assert report1.compacted == 1
    assert report2.compacted == 0
    assert report2.skipped_already_compacted == 1


def test_long_list_in_generic_json_truncated() -> None:
    """vault_list-style payloads (dict whose value is a long list of small
    entries) should compact even though no single string is huge."""
    entries = [{"path": f"f/{i}.md", "type": "file", "size": 100} for i in range(5000)]
    payload = json.dumps({"ok": True, "entries": entries})
    assert len(payload) > DEFAULT_COMPACT_THRESHOLD_BYTES
    out, report = compact_history([_tool_msg(payload, tcid="t-list")], sample_rows=4)
    assert report.compacted == 1
    summary = json.loads(out[0].content)
    assert summary["nx:compacted"] is True
    bucket = summary["entries"]
    assert bucket["_truncated_list"] is True
    assert bucket["total_items"] == 5000
    assert len(bucket["sample"]) == 4
    assert report.bytes_after < report.bytes_before // 100


def test_threshold_is_respected() -> None:
    # 10KB payload — under default threshold, should be left alone even though
    # it's a CSV. We expose the threshold so callers can be aggressive.
    body = "a,b\n" + ("x,y\n" * 1000)
    payload = json.dumps({"ok": True, "content": body})
    history = [_tool_msg(payload)]
    out, report = compact_history(history, threshold_bytes=1_000_000)
    assert report.compacted == 0
    assert out == history
    out2, report2 = compact_history(history, threshold_bytes=2_000)
    assert report2.compacted == 1
    assert "compacted" in json.loads(out2[0].content)


def test_auto_compact_threshold_is_4kb() -> None:
    assert _AUTO_COMPACT_THRESHOLD_BYTES == 4 * 1024


def test_auto_compact_head_keep_is_512() -> None:
    assert _AUTO_COMPACT_HEAD_KEEP == 512


def test_auto_compact_catches_5kb_result() -> None:
    payload = "x" * 5_000
    history = [_tool_msg(payload, tcid="t5k")]
    out, report = auto_compact(history)
    assert report.compacted == 1
    assert len(out[0].content) < 2_000


def test_auto_compact_leaves_3kb_result_alone() -> None:
    payload = "x" * 3_000
    history = [_tool_msg(payload, tcid="t3k")]
    out, report = auto_compact(history)
    assert report.compacted == 0
    assert out[0].content == payload


def test_auto_compact_persists_original_to_vault_ref() -> None:
    payload = json.dumps({"ok": True, "data": "x" * 5_000})
    history = [_tool_msg(payload, tcid="t_vault")]
    out, report = auto_compact(history)
    assert report.compacted == 1
    assert "vault://" in out[0].content


# ── microcompaction (stub_older_than) ──────────────────────────────────────
# Claude-Code-style tool-result clearing: OLD results (outside the trailing
# window) become one-line stubs regardless of size; recent ones stay verbatim
# unless oversized; tool_call_id/name linkage is always preserved.


def _stub_history(n_tools: int = 5, size: int = 800) -> list[ChatMessage]:
    msgs: list[ChatMessage] = [ChatMessage(role=Role.USER, content="go")]
    for i in range(n_tools):
        msgs.append(_tool_msg(f'{{"ok": true, "data": "{i}{"x" * size}"}}', tcid=f"c{i}", name="vault_read"))
    return msgs


def test_microcompact_stubs_old_small_results() -> None:
    history = _stub_history()
    out, report = auto_compact(history, stub_older_than=2)
    # 6 messages, cutoff = 6-2 = 4: tool msgs at idx 1..3 are old → 3 stubs.
    assert report.compacted == 3
    stub = json.loads(out[1].content.split("\n\n[Full result")[0])
    assert stub["nx:compacted"] is True and stub["stub"] is True
    assert stub["tool"] == "vault_read"
    assert stub["original_size"] == len(history[1].content)
    # Old small result shrunk to a stub (+ vault ref line).
    assert len(out[1].content) < 600
    # Recent two untouched.
    assert out[-1].content == history[-1].content
    assert out[-2].content == history[-2].content


def test_microcompact_preserves_tool_linkage() -> None:
    history = _stub_history()
    out, _ = auto_compact(history, stub_older_than=2)
    for orig, new in zip(history, out):
        assert new.role == orig.role
        assert new.tool_call_id == orig.tool_call_id
        assert new.name == orig.name


def test_microcompact_idempotent() -> None:
    history = _stub_history()
    once, r1 = auto_compact(history, stub_older_than=2)
    twice, r2 = auto_compact(once, stub_older_than=2)
    assert r2.compacted == 0
    assert [m.content for m in twice] == [m.content for m in once]


def test_microcompact_floor_keeps_tiny_results() -> None:
    msgs = [_tool_msg('{"ok": true}', tcid="tiny"), ChatMessage(role=Role.USER, content="q")]
    out, report = auto_compact(msgs, stub_older_than=1)
    assert report.compacted == 0
    assert out[0].content == '{"ok": true}'


def test_microcompact_oversized_recent_still_head_compacted() -> None:
    payload = "y" * 5_000
    history = [_tool_msg('{"ok": true, "data": "' + payload + '"}', tcid="big"),
               ChatMessage(role=Role.USER, content="q")]
    out, report = auto_compact(history, stub_older_than=1)
    # Index 0 is old enough to stub (cutoff=1); oversized+old → stub path is
    # cheapest, but either way it must shrink and stay linked.
    assert report.compacted == 1
    assert len(out[0].content) < 2_000
    assert out[0].tool_call_id == "big"


def test_microcompact_disabled_by_default() -> None:
    history = _stub_history()
    out, report = auto_compact(history)
    assert report.compacted == 0
    assert [m.content for m in out] == [m.content for m in history]


# ── recursive redaction (nested JSON) ──────────────────────────────────────
# Regression: a payload nested one level deep — e.g. a kanban board dump
# ``{"board": {"lanes": [...cards...]}}`` — used to be marked nx:compacted but
# pass through ~verbatim because the redaction only looked at top-level keys.


def test_compact_one_redacts_long_strings_nested_in_dict() -> None:
    from nexus.agent.loop.compact import _compact_one

    content = json.dumps({"board": {"note": "y" * 5_000}})
    out = json.loads(_compact_one(content, head_keep=512, sample_rows=3))
    note = out["board"]["note"]
    assert "[+" in note  # nested string was head-truncated
    assert len(note) < 600


def test_compact_one_redacts_long_list_nested_in_dict() -> None:
    from nexus.agent.loop.compact import _compact_one

    cards = [{"id": f"c{i}", "body": "x" * 200} for i in range(40)]
    content = json.dumps({"board": {"lanes": [{"id": "l1", "cards": cards}]}})
    out = json.loads(_compact_one(content, head_keep=512, sample_rows=3))
    lane_cards = out["board"]["lanes"][0]["cards"]
    assert isinstance(lane_cards, dict)
    assert lane_cards["_truncated_list"] is True
    assert lane_cards["total_items"] == 40
    assert len(lane_cards["sample"]) == 3


def test_compact_one_shrinks_kanban_board_shape_dramatically() -> None:
    """The motivating case: a full board dump must collapse, not pass through."""
    from nexus.agent.loop.compact import _compact_one

    lanes = [
        {
            "id": f"l{i}",
            "title": f"Lane {i}",
            "cards": [{"id": f"c{j}", "title": f"card {j}", "body": "B" * 800} for j in range(50)],
        }
        for i in range(9)
    ]
    content = json.dumps({"ok": True, "path": "boards/job-search.md", "board": {"title": "jobs", "lanes": lanes}})
    out = _compact_one(content, head_keep=512, sample_rows=3)
    assert len(out) < 8_000  # was ~360KB; must collapse to a few KB
    obj = json.loads(out)
    assert obj["nx:compacted"] is True
    # lanes list itself got capped (9 > 6) with a survivor sample
    lanes_val = obj["board"]["lanes"]
    assert isinstance(lanes_val, dict) and lanes_val["_truncated_list"] is True


def test_compact_one_preserves_short_nested_values() -> None:
    from nexus.agent.loop.compact import _compact_one

    content = json.dumps({"meta": {"id": "abc", "title": "small"}})
    out = json.loads(_compact_one(content, head_keep=512, sample_rows=3))
    assert out["meta"] == {"id": "abc", "title": "small"}
