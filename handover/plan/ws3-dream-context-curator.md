# WS3 — Dreaming → context curator

**Audience: the developer reworking the background agent.** Depends on
[`ws2`](ws2-single-thread.md); the trigger fix in §3 must land *with* ws2
because single-thread breaks the current gate.

## Review: why dreaming does nothing useful

Verified by reading `dream/engine.py`, `dream/consolidate.py`,
`dream/insight.py` and `heartbeat_drivers/dream_trigger/driver.py`.

1. **Consolidation — the phase that always runs — reads only
   `vault/memory/*.md`** (`consolidate.py:71`). That directory is populated
   solely by `memory_tool`. If the agent rarely calls it, the phase logs
   *"no memory files found, skipping"* and returns an empty result. It never
   looks at the vault proper, and **never at conversations at all**. This alone
   explains most of it.
2. **Every output is write-only.** Insights land in `dream_insights_dir()` with
   a 30-day expiry; skill suggestions are *staged for review*; the journal is a
   vault note — and `skills_created=0` is hard-coded in `finish_run`. Nothing
   re-enters the system prompt or the agent's live context, so even a fully
   successful dream has **no observable effect on behaviour**. This is the
   deepest problem: the feature cannot be felt, only read about.
3. **`[dream].schedule_cron = "0 3 * * *"` is dead config.** Declared at
   `config_schema.py:376`, read **nowhere** in `agent/src`. The driver gates on
   a hard-coded `_MIN_INTERVAL_HOURS = 24` instead. A user who sets a schedule
   gets silence.
4. **The session-count gate measures the wrong thing.**
   `_count_recent_sessions` counts **all** rows in `sessions` updated since the
   last run — including hidden subagent, workflow, calendar and dispatch
   sessions. So `min_sessions_since_last` (default 5) is satisfied by
   background churn unrelated to whether there is anything worth consolidating.

Two corrections to the obvious guesses, both of which sharpen the diagnosis:

- **It is not disabled.** `DreamConfig.enabled` defaults to **`True`**
  (`config_schema.py:375`). Dreaming has been running on every install.
- **Depth is not the problem.** The driver requires ≥24 h since the last
  finish, so `_pick_depth` returns `medium` at minimum (`deep` after a 7-day
  gap) — insight extraction normally *does* run.

So the honest summary is not "it never fires". It fires, spends from a
500k/day token budget, and produces nothing the user or the agent can act on.
That is worse than being switched off, and it is entirely fixable by giving the
phases a job with an observable output.

**It also breaks under ws2.** With one visible thread per bot, the
session-count gate would be driven entirely by hidden runs — the feature would
fire for reasons wholly unrelated to need. The trigger rewrite is not optional.

## Rework: the archivist of the single thread

Replace the four phases with three. Each has an output someone can see.

| Phase | What it does | Reuses |
|---|---|---|
| **Checkpoint** | For each bot thread above the yellow zone, or idle past a threshold, run compaction *ahead of time* so the user's next message starts in a cheap window and never pays the latency. | `compact.compact_and_summarize` verbatim. The reactive version already exists as the idle checkpoint in `precheck_context_window` (`turn_launcher.py:150-185`, `[agent].idle_compact_hours` default 12) — this makes it proactive. |
| **Archive** | Read what compaction *removed* — the `.parts/*.jsonl` archive, already FTS5-indexed by `parts_index` — and promote durable facts into the bot's memory document, **each with a `part:{sid}:{line}:{idx}` provenance ref** so nothing is asserted without a citation. | `parts_index.search`/`fetch`, `history_recall`'s ref format, ws2's thread journal. |
| **Curate** | Dedupe, merge, resolve contradictions and absolutize relative dates across the bot memory document + `vault/memory/`. | `consolidate.py`'s prompt and `_execute_actions` largely as-is — but now there is real content to curate, because Archive produced it. |

### Changes required

- **Retire** `dream/rehearse.py` (231 LOC) and the `rehearsal` phase. It
  generates hypothetical scenarios and "rehearses" them with no consumer for
  the result — the weakest cost/benefit in the codebase.
- **Park** `dream/skill_refine.py` (242 LOC). Its staged-suggestion flow needs
  the Dream UI that [`ws5`](ws5-prune-and-integrate.md) removes; revisit only
  if skill authoring gets a native surface.
- **Rewrite the trigger** (`heartbeat_drivers/dream_trigger/driver.py`): fire
  on **context pressure + idleness**, not session count.
  - Replace `min_sessions_since_last` / `_count_recent_sessions` with
    "any bot thread ≥ yellow zone" **or** "any thread idle > N hours with
    unarchived `.parts` lines".
  - **Honour `[dream].schedule_cron`** — loom's `parse_schedule` already
    accepts 5-field cron with an optional trailing IANA timezone, so the dead
    config becomes real for free.
  - **Keep** the single-flight guard (`_dream_in_flight`), the stale-run
    reconcile (`store.reconcile_stale_runs`) and the daily token budget. All
    three are sound and hard-won: the reconcile exists because one interrupted
    dream used to block all future dreams permanently, and the detached
    `asyncio.create_task` exists because an inline LLM sweep once froze **all**
    scheduling for 20+ minutes.
- **Make the output visible** where it matters: the bot's memory document and
  thread journal (ws2 §"Never losing track") replace the Dream view entirely.
  No Dream tab.
- **Keep** `dream_state.sqlite`, the budget accounting and `write_journal` —
  but `write_journal` becomes the per-bot thread ledger from ws2 rather than a
  separate dream diary.

### Interaction with WS1

There is no cache "invalidation" to orchestrate: `LRUPromptCache` is
content-addressed, so a compaction that rewrites the head simply produces a
full re-prefill on the next call. What Checkpoint buys is *when* that
re-prefill is paid — at idle, not mid-conversation. That is the ideal
arrangement, and it is the only place the two workstreams touch.

## Verification

```bash
uv run pytest tests/test_dream_consolidate.py tests/test_dream_insight.py \
              tests/test_dream_state.py tests/test_dream_journal.py \
              tests/test_dream_rehearse.py tests/test_dream_skill_refine.py
```

Expectations change substantially — `test_dream_rehearse.py` and
`test_dream_skill_refine.py` should be deleted or skipped with the phases.

New tests:

- `test_dream_checkpoint.py` — a yellow-zone thread is compacted; a green one
  is left alone; the daily budget still gates.
- `test_dream_archive.py` — every promoted fact carries a **resolvable**
  `part:{sid}:{line}:{idx}` ref (round-trip it through
  `parts_index.fetch`).
- `test_dream_trigger.py` — fires on context pressure, does **not** fire on
  hidden-session churn alone, and honours `schedule_cron`.

Manual: force a checkpoint on a yellow-zone thread, confirm the context meter
drops a zone and the thread journal gains a dated entry stamped with its token
cost. Then `history_recall` a fact from before the checkpoint and confirm the
`part:` ref resolves — that round trip is the whole promise of this workstream.
