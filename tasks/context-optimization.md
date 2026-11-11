# Context Management Optimization

**Created:** 2026-05-20
**Status:** In Progress
**Trigger:** Context management report showing 172K-token prompts, 184s max latency, 30s avg latency.

## Problem

Nexus agent sends 100K-172K token prompts. Root cause: `web_scrape` tool returns up to 100KB of text per page (even after HTML stripping), and the agent scrapes 5-8 pages per research turn. Existing compaction/summarization runs *after* the damage is done.

Example: 8 scrapes × 100KB = 800KB text ≈ 200K tokens → single 172K prompt request.

## Changes

### Change 1 — Reduce `max_content_bytes` default to 20KB

| | Before | After |
|---|---|---|
| Loom default | `102400` (100KB) | `20480` (20KB) |
| Nexus config default | `102400` | `20480` |

**Files changed:**
- `loom/src/loom/scrape/scrapling.py` — constructor default
- `nexus/agent/src/nexus/config_schema.py` — `ScrapeConfig.max_content_bytes`

**Side effects:** Existing users with explicit `max_content_bytes` in config are unaffected. Users relying on the default will see shorter scrape results. Agent may need to request longer content via the new `max_content_chars` parameter (Change 2).

**Rollback:** Revert both defaults back to `102400`.

---

### Change 2 — Expose `max_content_chars` tool parameter (Loom)

Add `max_content_chars` parameter to the `web_scrape` tool spec. Allows the agent to request less (or more) content per call. Default comes from config.

**Files changed:**
- `loom/src/loom/tools/scrape.py` — ToolSpec + invoke passthrough
- `loom/src/loom/scrape/scrapling.py` — accept per-call override

**Side effects:** Tool spec changes are backward-compatible (new optional parameter). No existing tool calls break.

**Rollback:** Remove `max_content_chars` from ToolSpec and provider.

---

### Change 3 — Update research skill instructions (Nexus)

Constrain the research skills to scrape fewer pages with explicit limits.

**Files changed:**
- `skills/web-research/SKILL.md`
- `skills/deep-research/SKILL.md`

**Side effects:** Agent behavior changes — will scrape fewer pages by default. Research may be less thorough on first pass but can be deepened via follow-up.

**Rollback:** Revert skill files to previous versions.

---

### Change 4 — Per-turn tool-result token budget: 50K (Nexus)

Structural guard: after each tool result in the agent loop, check cumulative tool-result tokens. If exceeding 50K, inject a system message telling the agent to synthesize with what it has.

**Files changed:**
- `agent/src/nexus/agent/loop/budget.py` — NEW: cumulative token estimator + threshold check
- `agent/src/nexus/agent/loop/agent.py` — integrate budget check after each tool result
- `agent/src/nexus/config_schema.py` — add `tool_budget_tokens` config field

**Side effects:** Agent may be interrupted mid-tool-loop on heavy research turns. It will be told to synthesize, not errored out. This is intentional — it prevents context explosion.

**Rollback:** Remove budget.py, revert agent.py changes, remove config field.

---

### Change 5 — Trafilatura for cleaner text extraction (Loom)

Replace `sel.get_all_text()` with `trafilatura.extract()` when `output_format="text"`. Trafilatura automatically strips nav/footers/ads and returns article-quality text.

**Files changed:**
- `loom/pyproject.toml` — add `trafilatura` as optional dep under `[scrape]` extra
- `loom/src/loom/scrape/scrapling.py` — try trafilatura, fall back to scrapling `get_all_text()`

**Side effects:** Scrape text output will differ — cleaner but potentially missing some content that trafilatura considers boilerplate. If trafilatura is not installed, falls back to existing behavior.

**Rollback:** Remove trafilatura import block; scrape falls back to existing extraction.

---

## Expected Impact

| Metric | Before | After (estimated) |
|---|---|---|
| Max prompt tokens | 172K | 25-35K |
| Avg prompt tokens | 40K | 15-20K |
| P95 latency | 123s | ~30-40s |
| Scrape payload per page | up to 100KB | up to 20KB |

## Rollback Procedure

Each change is independently revertible. To fully rollback:
1. `git revert` the specific commits for each change
2. No database migrations involved
3. No config file changes required (defaults are code-level)
4. User configs with explicit values override defaults regardless

## Lessons

_To be filled after implementation and testing._

---

## Implementation Status (2026-11-11 — researched follow-up, shipped)

The follow-up research (Anthropic cookbook compaction/memory, Codex 0.153.4
experimental context management, MemGPT/Letta archival memory, RAG-MCP/
ScaleMCP tool retrieval) converged on three techniques, all now implemented
behind `[agent]` config flags (defaults ON — "lowest possible budget"):

### 1. `history_recall` — all-history recall (config: `history_recall`, default true)

- `tools/history_recall_tool.py` — agent tool with `search` (BM25 over live
  `messages_fts` + archive `parts_fts`, all sessions by default), `get`
  (verbatim message by ref), `recent` (verbatim tail of this session).
- `agent/loop/parts_index.py` — FTS5 index over the `.parts` compaction
  archives. Ref formats: `live:{sid}:{seq}` (messages table, 0-based seq) and
  `part:{sid}:{line}:{idx}` (1-based JSONL line). Archive JSONL stays the
  source of truth; the index is derived and swept alongside the files
  (`context_artifacts.py` gained an `on_removed` sweep callback).
- Wired in `server/app.py` (store injection), `session_store/schema.py`
  (`_ARCHIVE_PARTS_SCHEMA`), `summarize.persist_summary_part` (index-on-append),
  `compact.py` (elision/stub hints now say "use history_recall"),
  `prompt_builder.py` (recall-first Context-Management section).

### 2. Progressive tool discovery (config: `tool_discovery` default true,
`tool_advertisement` = "sticky"|"pure" (default sticky), `tool_discovery_sticky_max` 28,
`core_tools` list override)

- `agent/tool_discovery.py` — patches `registry.specs()` (loom calls it live
  per request) to advertise only the ~16-tool core set; registers `tool_search`
  which returns hidden tools' full JSON schemas as result text.
- Invariants: hidden tools stay dispatchable (only `specs()` is patched);
  coordinator allowlisted registries never install discovery; the prompt's
  "More tools (on demand)" catalog (stable cache zone) lists ALL non-core
  tools regardless of stickiness so sticky discoveries never rewrite the
  cached prefix.
- `overflow.TOOLS_AND_SYSTEM_OVERHEAD` floor lowered 12,000 → 7,000 (the
  measured value with discovery ≈ 13K vs ≈ 24K without).

### 3. Week-scale session lifecycle (config: `idle_compact_hours`, default 12)

- `turn_launcher.precheck_context_window` gained an **idle checkpoint**:
  resuming after ≥ idle hours with history at yellow+ zone compacts before the
  turn (Claude Code's idle compaction). `chat_stream.py` computes
  `idle_seconds` via `store.get_session_timestamps`; the Telegram gateway
  inherits it through the shared launcher.

### Telemetry

`GET /sessions/{id}/context-stats` now returns `tool_discovery` (mode, core/
hidden counts, sticky state) and `history_recall` (armed or not) blocks, and
computes `usable_context_tokens`/`reserved_tokens` from the measured overhead.

### Tests

- `tests/test_history_recall.py` (12), `tests/test_tool_discovery.py` (8),
  `tests/test_tool_discovery_integration.py` (2, real registry end-to-end).
- `tests/test_overflow_integration.py::test_preflight_uses_fallback_when_window_unconfigured`
  was rewritten: it used to pass only because the full registry's measured
  overhead (~24K) left ~0 usable tokens on the 32K fallback window, so every
  turn refused. With discovery, the compactor genuinely rescues; the test now
  pins "gate must act — rescue or clean refusal, never silently stream the blob".

### Known follow-ups

- `tool_advertisement = "pure"` is off by default; validate provider
  pass-through of undeclared tool names before flipping.
- Pre-discovery installs (full registry) can still measure overhead that
  crowds out the 32K fallback window on unknown models — discovery is the fix,
  but a hard guard on measured-vs-fallback ratio would be belt-and-braces.
