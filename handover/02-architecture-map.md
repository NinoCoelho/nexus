# Architecture map

Reference, not a tutorial. Use it to find things in ~94k LOC of backend and
~97k LOC of frontend. Line counts and paths are from `542bfb5`.

---

## 1. The spine: one chat turn

```
POST /chat/stream                 server/routes/chat_stream.py       (612)
  → ChatTurnRunner                server/services/chat_turn_runner.py (654)
      sets CURRENT_SESSION_ID, TURN_SURFACE; owns the message queue
  → Agent.run_turn_stream         agent/loop/agent.py                (1657)
      per-iteration: _before_llm_call rebuilds the system prompt
  → LoomProviderAdapter           agent/_loom_bridge/adapter.py      (385)
      wraps a Nexus provider as a loom provider; 180s stream-idle watchdog
  → provider.chat_stream          agent/llm/{openai,anthropic,bedrock,mlx_provider}.py
  → stream_translator             agent/loop/stream_translator.py    (356)
      loom events → SSE event dicts
```

Two things to know about this path:

- **`launch_turn` is not on it.** `server/services/turn_launcher.py` (553) is
  the shared pipeline used by Telegram, dispatches and the coordinator — but
  the web route builds its own `ChatTurnRunner`. A "pre-turn" hook added only
  to `launch_turn` misses the main surface. Mirror hooks in both.
- **The system prompt is rebuilt on every iteration**, and history SYSTEM
  messages are stripped each time — except those `compact.is_carried_context`
  recognizes. That exception is what keeps the compaction summary alive.

## 2. Subsystems, by directory

### Agent core — `agent/src/nexus/agent/`

| Path | LOC | Purpose |
|---|---|---|
| `loop/agent.py` | 1657 | the turn runner; watermarks, calibration, overflow/error handling |
| `loop/compact.py` | 840 | compaction primitives: tool shrink, microcompaction, `hard_trim` |
| `loop/summarize.py` | 580 | LLM summarization + session-note persistence |
| `loop/overflow.py` | 384 | **all** budget constants, `usable_tokens`, window resolution |
| `loop/_builder.py` | 356 | builds the loom Agent; `_before_llm_call`, model choice, overhead measurement |
| `loop/stream_translator.py` | 356 | loom → SSE translation |
| `loop/parts_index.py` | 328 | FTS5 index over the compaction archive |
| `loop/retention.py` / `relevance.py` | 183 / 197 | which messages survive compaction, and why |
| `loop/compactor.py` | 206 | adapts loom's compaction protocol to Nexus |
| `loop/budget.py` | 153 | per-turn tool-result token budget |
| `prompt_builder.py` | 910 | the system prompt; progressive disclosure; cache zones |
| `page_tool.py` | 607 | acts on the user's **real** browser tab via the extension |
| `tool_discovery.py` | 428 | core tool set + `tool_search` for the rest |
| `registry.py` | 273 | **the provider factory** — one dispatch point |
| `_loom_bridge/registry.py` | 661 | builds the tool registry |
| `llm/` | 1823 | provider implementations + shared encoding |
| `providers/catalog.py` + `catalog.json` | 361 + 922 | provider catalog, capability resolution |
| `providers/probe.py` | 326 | lazy vision-capability prober |

**Provider interface** (`agent/llm/types.py`): one abstract method,
`async chat(messages, *, tools, model, max_tokens, extra_payload)`. Optional
`chat_stream` (default synthesizes from `chat`) and `aclose`. Stream events are
plain dicts: `delta`, `thinking_delta`, `tool_call_start/delta/end`, `finish`.
Note `chat_stream` has **no** `extra_payload` — there is no per-call channel for
"which bot", so providers that need it read `agent/context.py::CURRENT_SESSION_ID`.

### Server — `agent/src/nexus/server/`

`app.py` (878) registers ~50 routers and the auth middleware.
`app_lifespan.py` (937) is startup/shutdown wiring — heartbeat scheduler,
pollers, MCP, GraphRAG, vault watcher.

| Path | LOC | Purpose |
|---|---|---|
| `services/chat_turn_runner.py` | 654 | per-turn execution, queueing, chaining |
| `services/turn_launcher.py` | 553 | shared launch pipeline (Telegram, dispatch, coordinator) |
| `services/hitl_resume.py` | 258 | resuming parked human-in-the-loop prompts |
| `session_store/store.py` | 900 | sessions; composition over loom's store |
| `session_store/pubsub.py` | 855 | in-memory SSE pub/sub + replay |
| `session_store/schema.py` | 578 | **all** SQLite DDL + idempotent migrations |
| `project_store.py` | 297 | projects == bots |
| `bind.py` | 234 | loopback vs tailnet bind resolution |

Routes (52 modules, 17.5k LOC) are in `server/routes/`. The big ones:
`workflows.py` (1485), `vault_dashboard.py` (928), `sessions.py` (927),
`vault_import.py` (889), `chat.py` (848), `graph.py` (841), `providers.py` (835).

### Vault — top-level `agent/src/nexus/`

The vault is a folder of markdown. Features are **frontmatter keys**, not
separate stores:

| Frontmatter | Module | Rendered by |
|---|---|---|
| `kanban-plugin: basic` | `vault_kanban/` (936) | `ui/.../KanbanBoard` |
| `calendar-plugin: basic` | `vault_calendar/` (1145) | `ui/.../CalendarView` |
| `data-table-plugin: basic` | `vault_datatable*.py` | `ui/.../DataTableView` |
| `workflow-plugin: basic` | `workflows/` (2809) | `ui/.../WorkflowFlow` |
| folder `_data.md` (`data-dashboard`) | `vault_dashboard.py` (589) | `ui/.../DataDashboardView` |

Supporting: `vault.py` (427, the file store, 1 MiB text / 32 MiB binary caps),
`vault_search.py` (244, FTS5), `vault_index.py` (334, tags + backlinks),
`markdown_links.py` (121, **the single source of truth for link parsing** —
wiki-links, anchors, bare paths, Obsidian stem resolution),
`vault_watch.py` (249, debounced watchdog for external/Syncthing writes),
`vault_history.py` (492, optional git-backed undo),
`vault_csv.py` (682, DuckDB over CSV), `vault_graph/` (627, backlinks graph),
`vault_import_parsers.py` (326, ChatGPT/Claude/Gemini export conversion).

Knowledge graph: `agent/graphrag_manager/` (1021) wraps loom's `GraphRAGEngine`;
ontology lives in the vault at `vault/_system/ontology/*.csv`; retrieval is
hybrid (vector + FTS5 fused by reciprocal rank).

### Browser / computer-use — `agent/src/nexus/browser/`

Spec: [`/browser-computer-use-spec.md`](/browser-computer-use-spec.md) — read it
before touching this.

| Path | LOC | Purpose |
|---|---|---|
| `manager.py` | 1758 | one Chrome per context, CDP websocket, 21 actions, screencast, idle reaper |
| `probes.py` | 574 | the JS perception library — **byte-identical to `chrome/probes.js`** |
| `tool.py` | 483 | the `browser` ToolSpec, context-key resolution, flows, sub-LLM extract |
| `flows.py` / `site_notes.py` / `extract.py` | 132 / 128 / 64 | record-replay, per-host notes, sub-LLM page extraction |

Two surfaces, one contract: the `browser` tool drives a **managed** Chrome over
CDP; the `page` tool (`agent/page_tool.py`) drives the **user's real tab** via
the extension. Both run the same probes. Perception is layered cheap-first:
text → element → vision.

### Background work

All scheduling rides loom's `HeartbeatScheduler` (60s tick, started in
`app_lifespan.py`). Drivers in `heartbeat_drivers/`: calendar triggers,
coordinator sweeps, dream, workflow cron, user heartbeats. Driver isolation is
mandatory — each `check()` gets its own task plus a hard timeout, and long work
detaches. One inline LLM sweep once froze **all** scheduling for 20+ minutes.

| Subsystem | Path | LOC | State |
|---|---|---|---|
| Dream | `dream/` | 1842 | **being reworked** — see `plan/ws3` |
| Workflows | `workflows/` | 2809 | engine kept, UI being removed |
| Coordinator | `coordinator.py` + `coordinator_tools.py` | 1105 | the "master chat" deputy |
| Telegram | `telegram/` | 4279 | **most actively developed surface** |
| Push | `push/` | 454 | Web Push + APNs |
| Local LLM | `local_llm/` | 2136 + MLX modules | llama.cpp supervisor; MLX is in-process |

### Clients

| Client | LOC | Notes |
|---|---|---|
| `ui/` | 97.5k (451 files, 82 CSS) | React 19 + Vite; being retired in favour of native |
| `ios/` | 6.4k (20 Swift) | SwiftUI, zero deps. `ios/Nexus/Core/` is **UIKit-free** → the seed of the shared Swift core |
| `chrome/` | 5.9k | no-build MV3 side panel, one conversation per tab |
| `packaging/macos/` | 952 Swift | bundled-Python supervision, signing, notarization |

## 3. Data stores

| Store | Path | Notes |
|---|---|---|
| Sessions | `~/.nexus/sessions.sqlite` | loom-owned `sessions`/`messages` + Nexus columns and tables. `messages_fts` is **external-content FTS5** with sync triggers. |
| Vault FTS | `vault_index.sqlite` | `vault_fts` (porter unicode61) |
| Vault meta | `vault_meta.sqlite` | `file_tags`, `file_links`, `file_meta` (mtime-incremental) |
| Compaction archive | `vault/.session-memory/.parts/*.jsonl` + `parts_fts` | **the JSONL is the source of truth**; the index only points at it |
| GraphRAG | `~/.nexus/graphrag/` | loom-owned triple store + Nexus `content_hashes`/`index_meta` manifest |
| Dream / workflows / heartbeat | `dream_state.sqlite`, `workflow_runs.sqlite`, `heartbeat.db` | |
| Site credentials | `site_credentials.db` + `keys/` | Fernet, 0600 |

Nexus-added columns worth knowing: `sessions.{parent_session_id, hidden,
project_id, archived, origin, team}`, `messages.{reasoning_content, origin}`,
`projects.{persona, handle, model}`. **loom's connection has no `row_factory`**
— read migration rows positionally.

## 4. Auth and network

`LoopbackOrTokenMiddleware` in `server/app.py:151-277` splits on whether proxy
headers are present:

- **loopback + no proxy headers** → bundled UI / native app, auth bypassed.
- **proxy headers present** → came through a tunnel; require the cookie unless
  the path is in the tunnel-public allowlist.
- **non-loopback, no proxy headers** → direct tailnet client; require a Bearer
  token (packaged `NEXUS_ACCESS_TOKEN` or the pairing `client_token`).
  Fail-closed 401.

Hard loopback-only routes (403 even with a valid token): `/pair`,
`/pair/rotate`, `/tunnel/{status,start,stop,install}`, `/auth/callback`,
`/auth/local/*/claim`. `POST /browser/action` is conditionally gated by
`[browser].remote_control` for non-loopback clients.

`/docs`, `/redoc`, `/openapi.json` are **disabled** — single-user app, not a
public API.

## 5. Config

`~/.nexus/config.toml` is canonical (`config_schema.py` 663,
`config_file.py` 574). Sections: `[agent]`, `[providers.*]`, `[[models]]`,
`[graphrag]`, `[search]`, `[scrape]`, `[transcription]`, `[tts]`, `[vault]`,
`[ui]`, `[location]`, `[dream]`, `[mcp]`, `[delegate]`, `[broker]`,
`[telegram]`, `[coordinator]`, `[browser]`, `[server]`, `[push]`.

UI preferences are separate, in `~/.nexus/settings.json`
(`server/settings.py`).

**Every `save()` rewrites the whole file.** A field missing from
`_cfg_to_dict` / `_provider_to_dict` is silently wiped on the next settings
save. This has bitten `[delegate]` and the capability-probe cache already. See
[`05-known-traps.md`](05-known-traps.md) #3.

Legacy escape hatch: `NEXUS_LLM_BASE_URL` + `NEXUS_LLM_API_KEY` +
`NEXUS_LLM_MODEL` override the config file when **all three** are set.

## 6. Removed systems you may see references to

- **Feature flags / subscription gating** — `features.py`, `FeatureGateMiddleware`,
  `FEATURE_TOOLS`/`FEATURE_ROUTES` were deleted in `b4f6779`. All tools and
  routes register unconditionally. `docs/features.md` and a CLAUDE.md line
  still described it; both corrected on this branch.
- **`ui_mode: normal|advanced`** — the view-hiding mechanism is gone; advanced
  views live behind `#/advanced/*` routes. The settings field survives for
  compat and nothing reads it.
- **Router `fixed`/`auto` modes** — removed; `[agent].default_model` is it.
