# Fix: reasoning_content restoration + mid-turn compaction type confusion

## Problem

Two interacting bugs causing `format_error` (HTTP 400) on DeepSeek thinking models:

1. **reasoning_content fingerprint restoration is fragile** — 55 format_errors across 28 sessions since 2026-05-23
2. **Mid-turn compaction has type confusion** — `auto_compact` receives loom messages but creates Nexus ChatMessages, producing mixed-type lists
3. **Retry/restart paths don't preserve reasoning state**

## Root Cause Analysis

The `reasoning_content` feature (commit `20031a1`) uses a fingerprint-based approach (`("assistant", content[:500], tc_ids)`) to preserve DeepSeek's reasoning across loom iterations. Loom's `ChatMessage` type has no `reasoning_content` field, so the adapter maintains a `_reasoning_content_map` dict mapping fingerprints to reasoning strings.

The fingerprint approach is fragile because:
- Any transformation (sanitization, compaction) between map population and restoration can break matching
- The map is a single dict shared across cross-turn (hydrated from history) and within-turn (self-registered after finish events)
- `hydrate_adapter_map` at agent.py:515 REPLACES the entire map, which is fine for cross-turn but means within-turn self-registrations only survive until the next `chat_stream` call

The mid-turn compaction code (agent.py:664-703) calls `auto_compact` on `tr.working_messages` (loom `lt.ChatMessage` list) but `auto_compact` creates Nexus `ChatMessage` objects, producing a mixed list.

## Fix Strategy

### Fix 1: Attribute-based reasoning_content (replaces fingerprint-only)

**Files: `adapter.py`, `agent.py`, `stream_translator.py`, `reasoning.py`**

**Core idea**: Stamp `_reasoning_content` directly on loom `ChatMessage` objects as a Python attribute (not a Pydantic field). Pydantic v1 models allow extra instance attributes via `__dict__`. The adapter checks for this attribute first, falling back to fingerprint matching.

Changes:

1. **`agent.py:396`** — After converting Nexus messages to loom messages, stamp `_reasoning_content` on assistant messages:
```python
loom_messages = [_to_loom_message(m) for m in stripped_history]
for nm, lm in zip(stripped_history, loom_messages):
    if nm.role == Role.ASSISTANT and nm.reasoning_content:
        lm._reasoning_content = nm.reasoning_content
```

2. **`adapter.py:_restore_reasoning`** — Check for `_reasoning_content` attribute first, fall back to fingerprint:
```python
def _restore_reasoning(self, loom_msgs):
    nexus_messages = [_loom_to_nexus_message(m) for m in loom_msgs]
    if not self._reasoning_content_map:
        return nexus_messages
    out = []
    for loom_m, nexus_m in zip(loom_msgs, nexus_messages):
        rc = getattr(loom_m, '_reasoning_content', None)
        if rc:
            out.append(nexus_m.model_copy(update={"reasoning_content": rc}))
            continue
        fp = _msg_fingerprint(loom_m)
        if fp and fp in self._reasoning_content_map:
            out.append(nexus_m.model_copy(update={"reasoning_content": self._reasoning_content_map[fp]}))
        else:
            out.append(nexus_m)
    return out
```

3. **`stream_translator.py:materialise_assistant_if_needed`** — Stamp reasoning on the loom assistant message created for `working_messages`:
```python
msg = lt.ChatMessage(role=lt.Role.ASSISTANT, content=content, tool_calls=tcs or None)
rc = getattr(self._adapter, "_last_reasoning_content", None) if self._adapter else None
if rc:
    msg._reasoning_content = rc
self.working_messages.append(msg)
```

4. **`adapter.py:finish handler`** — Keep fingerprint self-registration as fallback (no change needed, but also stamp on the incoming messages for robustness).

### Fix 2: Mid-turn compaction type safety

**File: `agent.py` (lines 664-703)**

Wrap the mid-turn compaction with proper type conversion:

```python
if ctx_window > 0 and len(tr.working_messages) > 6:
    from .compact import compact_failed_scrapes, auto_compact
    # ... zone estimation ...
    
    if _need_loom_restart:
        # Convert loom messages → Nexus for compaction, then back to loom
        from .._loom_bridge.message import _loom_to_nexus_message, _nexus_to_loom_message
        nexus_wm = [_loom_to_nexus_message(m) for m in tr.working_messages]
        # Run compaction on Nexus messages
        nexus_wm, _n_cleaned = compact_failed_scrapes(nexus_wm)  # (if yellow/orange/red)
        nexus_wm, _mid_report = auto_compact(nexus_wm)  # (if orange/red)
        # Convert back to loom messages, preserving _reasoning_content attribute
        loom_wm = []
        for nm, orig_lm in zip(nexus_wm, tr.working_messages):
            lm = _nexus_to_loom_message(nm)
            rc = getattr(orig_lm, '_reasoning_content', None)
            if rc:
                lm._reasoning_content = rc
            loom_wm.append(lm)
        tr.working_messages = _sanitize_loom_tool_pairs(loom_wm)
        tr.reset_iteration()
        loom_iter = self._loom.run_turn_stream(
            tr.working_messages, model_id=model_id
        ).__aiter__()
        loom_task = asyncio.ensure_future(loom_iter.__anext__())
        continue
```

Actually, this is more complex than needed. Since `auto_compact` only modifies TOOL-role messages (not assistant messages), the `_reasoning_content` attribute on assistant messages is never touched. The issue is that `auto_compact` creates new Nexus `ChatMessage` objects for compacted tool messages. The simpler fix:

```python
if _need_loom_restart:
    tr.working_messages = _sanitize_loom_tool_pairs(tr.working_messages)
    # Preserve _reasoning_content on assistant messages through sanitize
    for m in tr.working_messages:
        if m.role == lt.Role.ASSISTANT:
            rc = getattr(m, '_reasoning_content', None)
            # Already preserved if same object; sanitize creates new lists but reuses objects
    tr.reset_iteration()
    ...
```

Wait — `_sanitize_loom_tool_pairs` can create new assistant messages? No, it only adds synthetic tool responses and removes orphaned tool messages. It never modifies assistant messages. So assistant message objects survive sanitization with their `_reasoning_content` attributes intact.

The actual issue is that `auto_compact` and `compact_failed_scrapes` are called with loom messages but produce Nexus messages for compacted entries. The `Role.TOOL` comparison works via duck typing (same string values), and the output list has a mix of original loom messages (uncompacted) and new Nexus messages (compacted). `_sanitize_loom_tool_pairs` then processes this mixed list.

The safest fix: convert to Nexus before compacting, compact, then convert back:

```python
if _zone in ("yellow", "orange", "red") or _zone in ("orange", "red"):
    from .._loom_bridge.message import _loom_to_nexus_message
    nexus_wm = [_loom_to_nexus_message(m) for m in tr.working_messages]
    if _zone in ("yellow", "orange", "red"):
        nexus_wm, _n_cleaned = compact_failed_scrapes(nexus_wm)
        if _n_cleaned > 0:
            _need_loom_restart = True
    if _zone in ("orange", "red"):
        nexus_wm, _mid_report = auto_compact(nexus_wm)
        if _mid_report.compacted > 0:
            _need_loom_restart = True
    if _need_loom_restart:
        from .._loom_bridge.message import _nexus_to_loom_message
        loom_wm = [_nexus_to_loom_message(m) for m in nexus_wm]
        # Re-stamp _reasoning_content from original messages
        for orig, new in zip(tr.working_messages, loom_wm):
            if getattr(orig, '_reasoning_content', None) and orig.role == lt.Role.ASSISTANT:
                new._reasoning_content = orig._reasoning_content
        tr.working_messages = loom_wm
```

### Fix 3: Retry paths preserve reasoning

**File: `agent.py`**

For the clean retry (line ~760), mid-stream retry (line ~800), and post-retry compaction (line ~826):

The key insight: `tr.working_messages` now has `_reasoning_content` stamped on assistant messages (from `materialise_assistant_if_needed`). When these messages are passed to a new loom iteration via `run_turn_stream`, the adapter's `_restore_reasoning` will find the attributes.

The retry paths that create new loom iterators should work correctly as long as:
1. `_sanitize_loom_tool_pairs` preserves `_reasoning_content` on assistant messages (it does — it never modifies assistant messages)
2. The adapter's `_reasoning_content_map` is not cleared between iterations

The `clear_adapter_map` is only called in the `finally` block at line 983. So within a turn, the map persists. But the attribute-based approach makes the map less critical.

## Implementation Order

1. Stamp `_reasoning_content` on loom messages in `agent.py` (cross-turn)
2. Stamp `_reasoning_content` in `stream_translator.py:materialise_assistant_if_needed` (within-turn)
3. Update `_restore_reasoning` in `adapter.py` to check attribute first
4. Fix mid-turn compaction type conversion in `agent.py`
5. Test with existing tests
6. Test manually with a DeepSeek session

## Files to Modify

- `agent/src/nexus/agent/_loom_bridge/adapter.py` — `_restore_reasoning` attribute-first lookup
- `agent/src/nexus/agent/loop/agent.py` — stamp reasoning on loom messages + fix compaction types
- `agent/src/nexus/agent/loop/stream_translator.py` — stamp reasoning in `materialise_assistant_if_needed`
- `agent/src/nexus/agent/loop/reasoning.py` — keep `hydrate_adapter_map` as-is (it's still useful as fallback)
