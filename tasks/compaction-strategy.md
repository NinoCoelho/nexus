# Compaction Strategy — Loom Contract + Relevance-Ranked Retention

**Created:** 2026-07-09
**Status:** In Progress
**Span:** loom (`/Users/nino/Code/loom`) + nexus (`agent/src/nexus/agent/loop/`)
**Trigger:** Review of nexus/loom compaction. Loom detects overflow and hard-stops;
all compaction lives in nexus as reactive, positional, mostly-irreversible logic.

## Problem (validated from code)

- **Loom has no compaction contract.** `_loop.py:173,288` detect overflow via
  `executor.check_overflow()` and terminate the turn with `OverflowEvent`. No
  retry, no callback. Nexus works around it by compacting *around* loom
  (pre-flight + mid-turn + post-retry), but loom can't rescue itself.
- **`overflow.py` is forked** between repos. Nexus added `KNOWN_WINDOWS` and
  dropped loom's `estimator` hook.
- **Summarization is positional + irreversible.** `summarize_older_turns`
  splits at `len - keep_recent_n=20` and LLM-collapses the head. Critical old
  context can be lost; no recovery path.
- **Tool-result shrink is reversible (`vault://` refs) but turn-summary is not**
  — inconsistent.
- No relevance ranking; no central config; ~30% estimator error near boundary.

## Design

### A. Loom compaction contract (Phase 1–2)

New module `loom/loop/compaction.py`:

```python
Zone = Literal["green", "yellow", "orange", "red"]

def classify_zone(tokens_used, context_window, *, tools_overhead=0) -> Zone: ...

@dataclass
class CompactionRequest:
    messages: list[Any]
    estimated_tokens: int
    context_window: int
    zone: Zone
    iteration: int
    attempt: int          # 1-based

@dataclass
class CompactionResult:
    messages: list[Any]
    tokens_after: int = 0
    actions: list[str] = field(default_factory=list)
    still_overflowed: bool = False

Compactor = Callable[[CompactionRequest], Awaitable[CompactionResult]]
```

- `AgentConfig` gains `compactor: Compactor | None = None` and
  `max_compaction_attempts: int = 3`.
- `TurnExecutor.resolve_overflow()` replaces the hard-stop: runs the compactor
  up to N times (re-checking each pass), returns `(messages, ov|None)`. With no
  compactor, behavior is identical to today.
- Emits `before_compaction` / `after_compaction` / `compaction_error` events.
- Zone classifier moves here from nexus `zones.py`.
- **Backward compatible**: `compactor=None` ⇒ today's semantics.

### B. Relevance-ranked retention (Phase 3)

Replace the single positional split with **multi-bucket partition**:

| Bucket | Policy |
|---|---|
| Protected | never compacted (system, last user/assistant, pinned, in-flight tool pairs) |
| Recent | keep verbatim (last K) |
| Relevant | keep verbatim (score ≥ threshold) |
| Summarize | collapse → Session Memory |
| Drop | discard (garbage, summarized no-relevance) |

**Scoring (cheap → pluggable):** recency decay · role weight · entity overlap
(file paths, `code`, URLs, `nx:` markers) · `nx:pin` markers · embeddings
(behind `knowledge` feature, Phase 4).

**Reversibility:** summarize-bucket slices persisted to
`~/.nexus/session-memory/{sid}.parts/{n}.json` with `vault://` refs injected
into the Session Memory header (mirrors `.tool-cache`).

## File-level change map

**Loom** (`/Users/nino/Code/loom`):
- NEW `src/loom/loop/compaction.py` — protocol, types, zone classifier.
- EDIT `src/loom/loop/_types.py` — `compactor`, `max_compaction_attempts`.
- EDIT `src/loom/loop/_executor.py` — `resolve_overflow()` method.
- EDIT `src/loom/loop/_loop.py` — call `resolve_overflow` in both loops.
- NEW `tests/loop/test_compaction.py`.

**Nexus** (`/Users/nino/Code/nexus`):
- EDIT `loop/overflow.py`, `loop/zones.py` — re-export from loom.
- NEW `loop/relevance.py`, `loop/retention.py`.
- NEW `loop/compactor.py` — `NexusCompactor` (orchestrates relevance →
  retention → shrink → summarize → persist).
- EDIT `loop/summarize.py` — consume `RetentionPlan`; reversibility.
- EDIT `loop/_builder.py` — wire `NexusCompactor`.
- EDIT `loop/agent.py` — drop redundant call sites now handled by loom.
- EDIT `loop/compact.py` — keep primitives; move `compact_and_summarize` into
  the new compactor.

## Phasing

- [x] **Phase 1 — Loom primitive** (additive, low risk). — committed `7ea558e` on loom main.
- [x] **Phase 2 — De-dup + nexus adopts contract.** — committed `4748dbd` on nexus main.
- [x] **Phase 3 — Relevance-ranked retention.**
- [~] **Phase 4 — Optional**: embeddings ✓, real tokenizers, tool-schema
  filtering, metrics.

## Verification
- `uv run pytest` + `uv run ruff check` green in **both** repos per phase.
- Unit: `classify_zone`; `resolve_overflow` (mock compactor: fail→success,
  exhausted→`OverflowEvent`, no-compactor⇒unchanged); relevance scoring;
  retention bucket invariants.
- Integration: a CSV/scrape-heavy session that hard-stopped now recovers.
- Regression golden set: Protected/Recent buckets byte-identical pre/post.

## Risks & mitigations
- Framework contract change → mitigated: `compactor` optional; default unchanged.
- Relevance drops critical info → conservative Protected bucket; reversibility;
  high thresholds initially.
- Mid-turn compaction breaks prefix cache → compactor only at iteration
  boundaries; retention favors head-trim.
- Entity extraction cost → regex-first; embeddings lazy + feature-gated.

## Lessons
_To be filled after implementation._

## Results — Phase 1 & 2

**Phase 1 (loom, `7ea558e` on main):** Added the compaction contract —
`CompactionRequest`/`Result`, `classify_zone` (ported from nexus), and
`TurnExecutor.resolve_overflow()` which runs the compactor up to N times with
authoritative re-checks. Both `run_turn` and `run_turn_stream` now call it;
the hard-stop `OverflowEvent` fires only when compaction is absent or fails.
Backward compatible (`compactor=None` ⇒ old behavior). 693 loom tests pass.

**Phase 2 (nexus):**
- `zones.py` → thin re-export from loom (de-dup; identical impl).
- NEW `loop/compactor.py` — `NexusCompactor` bridges loom's contract to the
  existing `compact_and_summarize`, with attempt-based escalation
  (1=tools_only, 2=auto, 3=aggressive) and reasoning-content round-tripping
  across the loom↔nexus message conversion. Wired in `_builder.py`.
- Removed the redundant pre-flight overflow rescue in `agent.py` (it
  short-circuited before loom, making the new compactor dead code). Failure
  path now flows through loom's `OverflowEvent` → the existing in-loop
  `context_overflow` handler → SSE error.
- Fixed a fallback-window parity gap: `_model_context_window` now returns the
  32K default (not 0) for unknown models so loom's detection runs, matching
  the old nexus pre-flight.
- ruff clean; 50/50 loop/overflow tests pass. 87 unrelated pre-existing
  failures (vault/dashboard/datatable/qwen-live/server-sse) confirmed
  environmental via `git stash` isolation — they fail identically without
  these changes.

**Remaining redundancy (deferred, noted for Phase 2 follow-up):**
mid-turn zone-based `auto_compact` (`agent.py:700+`) and post-retry
compaction (`agent.py:860+`) overlap with loom's per-iteration check; both
are left in place as they carry orthogonal concerns (scrape-garbage removal,
retry recovery). The `overflow.py` full de-dup (estimator naming, frozen
`OverflowCheck`, KNOWN_WINDOWS registry) was deferred as lower-value/higher-
risk than the zones re-export.

## Results — Phase 3

**Relevance-ranked retention** replaces the fixed positional split
("keep last 20, summarize the rest") with a five-bucket partition scored by
relevance to the current turn. The single biggest quality lever from the
review — a critical old message (a key file, a stated constraint) is no
longer dropped just for being old.

- NEW `loop/relevance.py` — `score_messages()` with additive, documented
  factors: exponential recency decay, role weighting (user/assistant sticky,
  tool compressible), query entity-overlap (regex: backtick code spans,
  slashy paths, URLs, `nx:` markers), and a hard `nx:pin` → max. No
  embeddings (those are Phase 4 behind the `knowledge` feature).
- NEW `loop/retention.py` — `partition()` into protected / recent /
  relevant / summarize / drop. Tool-pair integrity is guaranteed by grouping
  each assistant(tool_calls) + its tool results into an **atomic compaction
  unit** — a kept assistant never loses its tool result, and a summarized tool
  result takes its caller into the prose summary. Scrape-noise tool results
  route to `drop`.
- `loop/summarize.py` — `summarize_older_turns` now consumes a
  `RetentionPlan` instead of a positional split; the superseded old summary
  SYSTEM message is dropped from survivors; the return contract is preserved.
- NEW `persist_summary_part()` — every summarization journals the verbatim
  collapsed messages to `~/.nexus/session-memory/.parts/{sid}.jsonl` so
  nothing is ever truly lost (reversibility parity with the `.tool-cache`
  tool-shrink path).
- 36 new tests (9 relevance, 13 retention, 7 summarize, 7 reversibility) —
  all property/invariant-based, robust to weight re-tuning. ruff clean.
