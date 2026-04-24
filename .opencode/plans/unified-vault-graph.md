# Unified Filterable Vault Graph — Implementation Plan

## Summary

Build a unified, filterable graph view that bridges Vault file links, GraphRAG entities, and tag/index metadata into a single explorable canvas. Users can scope the graph to a document, folder, tag, search result, or entity and explore neighborhoods at configurable depth.

## Architecture

### Data Flow
```
Vault files (disk)
  ├── vault_index.py → file_links, file_tags (SQLite)
  ├── vault_graph.py → link graph (currently ephemeral, will be cached)
  └── graphrag_manager → EntityGraph + chunks (GraphRAG SQLite)

Vault Graph API (scoped)
  └── GET /vault/graph?scope=...&seed=...&hops=N&edge_types=...
        ├── Vault nodes (files with tags, metadata)
        ├── Entity nodes (GraphRAG entities)
        └── Edges (link, tag-cooccurrence, shared-entity, folder-member)

Frontend
  └── GraphView.tsx (unified, with scope selector + filter bar)
```

---

## Phase 1 — Data Foundation (Backend)

### 1.1 Add `forward_links()` to `vault_index.py`
- [ ] Add public function `forward_links(path: str) -> list[str]` that queries `SELECT to_path FROM file_links WHERE from_path = ?`
- **File:** `agent/src/nexus/vault_index.py` (~line 218, after `backlinks()`)
- **Effort:** Small — one SQL query, mirrors existing `backlinks()` pattern

### 1.2 Expose forward links in API
- [ ] Add `GET /vault/forward-links?path=X` endpoint
- **File:** `agent/src/nexus/server/app.py` (near the existing `GET /vault/backlinks` route)
- **Effort:** Small

### 1.3 Enrich `GraphNode` with tags and metadata
- [ ] Extend `GraphNode` TypedDict to include: `tags: list[str]`, `title: str` (from frontmatter)
- [ ] Update `vault_graph.build_graph()` to read tags from `vault_index.tags_for_file()` and titles from frontmatter
- **File:** `agent/src/nexus/vault_graph.py`
- **Effort:** Small-Medium — need to parse frontmatter for each file (pattern exists in `vault.py:_parse_frontmatter()`)

### 1.4 Add vault graph caching
- [ ] Add module-level cache (same pattern as `server/graph.py` — TTL + monotonic time)
- [ ] Add `invalidate_graph_cache()` function
- [ ] Hook invalidation into `vault.write_file()` (after index updates)
- [ ] Hook invalidation into vault delete, move, and folder operations
- **Files:**
  - `agent/src/nexus/vault_graph.py` — cache + invalidation
  - `agent/src/nexus/vault.py` — call `invalidate_graph_cache()` after writes
- **Effort:** Small — well-established pattern in codebase

### 1.5 Add entity↔vault bridge in `graphrag_manager.py`
- [ ] Add `entities_for_source(source_path: str) -> list[dict]` — queries chunks by source_path, then entity_mentions to get entities. Returns `[{id, name, type}]`
- [ ] Add `sources_for_entity(entity_id: int) -> list[str]` — calls `graph.chunks_for_entity()`, then looks up each chunk's source_path via engine. Returns distinct paths
- **File:** `agent/src/nexus/agent/graphrag_manager.py`
- **Decision:** Build here (not in loom) since graphrag_manager already has access to both engine and graph. Zero loom changes needed.
- **Effort:** Medium

### 1.6 Add entity↔vault bridge endpoints
- [ ] `GET /vault/graph/entity-sources?path=X` — returns all GraphRAG entities extracted from a vault file
- [ ] `GET /vault/graph/source-files?entity_id=N` — returns all vault files that mention an entity
- **File:** `agent/src/nexus/server/app.py`
- **Effort:** Small — thin wrappers over graphrag_manager functions

### 1.7 Build scoped graph API
- [ ] Extend or replace `GET /vault/graph` with query parameters:
  ```
  scope: "all" | "file" | "folder" | "tag" | "search" | "entity"
  seed: str  (path / folder / tag / query / entity_id)
  hops: int (1-3, default 1)
  edge_types: "link,tag,entity" (comma-separated, default "link")
  ```
- [ ] Scope implementations:
  - **all**: current behavior (full graph, but now cached + enriched)
  - **file**: seed=path → forward links + backlinks, expand by hops through links
  - **folder**: seed=folder/ → all files in folder + their inter-connections + cross-folder links
  - **tag**: seed=tag → files with this tag, connected by shared links/tags
  - **search**: seed=query → FTS5 search, then graph the result files with their inter-connections
  - **entity**: seed=entity_id → vault files mentioning the entity, connected by shared entities
- [ ] Return enriched `GraphData`:
  ```python
  class GraphNode(TypedDict):
      path: str
      size: int
      folder: str
      tags: list[str]
      title: str

  class GraphEdge(TypedDict):
      from_: str  # or entity_id
      to: str     # or entity_id
      type: str   # "link" | "tag-cooccurrence" | "shared-entity" | "folder-member"

  class EntityNode(TypedDict):
      id: int
      name: str
      type: str
      source_paths: list[str]

  class ScopedGraphData(TypedDict):
      nodes: list[GraphNode]
      edges: list[GraphEdge]
      entity_nodes: list[EntityNode]  # optional, when edge_types includes "entity"
      orphans: list[str]
  ```
- **File:** `agent/src/nexus/vault_graph.py` (new function `build_scoped_graph()`) + `agent/src/nexus/server/app.py` (updated endpoint)
- **Effort:** Medium-Large — this is the core backend work

---

## Phase 2 — Unified Graph UI (Frontend)

### 2.1 Update TypeScript types in `api.ts`
- [ ] Extend `GraphNode` with `tags: string[]`, `title: string`
- [ ] Extend `GraphEdge` with `type: string`
- [ ] Add `EntityNode` type for GraphRAG entities in vault graph context
- [ ] Extend `GraphData` with optional `entity_nodes: EntityNode[]`
- [ ] Update `getVaultGraph()` to accept optional scope params
- **File:** `ui/src/api.ts`
- **Effort:** Small

### 2.2 Add scope selector and filter bar to `GraphView.tsx`
- [ ] Add state: `scope`, `seed`, `hops`, `edgeTypeFilter`, `tagFilter`
- [ ] Add toolbar controls:
  - Scope dropdown (All, File, Folder, Tag, Search, Entity)
  - Seed input (text input, contextually labeled based on scope)
  - Hops slider (1-3)
  - Tag filter chips (multi-select from all tags in current graph)
  - Edge type toggles (links, tag-cooccurrence, shared-entity)
- [ ] Wire scope/seed changes to re-fetch graph data with params
- **File:** `ui/src/components/GraphView.tsx`
- **Effort:** Medium-Large

### 2.3 Implement client-side filtering
- [ ] Filter drawn nodes/edges based on tag and edge-type filters
- [ ] Highlight seed node and its direct neighbors
- [ ] Dim or hide filtered-out nodes
- [ ] Update physics to only simulate visible nodes (not just hide drawn ones)
- **File:** `ui/src/components/GraphView.tsx`
- **Effort:** Medium

### 2.4 Implement node detail panel
- [ ] On node click, show a detail sidebar/panel with:
  - **File node**: title, tags, backlinks count, forward links count, GraphRAG entities extracted from it
  - **Entity node**: name, type, description, source files (clickable vault links)
- [ ] "Explore from here" button that re-scopes the graph to the clicked node
- **File:** `ui/src/components/GraphView.tsx` (new section within the component)
- **Effort:** Medium

### 2.5 Differentiate node/edge rendering
- [ ] Vault file nodes: current circle style, colored by folder
- [ ] Entity nodes: smaller, rounded square shape, colored by entity type (reuse KnowledgeView's TYPE_COLORS)
- [ ] Edge rendering by type:
  - Link edges: solid lines (current)
  - Tag-cooccurrence: dotted lines
  - Shared-entity: dashed lines, slightly different color
  - Folder-member: very faint/subtle
- **File:** `ui/src/components/GraphView.tsx`
- **Effort:** Medium

---

## Phase 3 — Polish & Performance

### 3.1 Lazy hop expansion
- [ ] Default to hops=1, show "expand" affordance on boundary nodes
- [ ] Click "expand" on a boundary node → fetch 1 more hop from that seed
- [ ] Merge new nodes/edges into existing graph (don't rebuild entire canvas)
- **File:** `ui/src/components/GraphView.tsx` + backend endpoint
- **Effort:** Medium

### 3.2 Label visibility thresholds
- [ ] Hide labels when zoomed out below a threshold (too many overlapping)
- [ ] Always show labels for hovered node + direct neighbors
- **File:** `ui/src/components/GraphView.tsx`
- **Effort:** Small

### 3.3 Keyboard navigation
- [ ] Arrow keys to move selection between connected nodes
- [ ] Enter to "explore from here" (re-scope to selected node)
- [ ] Escape to deselect / close detail panel
- **File:** `ui/src/components/GraphView.tsx`
- **Effort:** Small-Medium

### 3.4 Edge labels on hover
- [ ] Tooltip on edge hover showing edge type and (for entity edges) the shared entity name
- [ ] Pattern exists in `KnowledgeView.tsx` (edge hit testing with `distToSegment`)
- **File:** `ui/src/components/GraphView.tsx`
- **Effort:** Small-Medium

---

## Execution Order

Within each phase, tasks should be done in order. Phases 1 and 2 can partially overlap:

```
1.1 → 1.2 → 1.3 → 1.4  (vault graph enrichment — can ship together)
              ↓
           1.5 → 1.6   (GraphRAG bridge — can ship as separate unit)
              ↓
           1.7          (scoped API — depends on 1.1–1.6)
              ↓
           2.1 → 2.2 → 2.3 → 2.4 → 2.5  (UI — starts once 1.7 API is stable)
                                        ↓
                                   3.1–3.4  (polish)
```

## Key Decisions

1. **Entity→source aggregation location**: Build in `graphrag_manager.py` (not loom's EntityGraph) since it already has access to both engine and graph. **Zero loom changes needed.**

2. **No shared graph hook (yet)**: The three graph components have duplicated rendering code, but extracting a shared hook is a separate refactor. For this work, focus on GraphView.tsx only. Shared abstraction is a follow-up.

3. **Server-side scoping + client-side filtering**: Scoping (which nodes to include) is server-side (the API returns only relevant nodes). Tag/edge-type filtering is client-side (toggle visibility without re-fetching). Minimizes API calls while keeping the graph responsive.

4. **Backward compatibility**: The enriched `GET /vault/graph` with no params returns the same shape as before (plus new optional fields). Existing consumers won't break.

## Files Changed (Summary)

| File | Changes |
|------|---------|
| `agent/src/nexus/vault_index.py` | Add `forward_links()` |
| `agent/src/nexus/vault_graph.py` | Enrich GraphNode, add caching, add `build_scoped_graph()` |
| `agent/src/nexus/vault.py` | Call graph cache invalidation after writes |
| `agent/src/nexus/agent/graphrag_manager.py` | Add `entities_for_source()`, `sources_for_entity()` |
| `agent/src/nexus/server/app.py` | New endpoints, update `GET /vault/graph` |
| `ui/src/api.ts` | Update types, add scoped graph API function |
| `ui/src/components/GraphView.tsx` | Scope selector, filters, detail panel, entity nodes |
