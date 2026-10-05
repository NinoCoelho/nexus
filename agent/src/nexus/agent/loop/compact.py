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

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from ..llm.types import ChatMessage, Role
from nexus.home import vault_tool_cache as _vault_tool_cache_fn

if TYPE_CHECKING:
    from ..llm import LLMProvider

log = logging.getLogger(__name__)


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


# Cap recursion so a pathologically deep structure can't blow the stack;
# beyond this a subtree is stringified and head-truncated.
_MAX_REDACT_DEPTH = 8


def _redact_node(node: Any, *, head_keep: int, sample_rows: int, depth: int = 0) -> Any:
    """Recursively shrink long strings and long lists throughout a JSON tree.

    The historical redaction only looked at *top-level* dict values, so a
    payload nested one level deeper — e.g. ``{"board": {"lanes": [...cards...]}}``
    from a kanban dump — was marked ``nx:compacted`` but passed through almost
    verbatim, defeating the whole point. This walks every dict/list and applies
    the same truncation uniformly.
    """
    if depth > _MAX_REDACT_DEPTH:
        s = json.dumps(node, ensure_ascii=False, default=str)
        if len(s) <= head_keep:
            return s
        return s[:head_keep] + f" ...[+{len(s) - head_keep} bytes]"
    if isinstance(node, str):
        if len(node) > head_keep:
            return node[:head_keep] + f" ...[+{len(node) - head_keep} bytes]"
        return node
    if isinstance(node, list):
        if len(node) > sample_rows * 2:
            return {
                "_truncated_list": True,
                "total_items": len(node),
                "sample": [
                    _redact_node(x, head_keep=head_keep, sample_rows=sample_rows, depth=depth + 1)
                    for x in node[:sample_rows]
                ],
            }
        return [
            _redact_node(x, head_keep=head_keep, sample_rows=sample_rows, depth=depth + 1)
            for x in node
        ]
    if isinstance(node, dict):
        return {
            k: _redact_node(v, head_keep=head_keep, sample_rows=sample_rows, depth=depth + 1)
            for k, v in node.items()
        }
    return node


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

    # Generic JSON: recursively redact long strings and long lists at every
    # nesting depth (not just the top level). Long lists are the common shape
    # behind tools like ``vault_list`` and kanban board dumps — many entries
    # that individually never triggered the old top-only heuristic but
    # collectively dominate the message. We keep ``sample_rows`` head items,
    # record the original count, and recurse into the survivors so nested
    # card/document bodies are head-truncated too.
    redacted: dict[str, Any] = {_COMPACT_MARKER: True, "original_size": len(content)}
    for k, v in obj.items():
        redacted[k] = _redact_node(v, head_keep=head_keep, sample_rows=sample_rows)
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


_AUTO_COMPACT_THRESHOLD_BYTES = 4 * 1024
_AUTO_COMPACT_HEAD_KEEP = 512

_AUTO_COMPACT_SAMPLE_ROWS = 3

# Microcompaction (Claude-Code-style tool-result clearing): when enabled via
# ``stub_older_than``, TOOL results older than that many messages are reduced
# to one-line stubs regardless of size — old results are never re-read
# verbatim, and the full payload is preserved in the vault tool cache.
_STUB_FLOOR_BYTES = 512
_STUB_FIRST_LINE_CHARS = 160


def _persist_to_vault(content: str, tool_call_id: str | None) -> str | None:
    try:
        cache_dir = _vault_tool_cache_fn()
        cache_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256(content.encode()).hexdigest()[:16]
        suffix = f"{h}.json"
        if tool_call_id:
            suffix = f"{tool_call_id}_{suffix}"
        path = cache_dir / suffix
        path.write_text(content, encoding="utf-8")
        return f"vault://.tool-cache/{suffix}"
    except Exception:
        log.debug("failed to persist tool result to vault cache", exc_info=True)
        return None


def _stub_one(content: str, tool_name: str | None) -> str:
    """Return a one-line stub replacing an old tool result (microcompaction).

    Keeps the ``nx:compacted`` marker as the first JSON key so ``_is_compacted``
    idempotency holds. The tool's own call arguments live on the preceding
    assistant message, so ``tool`` + ``first_line`` is enough for the model to
    recognize what happened and re-fetch on demand.
    """
    first_line = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    obj = {
        _COMPACT_MARKER: True,
        "stub": True,
        "tool": tool_name or "",
        "original_size": len(content),
        "first_line": first_line[:_STUB_FIRST_LINE_CHARS],
        "hint": "Old tool result stubbed to save context; re-call the tool if needed.",
    }
    return json.dumps(obj, ensure_ascii=False)


def auto_compact(
    history: list[ChatMessage],
    *,
    threshold_bytes: int = _AUTO_COMPACT_THRESHOLD_BYTES,
    head_keep: int = _AUTO_COMPACT_HEAD_KEEP,
    sample_rows: int = _AUTO_COMPACT_SAMPLE_ROWS,
    stub_older_than: int | None = None,
    stub_floor_bytes: int = _STUB_FLOOR_BYTES,
) -> tuple[list[ChatMessage], CompactionReport]:
    out: list[ChatMessage] = []
    inspected = 0
    compacted = 0
    bytes_before = 0
    bytes_after = 0
    skipped = 0

    # Region [0, stub_cutoff) is "old": its TOOL results are stubbable.
    # 0 disables (and naturally guards stub_older_than >= len(history)).
    stub_cutoff = len(history) - stub_older_than if (stub_older_than or 0) > 0 else 0

    for idx, msg in enumerate(history):
        if msg.role != Role.TOOL or not msg.content:
            out.append(msg)
            continue
        inspected += 1
        size = len(msg.content)
        bytes_before += size
        already = _is_compacted(msg.content)
        oversized = size > threshold_bytes
        stubbable = idx < stub_cutoff and size >= stub_floor_bytes
        if not oversized and not stubbable:
            bytes_after += size
            out.append(msg)
            continue
        if already:
            skipped += 1
            bytes_after += size
            out.append(msg)
            continue
        vault_ref = _persist_to_vault(msg.content, msg.tool_call_id)
        if oversized:
            new_content = _compact_one(msg.content, head_keep=head_keep, sample_rows=sample_rows)
        else:
            new_content = _stub_one(msg.content, msg.name)
        if vault_ref:
            new_content += f"\n\n[Full result saved to {vault_ref}]"
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


@dataclass
class CompactAndSummarizeReport:
    compact_report: CompactionReport = field(default_factory=lambda: CompactionReport(0, 0, 0, 0, 0))
    summarized: bool = False
    summarized_messages: int = 0
    messages_before: int = 0
    messages_after: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    zone_after: str = "green"
    still_overflowed: bool = False
    budget_exceeded: bool = False
    reinjected_paths: list[str] = field(default_factory=list)
    notes_path: str | None = None


# Tools whose ``path`` argument identifies a vault file. Post-compaction
# re-injection surfaces the most recent of these as JIT pointers so the
# model can re-read what it was working with (Claude Code keeps its five
# most-recently-accessed files for the same reason).
_VAULT_PATH_TOOLS = frozenset({"vault_read", "vault_list", "vault_write", "vault_kanban"})
_REINJECT_MAX_PATHS = 5


def _recent_vault_paths(
    messages: list[ChatMessage], *, limit: int = _REINJECT_MAX_PATHS
) -> list[str]:
    """Distinct vault paths referenced by tool calls, most-recent-first."""
    paths: list[str] = []
    for msg in reversed(messages):
        if msg.role != Role.ASSISTANT or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.name not in _VAULT_PATH_TOOLS:
                continue
            path = tc.arguments.get("path")
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
            if len(paths) >= limit:
                return paths
    return paths


async def compact_and_summarize(
    history: list[ChatMessage],
    *,
    context_window: int,
    session_id: str | None = None,
    model_id: str | None = None,
    provider: LLMProvider | None = None,
    strategy: str = "auto",
    force_summarize: bool = False,
) -> tuple[list[ChatMessage], CompactAndSummarizeReport]:
    from .zones import classify_zone
    from .summarize import summarize_older_turns

    report = CompactAndSummarizeReport(
        messages_before=len(history),
    )

    est_tokens = _estimate_for(history)
    report.tokens_before = est_tokens

    effective_window = context_window if context_window > 0 else 32_000

    result = list(history)

    run_tools = strategy in ("auto", "tools_only", "aggressive")
    run_summarize = strategy in ("auto", "summarize_only", "aggressive") or force_summarize

    aggressive = strategy == "aggressive"

    if run_tools:
        tool_threshold = 4 * 1024 if aggressive else _AUTO_COMPACT_THRESHOLD_BYTES
        tool_head = 512 if aggressive else _AUTO_COMPACT_HEAD_KEEP
        tool_rows = 2 if aggressive else _AUTO_COMPACT_SAMPLE_ROWS

        # Microcompaction: aggressive always stubs old tool results; milder
        # strategies only once the pre-tools estimate has already left the
        # green zone, so a healthy history is never touched.
        if aggressive:
            stub_older_than: int | None = 30
        else:
            stub_older_than = 30 if classify_zone(est_tokens, effective_window) != "green" else None

        compacted_result, compact_report = auto_compact(
            result,
            threshold_bytes=tool_threshold,
            head_keep=tool_head,
            sample_rows=tool_rows,
            stub_older_than=stub_older_than,
        )
        report.compact_report = compact_report
        if compact_report.compacted > 0:
            log.info(
                "compact_and_summarize: auto_compact compacted=%d saved=%d bytes",
                compact_report.compacted, compact_report.saved_bytes,
            )
            result = compacted_result

    re_tokens = _estimate_for(result)
    zone = classify_zone(re_tokens, effective_window)

    should_summarize = run_summarize and (zone in ("yellow", "orange", "red") or force_summarize)
    if should_summarize and provider is not None:
        try:
            summary, recent = await summarize_older_turns(
                result, provider,
                model_id=model_id,
                session_id=session_id,
            )
        except Exception as exc:
            from ...error_classifier import is_budget_exceeded
            if is_budget_exceeded(exc):
                report.budget_exceeded = True
                log.warning("compact_and_summarize: budget exceeded during summarization")
            else:
                log.warning("compact_and_summarize: summarization failed", exc_info=True)
        else:
            if summary:
                from .summarize import _SUMMARY_PREFIX
                # JIT re-injection: pointers to the most recent vault files
                # touched inside the summarized-away region, so the compacted
                # model can re-read its working set on demand instead of
                # losing it to the summary.
                summarized_region = result[: max(0, len(result) - len(recent))]
                paths = _recent_vault_paths(summarized_region)
                if paths:
                    summary += "\n\nRecently accessed files (re-read via vault_read):\n" + "\n".join(
                        f"- vault://{p}" for p in paths
                    )
                    report.reinjected_paths = paths
                summary_msg = ChatMessage(
                    role=Role.SYSTEM,
                    content=f"{_SUMMARY_PREFIX} — auto-generated summary]\n{summary}",
                )
                report.summarized = True
                report.summarized_messages = len(result) - len(recent)
                # Persist the session-memory note so resumes/forks can reseed
                # (see ``load_session_summary``). Best-effort — never fatal.
                if session_id:
                    from .summarize import persist_session_summary
                    report.notes_path = persist_session_summary(
                        session_id, summary, model_id=model_id
                    )
                result = [summary_msg] + recent
                log.info(
                    "compact_and_summarize: summarized %d old messages into %d chars",
                    report.summarized_messages, len(summary),
                )

    final_tokens = _estimate_for(result)
    final_zone = classify_zone(final_tokens, effective_window)

    report.messages_after = len(result)
    report.tokens_after = final_tokens
    report.zone_after = final_zone
    from .overflow import _OUTPUT_HEADROOM_TOKENS
    report.still_overflowed = final_tokens > effective_window - _OUTPUT_HEADROOM_TOKENS

    return result, report


def _estimate_for(messages: list[ChatMessage]) -> int:
    from .overflow import estimate_tokens

    class _Msg:
        pass

    compat = []
    for m in messages:
        o = _Msg()
        o.content = m.content
        o.role = m.role
        o.tool_calls = getattr(m, "tool_calls", None)
        compat.append(o)
    return estimate_tokens(compat) if compat else 0


_FAILED_SCRAPE_PATTERNS = (
    "Scrape returned no usable content",
    "page may require JavaScript rendering",
    "cf-challenge",
    "checking your browser",
    "are you a robot",
    "just a moment",
    "enable javascript",
    "attention required",
)

_CSS_JS_PATTERN = re.compile(
    r"(?:function\s*\(|var\s+\w|const\s+\w|let\s+\w|"
    r"document\.|window\.|addEventListener|"
    r"color\s*:\s*#|background\s*:|margin\s*:|padding\s*:|"
    r"font-family|font-size|display\s*:\s*(?:flex|grid|block))",
    re.IGNORECASE,
)


def _looks_like_scrape_garbage(content: str) -> bool:
    """Detect tool results that are CSS/JS noise or known failure messages."""
    if not content:
        return False
    for pattern in _FAILED_SCRAPE_PATTERNS:
        if pattern in content:
            return True
    if len(content) < 500:
        return False
    css_js_matches = len(_CSS_JS_PATTERN.findall(content[:2048]))
    return css_js_matches > 8


def compact_failed_scrapes(
    history: list[ChatMessage],
) -> tuple[list[ChatMessage], int]:
    """Replace tool messages containing scrape garbage with compact summaries.

    Returns (new_history, count_of_compacted_messages).
    """
    out: list[ChatMessage] = []
    compacted = 0
    for msg in history:
        if msg.role != Role.TOOL or not msg.content:
            out.append(msg)
            continue
        if _is_compacted(msg.content):
            out.append(msg)
            continue
        if _looks_like_scrape_garbage(msg.content):
            vault_ref = _persist_to_vault(msg.content, msg.tool_call_id)
            tool_label = msg.name or "tool"
            summary = f"[{tool_label} result was CSS/JS noise or a blocked page"
            if vault_ref:
                summary += f"; full result saved to {vault_ref}"
            summary += "]"
            out.append(
                ChatMessage(
                    role=msg.role,
                    content=summary,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )
            )
            compacted += 1
        else:
            out.append(msg)
    return out, compacted
