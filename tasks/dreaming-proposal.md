# Nexus Dreaming System — Design Proposal

## Core Concept

A **scheduled background agent** that runs during idle periods to consolidate memories, surface cross-session insights, refine skills, and rehearse scenarios. It's the agent's "subconscious" — a parallel cognitive process that enriches the waking agent's context without the agent being aware of the dream mechanics.

## Inspiration

### OpenClaw Ecosystem

OpenClaw is a personal AI assistant platform (TypeScript, by Peter Steinberger / cpojer) where "dreaming" is an ecosystem pattern implemented by multiple community plugins on top of OpenClaw's hooks, tools, cron, and memory APIs. Key implementations:

- **OpenClawDreams** (RogueCtrl) — captures conversation summaries via `agent_end` hook, encrypts into AES-256-GCM SQLite, runs reflection cycles 4-5x/day, a nightly dream cycle at 2AM that generates surreal narratives grounded to actual activity, and pushes consolidated insights back to the waking agent's memory store.
- **clawdreamer** (EESIZ) — neuroscience-literal NREM/REM phases: NREM chunks and clusters episodes, embeds into LanceDB, distills facts; REM detects conflicts between new and existing memories, classifies them, merges/consolidates, applies importance decay.
- **openclaw-inner-life** (DKistenev) — modular "nervous system" with 6 skills (emotions with half-life decay, reflect, memory, dream, chronicle, evolve), pure Bash + JSON/jq, zero dependencies.

**Key takeaway:** Dreaming is the agent's subconscious — a parallel cognitive process whose outputs surface as intuition/insight in the waking agent's memory store.

### Anthropic's Dreams API / Auto Dream

Announced May 6, 2026 at "Code with Claude." Two products:

1. **Dreams API** (Claude Managed Agents) — async background job that reads memory stores + past session transcripts, produces a reorganized memory store. Input is never mutated (immutable); output is separate and reviewable. Supports custom `instructions` to guide the pipeline.
2. **Auto Dream** (Claude Code) — local background sub-agent with 4 phases: Orientation (scan memory), Signal gathering (search transcripts for corrections/decisions/themes), Consolidation (dedupe, fix dates, remove contradictions), Prune & index (keep under 200-line limit).

**Key takeaway:** Conservative safety model (input never mutated, output is reviewable), but practical — Harvey reported ~6x task completion increase after implementing dreaming. The theoretical foundation is the "Sleep-time Compute" paper (arXiv:2504.13171) showing 5x inference cost reduction from idle-time preprocessing.

### Academic Foundations

| Paper | Contribution |
|---|---|
| **Dreamer** (Hafner, 2019) | Agent imagines trajectories in latent world model, trains policy from imagined experience |
| **Generative Agents** (Park et al., 2023) | Memory stream + periodic reflection synthesizing memories into higher-level insights |
| **Reflexion** (Shinn, 2023) | Failure-triggered verbal self-reflections stored as episodic memory |
| **Voyager** (Wang, 2023) | Ever-growing skill library distilled from experience; composable, interpretable |
| **MemGPT / Letta** (Packer, 2023) | OS-like memory hierarchy (main = RAM, archival = disk) with self-managed memory |
| **Sleep-time Compute** (Lin, 2025) | Two-agent architecture: primary for real-time, sleep-time agent for async consolidation |

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Nexus Server                     │
│                                                   │
│  ┌──────────┐     ┌──────────────┐               │
│  │ Waking   │     │ Dream        │               │
│  │ Agent    │◄────│ Scheduler    │               │
│  │ (normal) │     │ (heartbeat)  │               │
│  └──────────┘     └──────┬───────┘               │
│       │                  │                        │
│       ▼                  ▼                        │
│  ┌─────────────────────────────┐                  │
│  │        Shared State         │                  │
│  │  • Vault (~/.nexus/vault/)  │                  │
│  │  • Memory (vault/memory/)   │                  │
│  │  • Skills (~/.nexus/skills/) │                 │
│  │  • Sessions (sessions.sqlite)│                 │
│  └─────────────────────────────┘                  │
└─────────────────────────────────────────────────┘
```

## Implementation: New Heartbeat Driver

The most natural integration point is a **new heartbeat driver** at `agent/src/nexus/heartbeat_drivers/dream_trigger/`. This leverages the existing `HeartbeatScheduler` (60s tick loop), `HeartbeatStore` (persistent state), and the background dispatch pattern already proven by `calendar_trigger`.

### Files to Create

```
agent/src/nexus/
├── heartbeat_drivers/dream_trigger/
│   ├── driver.py              # dream scheduler (HeartbeatDriver)
│   └── HEARTBEAT.md           # skill description for the agent
├── dream/
│   ├── engine.py              # orchestrates the dream cycle phases
│   ├── consolidate.py         # memory consolidation phase (NREM-analog)
│   ├── insight.py             # cross-session insight extraction (REM-analog)
│   ├── skill_refine.py        # skill refinement phase
│   ├── rehearse.py            # scenario rehearsal phase
│   └── journal.py             # dream log and audit trail
```

### Config Addition

Add a `[dream]` section to `NexusConfig` in `config_schema.py`:

```toml
[dream]
enabled = true
schedule_cron = "0 3 * * *"        # 3 AM daily default
min_sessions_since_last = 5         # or 24h since last dream
model_id = ""                       # blank = use default model
context_budget_tokens = 8000        # max input to dream agent
max_output_tokens = 4000            # max dream output
max_duration_seconds = 300          # kill switch
daily_token_budget = 500000         # cumulative spend limit
write_mode = "direct"               # "direct" or "stage"
```

## Dream Cycle Phases

A single dream run executes 4 phases sequentially. Each phase produces structured output that feeds into the next. Phases can be skipped based on depth level and remaining token budget.

### Phase 1: Memory Consolidation (NREM-analog)

**What:** Reads all memory notes + recent vault files, deduplicates, resolves contradictions, converts relative dates, prunes stale entries.

**How:**
1. Load all files from `~/.nexus/vault/memory/` via `vault.list_tree()`
2. Load last N session transcripts from `SessionStore` (FTS5 search for recent, or list recent sessions)
3. **Chunked consolidation:** If total content exceeds `context_budget_tokens`, cluster by topic (using existing FTS/tag indexes) and consolidate per-cluster, then do a final pass to merge cross-cluster duplicates
4. LLM call with memory content: "Identify duplicates, contradictions, stale references. Output a structured merge plan."
5. LLM outputs a **JSON merge plan** validated against a strict schema (not freeform text)
6. A **deterministic executor** validates and applies the merge plan — write updated memories, delete redundant ones
7. `_memory_summary()` updates automatically via vault post-write hooks

**Merge plan schema** (enforced by executor):
```json
{
  "actions": [
    {"op": "merge", "sources": ["path/a.md", "path/b.md"], "target": "path/a.md", "reason": "..."},
    {"op": "delete", "path": "path/b.md", "reason": "..."},
    {"op": "update", "path": "path/c.md", "changes": {"replace_dates": {"yesterday": "2026-05-09"}}},
    {"op": "flag_contradiction", "paths": ["path/x.md", "path/y.md"], "resolution": "keep_recent"}
  ]
}
```

**Anthropic-inspired details:**
- Convert relative dates ("yesterday", "last week") to absolute dates
- Remove references to deleted vault files
- Merge duplicate entries about the same topic
- Flag contradictory entries and resolve with recency bias

**Always runs** — highest ROI, cheapest phase. Even when budget is depleted, consolidation executes.

### Phase 2: Insight Extraction (REM-analog)

**What:** Analyzes cross-session patterns, recurring themes, user preferences, workflow habits. Generates new "insight" memory notes.

**How:**
1. Search session history for patterns: repeated tool calls, recurring errors, frequently accessed vault files
2. LLM call: "Given these N sessions, what patterns emerge? What does the user care about? What workflows repeat?"
3. Write insight notes to `vault/memory/dream-insights/` with tags `dream-insight`, `auto-generated`
4. Publish `dream.insight` event to event_bus

**OpenClaw-inspired details:**
- Track "explored territory" (hash of insight content) to avoid generating the same insight twice
- Insights tagged with confidence levels (high/medium/low)
- Low-confidence insights are candidates for user confirmation in the next waking session

**Insight lifecycle (importance decay):**
- Each insight has a `created_at` timestamp and a `last_referenced_at` field
- Insights older than 30 days with no waking-agent reference get soft-deleted
- This self-regulates volume naturally — no hard per-dream cap needed
- Waking agent references tracked via the existing FTS index (search for insight path in session transcripts)

**Runs when** budget remaining > 30%. Skipped if budget is low.

### Phase 3: Skill Refinement

**What:** Reviews agent-authored skills, identifies patterns where the agent repeatedly does something that could be a skill, and creates/updates skills.

**How:**
1. Scan `~/.nexus/skills/` for `agent`-trust-tier skills
2. Analyze session transcripts for repeated multi-step workflows not covered by existing skills
3. LLM call: "This agent repeatedly does [X] across sessions. Should this be a skill? If so, draft it."
4. For existing skills: check if they've been used, if they need updating based on recent changes
5. Create new skills via `SkillManager.invoke("create", ...)` — goes through `guard.scan()` like any agent-authored skill
6. Unused agent-authored skills older than 30 days: flag for review, optionally archive

**Micro-skill prevention:**
- Pattern must appear in ≥3 distinct sessions across ≥2 different days
- No embedding similarity computation needed — simple session/date deduplication
- LLM still decides whether the pattern warrants a skill, but the candidate pool is pre-filtered

**Skill versioning:**
- `.meta.json` tracks `last_dreamed_at` and `dream_version`
- If a human edits the skill after a dream update, mark `manually_edited_since_dream: true`
- Dream will not overwrite a manually-edited skill without flagging it in the journal for user review

**Runs when** budget remaining > 60% AND depth = "deep". Behind a config flag.

### Phase 4: Scenario Rehearsal

**What:** Simulates likely future interactions based on patterns, pre-computes useful context, and caches it for the waking agent.

**How:**
1. Based on insights from Phase 2, identify likely next queries/tasks
2. For each: run a mini agent turn (background session, hidden) to pre-compute useful artifacts
3. Cache results as "pre-computed context" notes in `vault/memory/precomputed/`
4. The waking agent's `prompt_builder` can optionally include a "dream hints" section with recently pre-computed context

**Sleep-time compute insight:** The more predictable the user's workflow, the more valuable this phase. A user who works on the same project daily gets much more benefit than one with random tasks.

**Speculative content handling:**
- Pre-computed notes are tagged with `confidence: speculative`
- Auto-expire after 24h if not referenced by a waking session
- Not limited to structural prep — full speculative content is allowed, but clearly labeled
- The value of rehearsal is precisely in pre-computing non-trivial artifacts

**Runs when** budget remaining > 60% AND depth = "deep". Behind a config flag. Most expensive, lowest guaranteed ROI.

## Enhancements Beyond OpenClaw/Anthropic

### 1. Dream Journal with Diff View

Every dream writes a structured journal entry to `vault/dreams/YYYY-MM-DD.md`:
- What was consolidated (before/after diffs on memory)
- What insights were generated (with confidence)
- What skills were created/updated
- Token spend and duration per phase
- Any issues flagged for user review
- **Fully reconstructible** — dream sessions are hidden but the journal contains enough detail to reconstruct the entire dream run without needing to find the hidden session

The UI gets a "Dream Journal" view (new view or vault filter) showing dream history with expandable diffs.

### 2. Progressive Dream Depth

Not every dream needs to be a full 4-phase cycle. Implement depth levels:

| Level | Frequency | Phases | Cost |
|---|---|---|---|
| **Light** | Every 6h or 5 sessions | Consolidation only | Low |
| **Medium** | Daily | Consolidation + insight extraction | Medium |
| **Deep** | Weekly | All 4 phases including skill refinement and rehearsal | High |

The scheduler picks depth based on time since last dream and accumulated session count.

### 3. Waking Realization Channel

After a dream, the agent surfaces relevant insights in the next conversation:
- `_memory_summary()` in `prompt_builder.py` includes a "Recent dream insights" subsection
- The agent naturally says things like "I noticed you've been working on X a lot — I've updated my notes"
- This is the "I had a dream last night..." moment from OpenClaw, but naturalistic

### 4. Dream-Informed Skill Suggestions

When the dream identifies a repeated pattern, it doesn't just auto-create a skill. It:
1. Writes a "skill suggestion" note to `vault/dreams/suggestions/`
2. In the next waking session, if the user's task matches the pattern, the agent mentions the suggestion
3. User can approve: "Yes, make that a skill" or dismiss

This keeps the aggressive write model but adds a soft confirmation layer for high-impact changes.

### 5. Cross-Vault Pattern Detection

Since Nexus has a rich vault with backlinks, tags, and graph:
- Dream can detect emerging topic clusters across vault files
- Auto-tag files that relate to the same project/concept
- Suggest new vault organization structures
- Update the link graph to reflect discovered relationships

### 6. Dream Trigger Flexibility

Beyond the cron schedule, dreams can also be triggered by:
- **Session threshold**: "Run a light dream after every 10 sessions"
- **Vault activity**: "Run a dream when 20+ vault files have changed since last dream"
- **Agent request**: The waking agent calls a `trigger_dream` tool during conversation
- **Manual**: User triggers from CLI (`uv run nexus dream`) or UI

### 7. Graceful Cost Degradation

Track cumulative dream token spend against the daily budget. Rather than a hard cap, degrade progressively:

| Remaining Budget | Phases Enabled |
|---|---|
| > 60% | All phases (depth permitting) |
| 30-60% | Consolidation + insight extraction only |
| < 30% | Consolidation only |
| 0% | No dream runs |

Consolidation always runs unless budget is fully exhausted — cheapest, highest ROI.

### 8. Dream Feedback Loop

After each dream, record what changed and measure downstream impact:
- Did the agent's next session use fewer tokens? (efficiency gain)
- Did memory lookups return more relevant results? (consolidation quality)
- Did the user explicitly accept/reject dream-generated skills? (skill quality)

This data feeds back into the dream agent's own prompts, creating a self-improving loop.

**Persistent metrics:** `dream_runs` table in `dream_state.sqlite` with per-run stats (tokens_in, tokens_out, duration_ms, phases_run, insights_generated, memories_merged, skills_created). Enables dashboarding and regression detection over time.

## Operational Safety

### Concurrency Lock

If the user triggers a manual dream while a scheduled dream is running, a lock prevents overlap. Managed via `HeartbeatStore` state:

```
state: idle | running
current_run_id: uuid | null
current_phase: string | null
started_at: timestamp | null
```

Manual trigger attempts while `state=running` return a "dream already in progress" status. The heartbeat driver skips ticks while `state=running`.

### Waking Session Priority

If the dream agent is running and the user starts a conversation, the dream should pause or yield:
- The engine exposes a `request_pause()` method
- Completes the current phase, saves intermediate state, then releases the agent instance
- The heartbeat driver resumes the dream on the next tick by detecting saved intermediate state
- This prevents resource contention between the dream agent and the waking agent

### Write Mode Toggle

The `write_mode` config option provides an escape hatch:

- **`direct`** (default) — dream writes to memory, vault, and skills directly through existing guard rails
- **`stage`** — dream writes proposed changes to `vault/dreams/staged/YYYY-MM-DD/` for manual review before merging

In `stage` mode, the journal entry includes a "pending changes" section with instructions for the user to review and apply. The waking agent can also prompt: "I have some dream suggestions to review — want to see them?"

## Integration Points with Existing Code

| Component | Change | File |
|---|---|---|
| `prompt_builder.py` | Add "dream insights" subsection to system prompt | `agent/src/nexus/agent/prompt_builder.py` |
| `config_schema.py` | Add `[dream]` config section | `agent/src/nexus/config_schema.py` |
| `app.py` lifespan | Register dream heartbeat driver | `agent/src/nexus/server/app.py` |
| `session_store` | Dream creates hidden sessions with `context="dream:..."` | existing API |
| `vault_dispatch.py` | Adapt `_dispatch_impl()` for dream dispatch | `agent/src/nexus/server/routes/vault_dispatch.py` |
| `event_bus.py` | Add dream lifecycle events | `agent/src/nexus/server/event_bus.py` |
| `main.py` | Add `nexus dream` CLI command | `agent/src/nexus/main.py` |
| UI | Dream journal view, dream status indicator, manual trigger | `ui/src/` |

## Key Design Decisions

- **Dream agent = fresh `Agent()` instance** with same tools as waking agent, created per dream cycle
- **Hidden sessions** — dreams don't pollute the user's session list
- **All writes go through existing vault/skill guard rails** — no special bypass
- **State persisted in `~/.nexus/dream_state.sqlite`** — last dream time, session count, token budget used, explored territory hashes, per-run stats
- **Dream runs async in the server process** — uses `asyncio.create_task()` in the heartbeat tick callback
- **Kill switch** — `max_duration_seconds` hard timeout + `daily_token_budget` soft limit
- **Write model** — `direct` mode (default) writes directly; `stage` mode writes to staging area for review
- **Structured merge plans** — LLM outputs JSON, deterministic executor validates and applies
- **Graceful degradation** — progressive phase-skipping as budget depletes; consolidation always runs
- **Concurrency lock** — only one dream runs at a time; state machine in HeartbeatStore
- **Waking priority** — dream pauses when user starts a conversation, resumes on next tick

## Implementation Order (Staged)

### Stage 1: MVP (engine + consolidation + scheduling) — DONE

1. **Dream engine core** (`dream/engine.py`) — orchestration, config loading, state tracking, concurrency lock, budget tracking, pause/resume
2. **Memory consolidation** (`dream/consolidate.py`) — chunked consolidation, structured JSON merge plan schema, deterministic executor
3. **Dream trigger heartbeat driver** — scheduling infrastructure (HeartbeatDriver), cron + session threshold triggers
4. **Config schema** (`[dream]` section in `config_schema.py`)
5. **Dream state DB** (`~/.nexus/dream_state.sqlite`) — state machine, run history, stats table

### Stage 2: Observability + insight extraction — DONE

6. **Dream journal** (`dream/journal.py`) — structured markdown entries with diffs, fully reconstructible
7. **Insight extraction** (`dream/insight.py`) — cross-session pattern analysis, explored territory dedup, importance decay
8. **Prompt builder integration** — `_memory_summary()` includes "Recent dream insights" subsection (waking realization channel)
9. **Event bus integration** — `dream.started`, `dream.phase_completed`, `dream.completed`, `dream.insight` events

### Stage 3: Advanced phases — DONE

10. **Skill refinement** (`dream/skill_refine.py`) — micro-skill prevention (≥3 sessions, ≥2 days), skill versioning via `.meta.json`, dream-informed suggestions
11. **Scenario rehearsal** (`dream/rehearse.py`) — speculative pre-computation with auto-expiry, confidence tagging
12. **CLI command** — `uv run nexus dream` for manual trigger

### Stage 4: UI — DONE

13. **Dream view** — dedicated sidebar view with 4 tabs: Status (budget bar, last run, depth trigger), Journal (date list + markdown preview), Suggestions (accept/dismiss skill drafts), History (all run records)
14. **Dream status indicator** — running/idle pill in Dream view header
15. **Manual trigger button** — depth selector (light/medium/deep) in Status tab
16. **Staged changes review** — Suggestions tab shows skill drafts with accept/dismiss actions
17. **Backend API** — `GET /dream/status`, `POST /dream/trigger`, `GET /dream/journal`, `GET /dream/journal/{date}`, `GET /dream/suggestions`, `POST /dream/suggestions/{filename}/accept`, `DELETE /dream/suggestions/{filename}`, `GET /dream/runs`

### Test Coverage

- **66 tests** across 6 test files, all passing
- `test_dream_state.py` — 16 tests (state store, concurrency lock, budget, explored territory)
- `test_dream_consolidate.py` — 17 tests (JSON extraction, chunking, action execution, mock LLM)
- `test_dream_journal.py` — 4 tests (create, append, consolidation result, error)
- `test_dream_insight.py` — 10 tests (hash, context building, memory loading, mock LLM, dedup)
- `test_dream_skill_refine.py` — 10 tests (hash, context, skill loading, mock LLM, duplicate skip, existing-skill skip)
- `test_dream_rehearse.py` — 9 tests (context, insight loading, mock LLM, failure handling)
