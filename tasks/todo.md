# Knowledge Center Overhaul + Repo Health Pass

Approved plan (see conversation). Phases executed in order; each phase ends with
`uv run pytest` + `uv run ruff check src tests` (backend) and `npm run build` (UI, when touched).

## Phase 0 — Loom: implement missing GraphRAG review/resolution API
- [x] Clone loom as sibling checkout (../../loom)
- [x] APIs already existed on loom main (HEAD 7ffc4bb) — the venv had a stale pin
- [x] `uv lock --upgrade-package loom-framework` + sync; review routes tests went red→green (8 passed)

## Phase 1 — Security
- [x] vault_import: `_confine` + `_resolve_csv_import_path` (realpath containment, CSV/TSV suffix enforcement)
- [x] DuckDB `read_csv_auto` parameterized via `_sql_str` escaping; duplicated blocks consolidated
- [x] `_collect_selected_files` dir-probe confined; redundant `except (BadZipFile, Exception)` fixed
- [x] Tests: `test_vault_import_security.py` (8 tests: traversal, sneaky traversal, suffix, apostrophe-in-path route)
- [x] Timing-safe `hmac.compare_digest` for NEXUS_ACCESS_TOKEN (header + query)
- [x] `skills/guard.py` hardened: `$(env`, `${SECRET}`, env→pipe/base64/curl, `$VAR|base64`, ssh/aws material, root/home rm targets; `systemctl`/`crontab` caution; 5 tests (19 dangerous / 10 safe / 2 caution snippets); bundled skills scanned — only the cron skill flags caution (expected)

## Phase 2 — Knowledge graph P0
- [x] `vault_graph/scoped.py`: `to_`→`to` KeyErrors fixed (all 5 scopes); O(E²) dedup → set-based
- [x] `routes/vault.py:431` serialization `to_`→`to` fixed
- [x] NEW live bug found+fixed: route called `build_scoped_graph` positionally against a keyword-only signature (500 on every scoped query)
- [x] `markdown_links.py` — single shared parser (wiki-links, aliases, anchors, fences, stem resolution); builder + vault_index both consume it
- [x] builder: single file read, dead tag loop removed, degree computed per node
- [x] graphrag_manager: `_pending_index_tasks` strong refs (GC fix); extraction-model fallback is now loud (builtin extractor + `extraction_warning` in health)
- [x] `_system/` paths skipped in schedule_index + full/streaming indexing
- [x] Tests: `test_markdown_links.py` (18), `test_vault_graph.py` (9, first coverage for scopes)

## Phase 3 — Knowledge UX
- [x] `GraphCanvas2D` — NEW default renderer (canvas 2D, draggable nodes, label LOD, no WebGL); 3D stays via settings toggle (`renderer: "2d"|"3d"`, persisted)
- [x] Shared `canvasCommon.tsx` (tooltip, context menu, auto-fit) used by both canvases
- [x] GraphCanvas3D: per-node THREE object tracking + `dispose()`, pulse-map cleanup on data swap, hoisted `getComputedStyle`, WebGL context release on unmount
- [x] vault mode: seed commit on Go/Enter (no per-keystroke fetch), degree-based sizing, orphan toggle, cap note ("Show all"), tag no-match state, loading/error states, Fit-to-view wired
- [x] `/vault/graph`: degree-ranked `max_nodes` cap (default 300), `total_nodes`, `capped`, `graphrag` health summary
- [x] knowledge mode: empty query clears canvas (no stale graph), type filter keeps selected seed, health/extraction/stale banners, search-error surfacing, Map-based lookups, deps violations fixed
- [x] folder mode: nodes clickable → entity card with "Focus" (seed subgraph) + "Back to full graph"
- [x] SourceFilterBar: debounced (250ms) + cached tree + `.catch`; ReviewPanel no longer shows "No conflicts" on fetch error
- [x] index.tsx: lazy mode data (first tab visit), dead `graphSearch` state removed, dead agent session branch removed
- [x] App.tsx: stale `onSelectSession` prop removed

## Phase 4 — Extraction & retrieval quality
- [x] spaCy label map: only PERSON/PER direct; ORG/GPE/PRODUCT/etc. classify with label hints (no more company→project)
- [x] Relations classified from *sentence context* (not "head tail"); proximity window (≈30 tokens) stops clique explosion; strength from mention count (2+2n, cap 10)
- [x] Batched embeddings for type classification + relation contexts
- [x] Hybrid retrieval: `/graph/knowledge/query` = vector ∪ FTS5 via reciprocal-rank fusion (k=60), FTS-only files as `source: "fts"`; ValueError → 503 not 500
- [x] Tests: `test_builtin_extractor.py` (6), `test_hybrid_retrieval.py` (5)

## Phase 5 — Other correctness
- [x] Dream: `try_start_run` atomic conditional INSERT (TOCTOU closed) + engine uses it
- [x] vault_import: zip extraction, DuckDB describe, full-CSV load → `asyncio.to_thread`
- [x] Alarm GC reaps orphaned ringing/snoozed rows for past occurrences (no infinite re-ring)

## Phase 6 — Cleanup, docs, tests
- [x] Deleted dead UI: `GraphView/index.tsx`, `drawGraph/useSimulation/useCanvasInteraction`, `AgentGraphView/*` (live classes moved to UnifiedGraph.css), `graphEdgeUtils.ts`, `UnifiedGraphView.css`; `cytoscape` dep dropped
- [x] Ruff: 61 → 0 errors (F821 session_store via TYPE_CHECKING import, F401s, F841s, E741s, E402s)
- [x] Test-drift reconciled (was 32 failing): dashboard widgets (query/viz_type schema), datatable cache isolation, settings payload (ui_mode/auto_accept), folder tabs normalization, partial-turn TOOL synthesis, notify phrasing, compact_slash event loop, OCR order-dependence (ocr_server import-time home), ask_user form loops
- [x] CLAUDE.md: features-gating section rewritten (removed in b4f6779), acp_call no longer stub, router note, new "Knowledge graph" section (shared parser, hybrid retrieval, 2D default)
- [x] New tests: `test_vault_search.py` (7 — first FTS coverage)

## Summary

**Why the knowledge graph didn't work:** the venv pinned a loom build that
predates the GraphRAG review/resolution API, so engine init silently failed and
every knowledge endpoint returned empty; the scoped vault-graph queries then
500'd on two independent bugs (`to_` KeyError + positional-args TypeError).
All three fixed; engine initializes and all endpoints verified live against
the real 3,202-file vault.

**Why it was tricky to use:** 3D-only orbit navigation, dead buttons, no-click
folder graphs, per-keystroke refetches, silent failures, uncapped graph loads,
and THREE-object churn. Now: 2D default with draggable nodes, wired
interactions, debounced/committed fetches, visible errors + health banners,
degree-ranked node cap with "show all", orphan visibility, and dispose-safe
renderers.

**Also:** path-traversal + DuckDB SQLi closed in vault import, skills guard
actually blocks exfil patterns now, dream double-run race closed, event-loop
blocking moved off-thread, alarm GC stops phantom re-rings, 61 lint errors
cleared, ~2,000 lines of dead UI removed, and the test suite went from
32-failing to fully green (1,277 passed; 6 remaining errors are env-gated
llama-server live tests, unchanged from baseline).

Verification: `uv run pytest` (1277 passed / 26 skipped / 6 env errors),
`uv run ruff check src tests` (clean), `npm run build` (clean), live server
smoke of all graph endpoints.

## Review
- Loom dependency: lockfile now pins 7ffc4bb (contains the review/resolution API). No loom code changes were needed.
- The builtin-extractor quality changes (sentence-context relations, label hints) are covered by deterministic tests with a fake embedder; recommend a one-off `uv run nexus graphrag reindex` to rebuild the knowledge graph with the new extraction.
- The `/vault/graph` cap default (300) is a UX choice; "Show all" raises to 2000 server-side max.
- Uncommitted by design — review `git diff` and commit when satisfied.
