# Context Management & Error Resilience — Implementation Plan

## Problem Statement

Long-running sessions accumulate hundreds of messages (especially tool results),
causing empty responses, 429 rate-limit exhaustion, and unrecoverable states.
The current system has:
- No auto-compaction of tool outputs
- No context-window enforcement when `ctx_window=0`
- No structured error logging
- No "pause and resume" for rate limits
- No agent-level awareness of context budget
- No auto-fork safety net

Evidence: `sess=615f5…` reached 148 messages (78 tool), `ctx_window=0`,
model returned `empty_response` with `in_tokens=0`.

---

## Phase 1: LLM Error Log Table

Track errors so we can monitor failure patterns.

### Schema (`session_store/schema.py`)

```sql
CREATE TABLE IF NOT EXISTS llm_errors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    error_type   TEXT NOT NULL,          -- rate_limit, timeout, context_overflow, empty_response, auth, server_error, transport
    status_code  INTEGER,                -- HTTP status (429, 500, etc.)
    provider     TEXT,                   -- provider name
    model        TEXT,                   -- model id
    message      TEXT,                   -- error detail
    retryable    INTEGER NOT NULL DEFAULT 0,
    retry_attempt INTEGER,               -- which auto-retry attempt (0 = not a retry)
    tokens_in    INTEGER,                -- estimated input tokens at time of error
    context_window INTEGER              -- configured context window (0 = unknown)
);
CREATE INDEX IF NOT EXISTS llm_errors_session_idx ON llm_errors(session_id);
CREATE INDEX IF NOT EXISTS llm_errors_type_time_idx ON llm_errors(error_type, created_at DESC);
CREATE INDEX IF NOT EXISTS llm_errors_created_idx ON llm_errors(created_at DESC);
```

### Wire points

- [ ] Add `_LLM_ERRORS_SCHEMA` to `schema.py`, execute in `init_fts()`
- [ ] Add `log_error(session_id, error_type, **kwargs)` method to `SessionStore`
- [ ] Wire into `agent.py` mid-stream error handler (line ~803-810) — log after auto-retry exhausted
- [ ] Wire into `chat_stream.py` exception catch (line ~427-482) — log `LLMTransportError`
- [ ] Wire into `agent.py` empty-reply handler — log `empty_response` with token counts
- [ ] Wire into `overflow.py` — log `context_overflow` with estimated tokens
- [ ] Add `GET /sessions/errors` endpoint (optional, for future dashboard)

### Files changed

- `agent/src/nexus/server/session_store/schema.py` — add table + index DDL
- `agent/src/nexus/server/session_store/store.py` — add `log_error()` method
- `agent/src/nexus/agent/loop/agent.py` — call `log_error()` at error points
- `agent/src/nexus/server/routes/chat_stream.py` — call `log_error()` at exception points
- `agent/src/nexus/server/routes/sessions.py` — add errors endpoint (optional)

---

## Phase 2: Fallback Context Protection (ctx_window=0)

When `ctx_window` is not configured, apply heuristic limits instead of
disabling all checks entirely. This prevents the "148 messages, zero protection"
scenario.

### Changes to `overflow.py`

- [ ] Add `_DEFAULT_FALLBACK_WINDOW = 32_000` (tokens) — conservative default for unknown models
- [ ] In `check_overflow()`: when `context_window <= 0`, use `_DEFAULT_FALLBACK_WINDOW` instead of returning "safe"
- [ ] Add `_DEFAULT_MAX_MESSAGES = 80` — hard cap on total messages regardless of token estimate
- [ ] New function `check_message_count(messages, limit=_DEFAULT_MAX_MESSAGES)` → returns bool
- [ ] When message count exceeds limit, return `OverflowCheck(True, ...)` with `detail="Message count exceeds safety limit"`

### Changes to `agent.py`

- [ ] Before entering loom loop, call both `check_overflow()` AND `check_message_count()`
- [ ] When message-count overflow detected, auto-trigger compact + summarize (Phase 3) instead of refusing the turn

### Files changed

- `agent/src/nexus/agent/loop/overflow.py` — fallback window + message count check
- `agent/src/nexus/agent/loop/agent.py` — wire new checks into pre-flight

---

## Phase 3: Auto-Compaction (Yellow Zone — 60-80%)

Automatically compact tool outputs when context enters the yellow zone.
This is the highest-impact layer — tool results are the #1 context consumer.

### Zone thresholds (new module: `agent/src/nexus/agent/loop/zones.py`)

```python
def classify_zone(tokens_used: int, context_window: int) -> Literal["green", "yellow", "orange", "red"]:
    pct = tokens_used / context_window if context_window > 0 else 0
    if pct < 0.60: return "green"
    if pct < 0.80: return "yellow"
    if pct < 0.90: return "orange"
    return "red"
```

### Changes to `compact.py`

- [ ] Lower default `threshold_bytes` from 32KB to 8KB for auto-compaction
- [ ] Add `auto_compact(history, context_window)` — runs compact_history with aggressive thresholds when in yellow zone
- [ ] Add vault persistence: save full tool result to `~/.nexus/vault/.tool-cache/{hash}.json` before truncating
- [ ] Truncated messages get appended: `\n\n[Full result saved to vault://.tool-cache/{hash}.json]`
- [ ] Mark compacted messages with `nx:compacted` prefix (already exists)

### Auto-trigger in `agent.py`

- [ ] At the start of `run_turn_stream`, after stripping dead placeholders:
  1. Estimate tokens with `estimate_tokens()`
  2. Classify zone
  3. If yellow+: run `auto_compact()` on history
  4. Re-estimate after compaction
  5. If still orange+: proceed to Phase 4 (summarization)

### Files changed

- `agent/src/nexus/agent/loop/zones.py` — NEW file, zone classification
- `agent/src/nexus/agent/loop/compact.py` — auto-compact + vault persistence
- `agent/src/nexus/agent/loop/agent.py` — auto-trigger compaction
- `agent/src/nexus/agent/loop/overflow.py` — import zone classification

---

## Phase 4: Sliding-Window Summarization (Orange Zone — 80-90%)

When compaction alone isn't enough, summarize older turns into a structured
memory block. Recent turns (last N messages) stay verbatim.

### New module: `agent/src/nexus/agent/loop/summarize.py`

- [ ] Structured summary schema:
  ```
  ## Session Memory (auto-generated summary)
  - **Goals:** [current objectives from recent user messages]
  - **Decisions:** [key choices + rationale from assistant messages]
  - **Entities:** [files, APIs, tools referenced — current state]
  - **Open TODOs:** [pending items from kanban/memory]
  - **Last state:** [what was happening before this summary]
  ```
- [ ] `summarize_older_turns(history, keep_recent_n=20)` — splits history into old + recent
- [ ] Old segment gets summarized via a quick LLM call (using the same provider, but with max_output_tokens=1024)
- [ ] Returns `[SYSTEM: summary] + recent_messages`
- [ ] LLM call uses a dedicated system prompt: "You are a session memory compressor. Given the following conversation, produce a structured summary following this schema: ..."
- [ ] Rate-limit the summarization: track last summarization timestamp, don't re-summarize if < 5 turns since last one

### Integration in `agent.py`

- [ ] After auto-compaction, re-check zone
- [ ] If orange: call `summarize_older_turns()`, prepend summary as a SYSTEM message
- [ ] The summary message is marked with metadata so it's not counted as user/assistant history
- [ ] Log summarization event to `llm_errors` table (type=`summarization`, with tokens before/after)

### Cost/latency considerations

- Summarization LLM call: ~1024 output tokens, ~2-5s latency
- Only triggers at orange zone (80-90%), not on every turn
- Summary is cached until enough new turns accumulate

### Files changed

- `agent/src/nexus/agent/loop/summarize.py` — NEW file
- `agent/src/nexus/agent/loop/agent.py` — wire summarization
- `agent/src/nexus/agent/loop/zones.py` — zone constants

---

## Phase 5: Agent Context Awareness (Planning-Phase Enforcement)

Make the agent aware of its context budget so it proactively splits work.

### New tool: `context_status` (`agent/src/nexus/tools/context_tool.py`)

```python
CONTEXT_STATUS_TOOL = ToolSpec(
    name="context_status",
    description="Check current context usage. Returns token estimate, zone (green/yellow/orange/red), and recommendations.",
    parameters={"type": "object", "properties": {}, "required": []},
)
```

Returns: `{tokens_estimated, context_window, zone, message_count, tool_message_count, recommendation}`

Recommendations:
- green: "Context is healthy. Continue as normal."
- yellow: "Consider using spawn_subagents for independent tasks."
- orange: "Use fork_session to start a new phase, or spawn_subagents for remaining work."
- red: "Immediately fork or summarize. Context is critically full."

### New tool: `fork_session` (`agent/src/nexus/tools/fork_tool.py`)

```python
FORK_SESSION_TOOL = ToolSpec(
    name="fork_session",
    description="Create a new session with a summary of the current conversation. Use this when starting a new phase of work or when context is getting large.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title for the new session"},
            "summary_focus": {"type": "string", "description": "What to emphasize in the summary (e.g., 'the API design decisions', 'the remaining TODOs')"},
            "copy_files": {"type": "array", "items": {"type": "string"}, "description": "Vault paths to reference in the new session"},
        },
        "required": ["title"],
    },
)
```

Behavior:
1. Generate a structured summary of current session (reuse `summarize_older_turns` logic)
2. Create a child session via `store.create_child(parent_session_id=..., hidden=False)`
3. Set the child's `context` to the summary
4. Inject summary as the first SYSTEM message in child's history
5. Return child session_id + summary to the agent
6. The agent can then tell the user: "I've started a new session for the next phase: [link]"

### System prompt additions (`prompt_builder.py`)

Add to the IDENTITY block:

```
## Context Management Guidelines

You have access to tools that help manage conversation context:
- `context_status`: Check how full your context window is and get recommendations.
- `fork_session`: Start a new session with a summary when entering a new phase.
- `spawn_subagents`: Run independent subtasks in isolated sessions.

### When to use these tools:
- **Check context_status** before starting complex multi-step operations.
- **Fork sessions** at natural boundaries: new feature, new file, new debugging target.
- **Spawn sub-agents** for independent parallel work (research, analysis, file operations).
- **Never** let context grow past orange zone without taking action.

### Planning rule:
When planning a task with 3+ steps, explicitly consider whether to:
1. Spawn sub-agents for independent steps
2. Fork before starting a new phase
3. Use vault_write to persist intermediate results instead of keeping them in context
```

### Files changed

- `agent/src/nexus/tools/context_tool.py` — NEW file
- `agent/src/nexus/tools/fork_tool.py` — NEW file
- `agent/src/nexus/agent/_loom_bridge/registry.py` — register new tools
- `agent/src/nexus/agent/prompt_builder.py` — add context management guidelines

---

## Phase 6: 429 "Continue Later" Flow

When rate limits exhaust auto-retries, save the turn state and let the user
resume after a cooldown.

### Schema (`session_store/schema.py`)

```sql
CREATE TABLE IF NOT EXISTS paused_turns (
    session_id          TEXT PRIMARY KEY,
    user_message        TEXT NOT NULL,
    working_messages    TEXT NOT NULL,   -- JSON-serialized ChatMessage list
    paused_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retry_after         TEXT NOT NULL,   -- ISO timestamp: earliest safe retry time
    status              TEXT NOT NULL DEFAULT 'paused',  -- paused | resumed | expired
    model_id            TEXT,
    error_detail        TEXT,
    resume_count        INTEGER NOT NULL DEFAULT 0
);
```

### Backend flow

1. When 429 exhausts 3 retries in `agent.py`:
   - Save `working_messages` (accumulated tool results from this turn) to `paused_turns`
   - Estimate `retry_after`: parse `Retry-After` header from the 429 response, or default to 60s from now
   - Yield new event: `{type: "paused_for_cooldown", retry_after: <ISO timestamp>, estimated_seconds: 60}`
   - Persist partial turn with `[rate_limited]` prefix (new placeholder type)

2. New endpoint: `POST /sessions/{id}/resume-paused`
   - Load `paused_turns` row for session
   - If `retry_after` is in the future: return 425 Too Early with remaining seconds
   - If past: restore working_messages, re-enter agent loop from that point
   - Increment `resume_count`, set status to `resumed`

3. `paused_turns` cleanup: on session load, check for expired paused turns (> 1 hour old),
   set status to `expired`

### Frontend changes

- [ ] New SSE event type: `paused_for_cooldown` in `chat.ts`
- [ ] New message partial status: `rate_limited` in `ChatView/index.tsx`
- [ ] New `PARTIAL_CAN_CONTINUE` entry: `rate_limited: true`
- [ ] New banner component: shows cooldown countdown timer + "Resume" button
- [ ] "Resume" calls `POST /sessions/{id}/resume-paused`
- [ ] If response is 425: show "Still cooling down, wait X more seconds" toast
- [ ] If success: stream resumes into existing assistant bubble (in-place)

### Files changed

- `agent/src/nexus/server/session_store/schema.py` — add table
- `agent/src/nexus/server/session_store/store.py` — add `pause_turn()` / `load_paused()` / `resume_paused()`
- `agent/src/nexus/agent/loop/agent.py` — save state on exhausted 429, yield `paused_for_cooldown`
- `agent/src/nexus/server/routes/chat_stream.py` — forward `paused_for_cooldown` SSE event
- `agent/src/nexus/server/routes/sessions.py` — add `POST /sessions/{id}/resume-paused`
- `ui/src/api/chat.ts` — parse `paused_for_cooldown` event
- `ui/src/components/ChatView/index.tsx` — add `rate_limited` partial status
- `ui/src/components/ChatView/partialTurn.tsx` — add countdown + resume button
- `ui/src/hooks/streamEventHandlers.ts` — handle `paused_for_cooldown` event
- `ui/src/hooks/useChatSession.ts` — wire resume-paused API call

---

## Phase 7: Auto-Fork Safety Net (Red Zone — >90%)

When even summarization isn't enough, auto-fork as a last resort.

### Changes to `agent.py`

- [ ] After summarization, re-check zone
- [ ] If red and summarization already happened this turn: auto-fork
  1. Create child session with `store.create_child(hidden=False)`
  2. Generate summary (reuse summarize logic)
  3. Set child's context = summary
  4. Add a final assistant message to parent: `[auto_forked] This session reached context limits. Continuing in new session: <session_id>`
  5. Yield `{type: "auto_fork", new_session_id: child.id, summary: summary_text}`
  6. UI auto-switches to new session

### Frontend

- [ ] New SSE event: `auto_fork` with `new_session_id` + `summary`
- [ ] Auto-switch to new session on receipt
- [ ] Show toast: "Continued from previous session due to context limits"

### Files changed

- `agent/src/nexus/agent/loop/agent.py` — auto-fork logic
- `ui/src/api/chat.ts` — parse `auto_fork` event
- `ui/src/hooks/useChatSession.ts` — auto-switch session
- `ui/src/hooks/streamEventHandlers.ts` — handle `auto_fork` event

---

## Execution Order

| Phase | Depends on | Est. effort | Impact | Status |
|-------|-----------|-------------|--------|--------|
| 1: Error log table | — | Small | Observability | **Done** |
| 2: Fallback ctx protection | — | Small | Prevents the exact failure shown in the log | **Done** |
| 3: Auto-compaction | Phase 2 | Medium | Highest token savings | **Done** |
| 4: Summarization | Phase 3 | Large | Long-session resilience | **Done** |
| 5: Agent awareness | Phase 2 | Medium | Preventive behavior | **Done** |
| 6: 429 Continue Later | Phase 1 | Medium | Rate-limit resilience | **Done** |
| 7: Auto-fork | Phase 4 | Medium | Safety net | **Done** |

All phases implemented in 4 commits on `feat/skill-wizard`:

1. `4f06f52` — Phases 1-3 + 5
2. `18a9ec7` — Phases 4 + 6
3. `90c1561` — Phase 7
4. `4b3d799` — Bugfixes: ContextVar wiring for tools, `[rate_limited]` strip

---

## Review & Verification

After each phase:
- [ ] Run `uv run pytest` — all existing tests pass
- [ ] Run `uv run ruff check src tests` — no lint errors
- [ ] Run `npm run build` (from `ui/`) — no type errors
- [ ] Manual test: create a session with 80+ messages, verify zone triggers
- [ ] Manual test: trigger a 429 (or simulate), verify continue-later flow
- [ ] Check `llm_errors` table has entries after errors occur
