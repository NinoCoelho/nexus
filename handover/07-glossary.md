# Glossary

Nexus has a lot of jargon, and some of it is actively misleading (a "project"
is a bot; a "dream" is a cron job). Alphabetical.

---

**ACP** — Agent Communication Protocol, an optional WebSocket bridge with
Ed25519 device auth. **Being deleted** (`tools/acp_call.py` + loom's `acp`
extra).

**Bot** — the user-facing name for what the database calls a **project**. See
*project*. Each has a name, a `@handle`, a persona, a vault folder, and
(after [`plan/ws2`](plan/ws2-single-thread.md)) one chat thread.

**Carried context** — SYSTEM messages that are *conversation state*, not
prompt: the compaction summary (`[Session Memory`) and the elision marker
(`[nx:elided]`). `_before_llm_call` strips history SYSTEM messages every
iteration, and `compact.is_carried_context` is what exempts these. Deleting
that exemption makes compaction invisible to the model.

**CDP** — Chrome DevTools Protocol. Nexus drives a real Chrome over a CDP
WebSocket; no browser engine is embedded and no Puppeteer/Playwright is used
(~15 CDP methods total).

**Compaction** — shrinking the conversation so it keeps fitting. Three stages:
tool-result shrink → LLM summarization → `hard_trim`. See *microcompaction*,
*hard_trim*.

**Context zone** — green / yellow / orange / red classification of how full the
window is (`classify_zone`). Drives the UI meter and the soft-compaction
trigger.

**Coordinator** — one designated session that acts as the user's deputy; it can
inspect other sessions (`nexus_sessions`) and send messages into them
(`session_dispatch`, approval-gated). Its handle is derived from
`[coordinator].name` — `main` is its internal id and must never be shown as a
handle. Also called the **master chat**.

**Dream** — a scheduled background agent (nightly by intent) meant to
consolidate memory and extract insights. Currently inert; being reworked into
the context curator. See [`plan/ws3`](plan/ws3-dream-context-curator.md).

**GraphRAG** — the knowledge graph over the vault: chunk → embed →
entity/relation extraction → SQLite triple store. Retrieval is **hybrid**
(vector + FTS5 fused by reciprocal rank), so keyword-only matches still
surface. The ontology lives in the vault as editable CSVs.

**hard_trim** — the LLM-free, deterministic, guaranteed-fit backstop of
compaction: stub tool results → head-truncate prose → drop whole units
oldest-first → as a last resort truncate the protected tail. Without it, a
prose-heavy history plus an unavailable summarizer was unshrinkable and the
session died permanently.

**Heartbeat** — the 60s scheduler tick (loom's `HeartbeatScheduler`) that
drives all background work: calendar triggers, coordinator sweeps, dream,
workflow cron, user heartbeats. Each **driver** runs isolated with a hard
timeout.

**history_recall** — a tool that searches *everything*, including what
compaction removed, via FTS5 over the live `messages` table and over the
`.parts` JSONL archive. Refs are `live:{sid}:{seq}` and
`part:{sid}:{line}:{idx}`.

**HITL** — human-in-the-loop. The `ask_user` and `terminal` tools block on a
user answer delivered over SSE. Kinds: `confirm`, `choice`, `text`, `form`. A
prompt can be answered **live** or, if the turn ends first, **parked** and
resumed later (that resume path returns 409 on the live endpoint — a real
source of bugs).

**Loom** — `loom-framework`, the agentic-core library Nexus is built on
(separate repo, `NinoCoelho/loom`, pulled from the `main` branch). Owns the
base `Agent`, the session store, the heartbeat scheduler, the GraphRAG engine,
and the shared types. **Its DB connection has no `row_factory`.**

**Master chat** — see *coordinator*.

**MCP** — Model Context Protocol. Nexus is both a *client* (connects to
external MCP servers, registers their tools) and a *server* (exposes its own
tools over Streamable HTTP, with a forbidden-tools list so external clients
cannot bypass HITL).

**Microcompaction** — replacing old tool results with one-line stubs that carry
a `vault://.tool-cache/<hash>.json` pointer. The full content is never lost,
just moved out of the window.

**MLX** — Apple's array framework for Apple Silicon. Nexus loads `mlx_lm`
**in-process** (not as a server) to get at its prefix KV cache. See
[`plan/ws1`](plan/ws1-mlx-provider.md).

**NBIE** — the browser subsystem's name for text-first progressive inspection:
perception escalates **text → element → vision**, and each layer is only used
when the previous one cannot answer. Telemetry tracks per-level call counts and
the read cache-hit rate.

**`nx:`** — the prefix for agent-facing identifiers embedded in vault markdown
as HTML comments: `<!-- nx:id=<uuid> -->`, `<!-- nx:session=<sid> -->`,
`<!-- nx:start=... -->`. Invisible when rendered, parseable by both sides.

**Origin** — two different things. On a *message*, the channel it came from
(`web`, `chrome-extension`, `ios`, `telegram`). On a *session*, a JSON blob
`{kind, source, run_id}` recording what created it (calendar, kanban, workflow)
— used to keep scheduler-created sessions out of chat lists.

**`page` tool vs `browser` tool** — two surfaces, one perception contract.
`browser` drives a **managed** Chrome over CDP (any session). `page` drives the
**user's real tab** via the Chrome side panel (side-panel sessions only). Both
run the same probe library, kept byte-identical by a test.

**Parked** — a HITL prompt whose turn ended before it was answered. The answer
is persisted and the turn is resumed later through a different endpoint.

**`.parts`** — the append-only JSONL archive of everything compaction removed
(`vault/.session-memory/.parts/<sid>.jsonl`). **The file is the source of
truth**; `parts_fts` only points at it.

**Progressive disclosure** — two instances of the same idea. (1) *Skills*: the
system prompt carries only names + one-sentence blurbs; full bodies load on
demand via `skill_view`. (2) *Tools*: only a ~16-tool core set is advertised;
the rest stay registered but hidden behind `tool_search`.

**Project** — the database/API name for a **bot** (`projects` table,
`sessions.project_id`). The rename to "bot" was a UX-layer decision; the
persistence backbone still says project everywhere, including Telegram
bindings and coordinator `auto_approve`.

**Prompt cache zones** — the system prompt is split by a marker into a
**stable** zone (identity, skills catalog, credential lists — byte-identical
across sessions) and a **volatile** zone (time, location, session context).
Providers place their cache breakpoint at the marker, which must never reach a
model. Note the volatile zone currently sits *before* the conversation, which
is why cross-turn prefix caching doesn't work — see
[`05-known-traps.md`](05-known-traps.md) #5.

**Queue-then-inject** — sending while a turn is running does **not** start a
parallel loop. The message is queued on the runner and injected as a user
message at the next tool-batch boundary; if the turn ends first, a follow-up
turn is chained.

**Session** — one conversation. May be visible, `hidden` (subagents, workflow
runs, scheduler fires) or `archived` (soft-hidden, restorable). After
[`plan/ws2`](plan/ws2-single-thread.md) each bot has exactly one *visible* one.

**Skill** — a markdown file (plus optional scripts and a `SKILL.toml` declaring
Python deps for a per-skill venv) that teaches the agent a capability. 50 are
bundled. The agent can author its own at runtime via `skill_manage`, gated by a
static security scan with rollback. Trust tiers: `builtin` / `user` / `agent`.

**Surface** — which client a turn came from (`web`, `chrome-extension`, `ios`,
`telegram`). Carried in a ContextVar so tools and the prompt can react — e.g.
the `browser` tool is policy-blocked on `chrome-extension` turns because the
`page` tool is the right surface there.

**Team** — a session with multiple bot members. `mode=round` fans a turn out to
every member via synthetic queued messages. Being **parked** as part of the
one-thread-per-bot change.

**Tool budget** — a per-turn token cap on tool *results* (15k default) plus a
per-session cap (50k), with a hard stop. Exists because two or three oversized
page reads could consume an entire turn's budget.

**Vault** — `~/.nexus/vault/`, a folder of markdown files. **The user's data
and the source of truth.** Has FTS5 search, a tag index, a backlinks graph, and
GraphRAG on top. Kanban boards, calendars, data tables and workflows are all
*just vault files* identified by a frontmatter key.

**Watermark** — a per-session high-water mark of estimated tokens, used to stop
soft auto-compaction re-triggering every turn (it requires 15% growth before
firing again).

**YOLO mode** — auto-answer all HITL prompts. For unattended runs.
