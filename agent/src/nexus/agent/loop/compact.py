"""History compaction — replace oversized tool results with summaries.

When a session blows past the model's context window (typically because a
single ``vault_read`` returned a megabyte-class CSV), the conversation
becomes unrunnable: every retry resends the same overflowed history. This
module rewrites the offending tool messages in place — keeping the role,
``tool_call_id``, and ``name`` so the assistant↔tool linkage is preserved —
while shrinking the payload to a structured summary.

Goals:
  * Idempotent: re-running compaction on an already-compacted history is a
    no-op.
  * Format-aware: CSV-shaped JSON tool results get a header + row sample;
    everything else falls back to a head-truncated string.
  * Reversible enough: the summary records ``original_size`` and the tool
    call's arguments live on the preceding assistant message, so the agent
    can re-fetch a slice on demand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..llm.types import ChatMessage, Role


# Default threshold: anything bigger than this in a single tool message is a
# candidate for compaction. 32KB ≈ 8K tokens, comfortably below any sane
# per-tool budget. Configurable per call.
DEFAULT_COMPACT_THRESHOLD_BYTES = 32 * 1024
# Bytes kept verbatim from the head of an unstructured tool result.
DEFAULT_HEAD_KEEP_BYTES = 2 * 1024
# Rows kept from CSV-shaped payloads.
DEFAULT_CSV_SAMPLE_ROWS = 5


_COMPACT_MARKER = "nx:compacted"


@dataclass
class CompactionReport:
    inspected: int
    compacted: int
    bytes_before: int
    bytes_after: int
    skipped_already_compacted: int

    @property
    def saved_bytes(self) -> int:
        return self.bytes_before - self.bytes_after


def _is_compacted(content: str) -> bool:
    return _COMPACT_MARKER in content[:200]


def _summarize_csv(content_obj: dict[str, Any], sample_rows: int) -> dict[str, Any] | None:
    """If a vault_read tool result wraps CSV-ish text, return a summary blob.
    Returns None when the payload doesn't look like CSV — caller falls back to
    a generic head-truncation."""
    raw = content_obj.get("content")
    if not isinstance(raw, str) or "\n" not in raw:
        return None
    lines = raw.splitlines()
    if len(lines) < 2:
        return None
    header = lines[0]
    # Heuristic: CSV header has commas, semicolons, or tabs.
    if not any(sep in header for sep in (",", ";", "\t")):
        return None
    sample = lines[1 : 1 + sample_rows]
    return {
        **{k: v for k, v in content_obj.items() if k != "content"},
        "compacted": True,
        "format": "csv",
        "header": header,
        "total_lines": len(lines),
        "sample_rows": sample,
        "original_size": len(raw),
        "hint": (
            "Original CSV omitted to save context. To re-read a slice, call "
            "vault_read with `head=N` / `tail=N` or `offset`/`limit`."
        ),
    }


def _summarize_unstructured(text: str, head_keep: int) -> str:
    return (
        text[:head_keep]
        + f"\n\n... [{_COMPACT_MARKER}] truncated; original_size={len(text)} bytes ..."
    )


def _compact_one(content: str, *, head_keep: int, sample_rows: int) -> str:
    """Return a compacted form of a single tool message's content string."""
    # Try JSON first — vault tools wrap their payload as ``{"ok": true, ...}``.
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return _summarize_unstructured(content, head_keep)

    if not isinstance(obj, dict):
        return _summarize_unstructured(content, head_keep)

    csv_summary = _summarize_csv(obj, sample_rows)
    if csv_summary is not None:
        # Put the marker first so `_is_compacted` (which only scans the first
        # 200 chars to keep idempotency cheap) finds it regardless of how
        # large the rest of the summary grows.
        ordered = {_COMPACT_MARKER: True, **csv_summary}
        return json.dumps(ordered, ensure_ascii=False)

    # Generic JSON: keep top-level keys; redact long strings *and* long lists.
    # Long lists are the common shape behind tools like ``vault_list`` — many
    # tiny entries that individually never trigger the string heuristic but
    # collectively dominate the message. We keep ``sample_rows`` head items,
    # record the original count, and drop the rest.
    redacted: dict[str, Any] = {_COMPACT_MARKER: True, "original_size": len(content)}
    for k, v in obj.items():
        if isinstance(v, str) and len(v) > head_keep:
            redacted[k] = v[:head_keep] + f" ...[+{len(v) - head_keep} bytes]"
        elif isinstance(v, list) and len(v) > sample_rows * 2:
            redacted[k] = {
                "_truncated_list": True,
                "total_items": len(v),
                "sample": v[:sample_rows],
            }
        else:
            redacted[k] = v
    return json.dumps(redacted, ensure_ascii=False)


def compact_history(
    history: list[ChatMessage],
    *,
    threshold_bytes: int = DEFAULT_COMPACT_THRESHOLD_BYTES,
    head_keep: int = DEFAULT_HEAD_KEEP_BYTES,
    sample_rows: int = DEFAULT_CSV_SAMPLE_ROWS,
) -> tuple[list[ChatMessage], CompactionReport]:
    """Return a new history with oversized TOOL messages replaced by summaries.

    Only TOOL-role messages are touched — user/assistant content is preserved
    verbatim because rewriting reasoning or instructions silently would change
    the conversation's meaning.
    """
    out: list[ChatMessage] = []
    inspected = 0
    compacted = 0
    bytes_before = 0
    bytes_after = 0
    skipped = 0

    for msg in history:
        if msg.role != Role.TOOL or not msg.content:
            out.append(msg)
            continue
        inspected += 1
        size = len(msg.content)
        bytes_before += size
        if size <= threshold_bytes:
            bytes_after += size
            out.append(msg)
            continue
        if _is_compacted(msg.content):
            skipped += 1
            bytes_after += size
            out.append(msg)
            continue
        new_content = _compact_one(
            msg.content, head_keep=head_keep, sample_rows=sample_rows
        )
        bytes_after += len(new_content)
        compacted += 1
        out.append(
            ChatMessage(
                role=msg.role,
                content=new_content,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
            )
        )
    return out, CompactionReport(
        inspected=inspected,
        compacted=compacted,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
        skipped_already_compacted=skipped,
    )
