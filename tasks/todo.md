# Nexus Uplift — Plan

Decisions (locked with user 2026-09-30):
- Main nav keeps Vault, Calendar, Workflows (user choice).
- Coordinator: ONE master chat, proactive from day one (heartbeat sweeps + Telegram digests).
- Execution: big restructure first, then features phased on the new skeleton.
- Telegram WS5 focus: topic management UX (+ fix HITL routing after /switch).

## Phase 0 — Structural restructure
- [x] Route model + URL routing (history API, back button, deep links replace one-shot `?view=` parse)
- [x] Decompose App.tsx (838 lines) → view registry + thin shell; uniform keep-alive; chat state never drops on view/session switch
- [x] New nav: Chat · Projects · Apps · Vault · Calendar · Workflows + Settings bottom
- [x] Remove from main nav: Kanban top-level view (kanban only via vault frontmatter), Graph/Heartbeat/Dream (routes stay until Phase 4 advanced subsite)
- [x] Sidebar: remove per-database nav injection; collapse-all/expand-all projects
- [x] Fix per-project pagination bug (hasMoreSessions compares wrong counts — projects look empty)
- [x] Verify: npm run build + manual smoke of all views

## Phase 1 — Project workspace
- [x] Project view in main area: editable metadata (name/description/instructions/color — backend CRUD exists), searchable chat list, session count, vault folder link, Telegram binding status
- [x] Projects nav item = projects browser launching workspaces

### Phase 1 notes
- Backend: `TelegramBindingStore.list_all()` + `GET /telegram/bindings` (project names + active-session
  titles, home-resolved DB path). Test: `test_bindings_lists_with_project_names`.
- UI: ProjectsPane = cards + workspace (metadata chips: vault folder opens Vault, TG binding; collapsible
  instructions; FTS message search scoped to project sessions + title filter). `onOpenInVault` wired.
- Pagination note: `/sessions` returns ALL project sessions every call; X-Total-Count counts ungrouped
  only — the sidebar comparison (ungrouped vs total) was already right; kept original semantics.

## Phase 2 — Apps as main-area surfaces
- [x] Apps nav item → app list (data-table databases); opening renders in main area where chats render
- [x] Mobile tab bar simplified
(Both landed as part of Phase 0's AppsPane/MobileTabBar rework; no leftover nav-to-"data" references.)

## Phase 3 — Coordinator (one proactive master chat)
- [x] Tools: `nexus_sessions` (list/search/read session+project summaries), `session_dispatch` (launch_turn in any session; wait/detach; coordinator-session-only gating)
- [x] Master session: config [coordinator], own system prompt section, provisioned + persisted on first use, "Master" badge in Chat sidebar
- [x] Telegram: owner DM binds to master session (new DM → coordinator instead of throwaway chat)
- [x] Proactive: heartbeat driver `coordinator_sweep` — 30-min tick, interval gating via sweep_interval_minutes, quiet hours, busy-check; read-only sweep enforced via sweep-mode ContextVar (session_dispatch refuses); digest → Telegram via outbound-only client (no poller conflict); NOTHING_NEW suppresses delivery
- [x] Hot enable/disable: PATCH /config coordinator wires/unwires the service live (no restart)
- [ ] Rules config tiers (auto_approve list) — schema + plumbing only; dispatch-side enforcement is Phase 3b polish

### Phase 3 notes
- New: `agent/coordinator.py` (service: identity/inspect/dispatch/sweep-mode/quiet-hours),
  `agent/coordinator_tools.py`, `heartbeat_drivers/coordinator_sweep/`, `[coordinator]` in
  config_schema + config_file parse/serialize + /config GET/PATCH (PATCH allowlist excludes
  session_id — server-owned), DM override in `telegram/router.py:_handle_chat_message`,
  coordinator prompt block (`prompt_builder._COORDINATOR_BLOCK` via `_builder` session check),
  registry wiring (nexus.agent.context CURRENT_SESSION_ID gating), UI `useCoordinator` + Master badge.
- Tests: `tests/test_coordinator.py` (provisioning/persistence, sweep guard, tool gating, inspect).
  Full suite 1438 passed (6 pre-existing llama-server live-test errors).
- Sweep digest flows: run_background_turn in master session → digest via Telegram binding
  (find_by_session). When user messages the DM, normal poller streaming takes over.

## Phase 4 — Advanced subsite
- [ ] Settings → "Advanced tools" area with tabs Knowledge / Heartbeat / Dream
- [ ] Refactor pass: Heartbeat driver config + log UX, Dream suggestion-review surface, Knowledge consolidates graph + GraphRAG health/reindex
- [ ] Retire ui_mode hiding (superseded) — delete useFeatures hook + server ui_mode remnants
(Interim from Phase 0: advanced views reachable via #/advanced/<area> routes + Settings → Advanced →
"Advanced tools" cards; the ui_mode toggle was already removed from the Advanced tab.)

## Phase 5 — Telegram topic management UX
- [ ] Fix HITL routing after /switch (find_by_session matches only active session)
- [ ] Topic dashboard in UI: bindings list, bind/rebind/unbind, rename, active-session switcher
  (backend ready: GET /telegram/bindings from Phase 1)
- [ ] Telegram: /project inline buttons, /topics listing

## Phase 6 — Cleanup & docs
- [ ] Remove dead code paths (old view union, ui_mode remnants)
- [ ] Backend tests for new tools; uv run pytest; ruff check
- [ ] Update CLAUDE.md to new architecture

## Review / Results

### Phase 0 (2026-09-30) — DONE
- `ui/src/routes.ts` (new): hash-route model `#/chat|projects|apps|vault|calendar|workflows[/path]` +
  `#/advanced/graph|heartbeat|dream`; `useAppRoute` hook with pushState navigation (back/forward works),
  popstate/hashchange sync, legacy redirects (`#/kanban`→vault, `#/data|database`→apps,
  `#/graph|heartbeat|dream`→`#/advanced/…`) and first-load `?view=&path=` compat.
- New view containers: `components/views/AppsPane.tsx` (app grid → dashboard → table → ER diagram; owns
  selection state + database list + vault-event refresh — all lifted OUT of App.tsx),
  `components/views/ProjectsPane.tsx` (project cards + workspace column: searchable chat list, edit modal,
  new-chat-in-project — seed of the Phase-1 workspace), `components/views/AdvancedPane.tsx`
  (graph/heartbeat/dream dispatcher behind advanced routes).
- App.tsx: 838 → ~730 lines; view state = URL route; data-view state deleted; kanban view deleted
  (boards open via Vault per CLAUDE.md); ui_mode hiding removed (advanced views always reachable).
- Sidebar: nav = Chat/Projects + Apps/Vault/Calendar/Workflows; per-database nav injection REMOVED;
  KanbanListPanel section removed; `hasMoreSessions` bug FIXED (was comparing ungrouped count vs
  total-including-grouped — projects looked empty); SessionsPanel gained Collapse all / Expand all
  (localStorage per project + remount revision).
- MobileTabBar: static 6 tabs (Chat/Projects/Apps/Vault/Calendar/Flows) + menu; no per-DB tabs.
- Settings → Advanced tab: ui_mode toggle REPLACED with "Advanced tools" cards (Knowledge/Heartbeat/Dream)
  that navigate to the advanced routes and close the drawer.
- i18n en + pt-BR viewNames updated (projects/apps added, kanban/data removed).
- Verified: `npm run build` (tsc clean) + dev server serves (200). useFeatures hook now unused —
  delete in Phase 6 cleanup (server ui_mode field untouched).

### Phase 3 (2026-09-30) — core DONE (see Phase 3 notes above)
- Backend + prompt + Telegram DM + proactive sweep + hot-enable all landed; full suite green
  (1438 passed; 6 pre-existing live-LLM errors needing a local llama-server).
- UI: Master badge only — a full Coordinator settings section (enable toggle, interval,
  quiet hours) is still to build (Phase 3b/4 UI pass).

### Lessons
- explore-agent claims about bugs can be wrong: the "session pagination bug" was actually
  correct semantics (`/sessions` returns ALL project sessions; X-Total-Count = ungrouped only).
  Verify endpoint behavior in source before "fixing" comparisons.
- Every nav view must list its items in the sidebar's lower panel (user correction): new views
  get a list panel, selection lifted to App — not main-area-only navigation.
- Chat list shows ONLY unprojected chats once Projects is a dedicated view (user correction).
- New surfaces must reuse the app's tokens (--border-soft for borders, not --bg-soft; var(--radius);
  --bg-hover hovers) and the shared views.css language — bespoke CSS looks unprofessional.
