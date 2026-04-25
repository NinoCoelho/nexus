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
