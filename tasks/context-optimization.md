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
