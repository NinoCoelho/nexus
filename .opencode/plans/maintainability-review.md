# Codebase Maintainability & Best Practices Review

## Summary

Full audit of the Nexus codebase (~63K lines frontend, ~20K lines backend routes, ~15K lines agent core, ~10K lines tools/features). Findings are organized by priority below.

---

## P0 — Critical (God classes/methods, high duplication)

### 1. Decompose `loop/agent.py` — 1,570 lines (1 class)
**File:** `agent/src/nexus/agent/loop/agent.py`

The `Agent` class is 1,463 lines. `run_turn_stream()` alone is **~1,038 lines** handling 6+ distinct concerns:
- Streaming event translation (loom-to-SSE mapping)
- Retry orchestration (3 retry strategies with shared cleanup)
- Mid-turn compaction triggers
- HITL parking detection
- Reasoning content tracking
- Rate-limit pausing

**Additionally:** `continue_after_hitl()` (~195 lines) duplicates the event translation logic from `run_turn_stream`.

**Plan:**
- [ ] Extract `StreamEventTranslator` — loom-to-SSE event mapping (shared by `run_turn_stream` and `continue_after_hitl`)
- [ ] Extract `RetryManager` — the 3 retry paths + the 5-line cleanup block that appears 3x verbatim
- [ ] Extract `ReasoningTracker` — reasoning content lifecycle (per-iteration sink, map restoration)
- [ ] Extract context-window / overflow pre-check logic (partially exists in `overflow.py`)

### 2. Decompose `app.py` — 1,338 lines (god function)
**File:** `agent/src/nexus/server/app.py`

`create_app()` is **1,024 lines** — the single largest function in the codebase. Handles: dependency injection, HITL wiring, terminal management, trace callback, lifespan (~487 lines of startup/shutdown), middleware stacking, router mounting, subagent spawning, lane-change hooks, and UI mounting.

**Plan:**
- [ ] Extract `app_lifespan.py` — the 487-line lifespan context manager
- [ ] Extract `app_subagents.py` — the `_run_one_subagent` closure (~118 lines)
- [ ] Extract agent wiring closures (HITL handlers, terminal, dispatcher) to `app_agents.py`
- [ ] Keep `app.py` as a thin factory (~100 lines)

### 3. Extract dream module shared utilities — ~200 lines of exact duplication
**Files:** `agent/src/nexus/dream/{insight,consolidate,rehearse,skill_refine}.py`

`_extract_json()` is **byte-for-byte identical** (36 lines each) across 4 files. Session loading is near-identical across 3 files.

**Plan:**
- [ ] Create `dream/_shared.py` with `_extract_json()`, `_load_session_summaries()`, `_format_context_dict_list()`
- [ ] Replace all 4 copies of `_extract_json` + 3 copies of session loading

### 4. Deduplicate SSE event generator — ~500 lines of near-duplicate code
**Files:** `routes/chat_stream.py`, `routes/chat.py`, `routes/vault_dispatch_helpers.py`

The streaming event-to-SSE translation (delta accumulation, tool tracking, error classification, partial persistence) is implemented nearly identically in 3 locations.

**Plan:**
- [ ] Create `server/routes/_streaming.py` with a reusable async generator
- [ ] Refactor `chat_stream.py`, `chat.py` (hitl answer), and `vault_dispatch_helpers.py` to use it

---

## P1 — High (Large files, repeated patterns)

### 5. Refactor `build_tool_registry` — 392 lines of repetitive closures
**File:** `agent/src/nexus/agent/_loom_bridge/registry.py`

~25 tool registrations follow an identical pattern. Should be a declarative table.

**Plan:**
- [ ] Create a `(ToolSpec, handler_fn, feature_gate?)` registry table
- [ ] Drive registration from the table, reducing to ~50 lines

### 6. Split `workflows/engine.py` — 1,601 lines
**File:** `agent/src/nexus/workflows/engine.py`

Handles step dispatch for 10+ step types, template resolution, interactive debugging, run persistence, and step re-running.

**Plan:**
- [ ] Extract step-type handlers into `workflows/steps/` (one file per step type or group)
- [ ] Keep `engine.py` as the orchestrator (~200-300 lines)

### 7. Split `vault_datatable.py` — 1,095 lines
**File:** `agent/src/nexus/vault_datatable.py`

Handles schema management, row operations, typed relations, junction tables, formula/rollup materialization, and CSV import.

**Plan:**
- [ ] Split into `vault_datatable_schema.py`, `vault_datatable_rows.py`, `vault_datatable_materialize.py`

### 8. Deduplicate `_is_proxied()` — 3 identical copies
**Files:** `server/app.py`, `server/middleware.py`, `server/routes/tunnel.py`

**Plan:**
- [ ] Single canonical implementation in `server/middleware.py` (or `server/utils.py`)
- [ ] Import in the other two

### 9. Refactor action-dispatch pattern in tool files
**Files:** `tools/{datatable,kanban,calendar,dashboard,vault,csv}_tool.py`

All 6 files use the same monolithic `if/elif` dispatch with identical error handling. A registry pattern would reduce line counts by 15-20% and make individual actions testable.

**Plan:**
- [ ] Introduce `_REGISTRY = {"action": handler_fn}` pattern
- [ ] Extract common `@json_tool_handler` decorator for try/except + serialization
- [ ] Unify response format across all tools (some return `json.dumps`, some return `ToolResult.to_text()`, some return raw dicts)

### 10. Extract LLM provider shared payload construction
**Files:** `llm/openai.py`, `llm/anthropic.py`, `llm/bedrock.py`

All three providers duplicate model resolution, multimodal preparation, and payload dict construction between `chat()` and `chat_stream()`.

**Plan:**
- [ ] Extract `_build_payload()` method in a base mixin or shared function
- [ ] Same for `_resolve_model()` and `_prepare_multimodal()`

### 11. Extract background turn runner pattern — duplicated 5+ times
**Files:** `vault_dashboard.py`, `vault_dispatch_helpers.py`, `skill_wizard.py`, `app.py`

The pattern "create hidden session, run agent turn, publish terminal event, persist partial on failure" is repeated throughout.

**Plan:**
- [ ] Create `server/services/background_turn.py` with a reusable runner

---

## P2 — Medium (Frontend god components)

### 12. Decompose `App.tsx` — 967 lines, 16 useState, 14 useEffect
**File:** `ui/src/App.tsx`

Manages: routing, auth, notifications, chat state, graph indexing, calendar, voice, model selection, update checking, all view rendering. `VaultView` is rendered 3x with identical props.

**Plan:**
- [ ] Extract `useGlobalSubscriptions()` hook — health polling, language sync, GraphRAG, voice ack, tier changes
- [ ] Extract `useDataViewRouting()` hook — database/dashboard/table sub-view state machine
- [ ] Consolidate the 3x `VaultView` renders into one
- [ ] Consider lazy-loading heavy hidden views (Dream, Workflow, DataDashboard)

### 13. Decompose `WorkflowFlow/ConfigPanel.tsx` — 1,480 lines
**File:** `ui/src/components/WorkflowFlow/ConfigPanel.tsx`

A single `ConfigPanel` component at ~1,100 lines handles 9 step types with inline JSX for each.

**Plan:**
- [ ] Extract per-step-type form components: `ToolCallStepForm`, `HttpRequestStepForm`, `McpCallStepForm`, `TransformStepForm`, etc.
- [ ] Keep `ConfigPanel` as a thin step-type router

### 14. Decompose `WorkflowFlow/index.tsx` Canvas — 1,055 lines
**File:** `ui/src/components/WorkflowFlow/index.tsx`

Mixes graph layout, node CRUD, edge CRUD, interactive execution, undo/redo, payload modal, trigger test.

**Plan:**
- [ ] Extract `useInteractiveRun` hook
- [ ] Extract `useWorkflowCRUD` hook
- [ ] Extract `WorkflowToolbar`, `RunPayloadModal` components

### 15. Decompose `DataDashboardView/index.tsx` — 984 lines
**File:** `ui/src/components/DataDashboardView/index.tsx`

18 useState, 6 useEffect, 13 async callbacks. Manages dashboard CRUD, operations, widgets, SSE tracking, forms.

**Plan:**
- [ ] Extract `useDashboardOperations` hook
- [ ] Extract `useDashboardWidgets` hook
- [ ] Extract `DashboardFormOverlay` component

---

## P3 — Lower Priority (Smaller improvements)

### 16. Extract shared frontend hooks
- [ ] `hooks/usePollWhileRunning.ts` — replaces duplicated polling in HeartbeatView + KanbanBoard
- [ ] `hooks/useCredentials.ts` — cached `listCredentials()` for IntegrationsTab, SkillWizard, ConfigPanel

### 17. Extract utilities from component files
- [ ] `lib/mcpParser.ts` from `IntegrationsTab.tsx` (MCP config parser ~150 lines)
- [ ] `lib/iosAudioUnlock.ts` from `InputBar/index.tsx`
- [ ] `dashboard_prompts.py` from `routes/vault_dashboard.py` (~470 lines of embedded LLM prompts)

### 18. SQLite base store pattern
- [ ] Extract `NexusSqliteStore` base class for `alarm_store.py`, `dream/state.py`, `workflows/store.py` (all share mkdir/connect/PRAGMA/CREATE TABLE)

### 19. Vault index lazy-init guard
- [ ] Add `vault_index.ensure_ready()` to eliminate 5 copies of `is_empty() -> rebuild_from_disk()`

### 20. Message conversion deduplication
- [ ] `loop/helpers.py` and `_loom_bridge/message.py` contain functionally identical Nexus-to-loom message converters — consolidate

### 21. voice_ack.py prompt externalization
- [ ] Move ~130 lines of inline EN/PT prompt templates to a YAML/JSON resource file

---

## Architecture Patterns to Adopt

| Pattern | Where | Benefit |
|---|---|---|
| Service layer for route files | `vault_import.py`, `sessions.py`, `workflows.py` | Separate HTTP from business logic |
| Registry-based tool dispatch | All 6 tool files | Testable actions, less boilerplate |
| Shared streaming utility | Route SSE handlers | Eliminates 500 lines of duplication |
| Composable hooks | React components | Extract state from god components |
| SQLite base class | All stores | Consistent connection management |
