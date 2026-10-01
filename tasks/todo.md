# Nexus Uplift — Plan

Decisions (locked with user 2026-09-30):
- Main nav keeps Vault, Calendar, Kanban, Workflows (kanban restored on user request).
- Coordinator: ONE master chat, proactive from day one (heartbeat sweeps + Telegram digests).
- Execution: big restructure first, then features phased on the new skeleton.
- Every nav view lists its items in the sidebar's lower panel; Chat lists only
  unprojected chats; Settings lives in the header gear.

## Phase 0 — Structural restructure ✅
- [x] Route model + URL routing (`ui/src/routes.ts`, `#/view[/path]`, `#/advanced/<area>`,
      legacy redirects, back/forward)
- [x] App.tsx decomposed into view panes (`components/views/`) with shared `views.css`
- [x] Nav: Chat · Projects · Apps · Vault · Calendar · Kanban · Workflows (+ Settings gear in header)
- [x] Sidebar list panels for every view (sessions/projects/apps/tree/calendars/boards/workflows)

## Phase 1 — Project workspace ✅
- [x] ProjectsPane workspace: metadata, vault link, TG binding chips, instructions, title+FTS search
- [x] Backend `GET /telegram/bindings` (+ PATCH/DELETE in Phase 5)

## Phase 2 — Apps as main-area surfaces ✅
- [x] AppsPane grid → dashboard → table drill-down → ER diagram; `#/apps/<folder>` deep links

## Phase 3 — Coordinator master chat ✅
- [x] `nexus_sessions` + `session_dispatch` tools (master-session gated)
- [x] `[coordinator]` config, auto-provisioned Master session, prompt block, Master badge
- [x] Telegram DM → master session; hot enable via PATCH /config
- [x] Proactive sweeps (read-only enforced, quiet hours, NOTHING_NEW, digest delivery)
- [x] 3b: dispatch approval tiers — `auto_approve` ("all" or project ids); unapproved
      dispatch refused with `needs_confirmation` → ask_user → retry `confirmed=true`

## Phase 4 — Advanced subsite ✅
- [x] `#/advanced/graph|heartbeat|dream` + Settings → Advanced tools cards
- [x] Tabbed AdvancedPane (route-driven, tabs mount lazily on first visit, stay alive)
- [x] ui_mode hiding retired (useFeatures deleted; server field kept for compat)

## Phase 5 — Telegram topic management UX ✅
- [x] HITL after /switch fixed (context-prefix fallback in find_by_session)
- [x] Bindings admin API + Settings "Linked chats & topics" table (rebind/unbind)
- [x] /topics command; /project inline buttons (already existed)

## Phase 6 — Cleanup, tests, docs ✅
- [x] Dead code removed (ProjectSection, old KanbanListPanel, useFeatures, AppsPane.css)
- [x] CLAUDE.md + tasks/todo.md updated to the new architecture
- [x] Full backend suite + ruff + tsc/vite green

## Review / Results (final)

All phases implemented, each committed separately (76197a3 → head). UX corrections
folded in along the way per user feedback: sidebar list panels everywhere, flat
unprojected chat list, Kanban restored as a view, settings gear in the header
(+ flex fix so the sidebar bottom never jumps), calendars panel instead of the
dropdown, shared views.css design language.

**How to test the coordinator**: Settings → Features → Coordinator → enable;
message the bot from your DM (becomes the master chat); ask it to "list my
projects" (nexus_sessions) or "ask project X to do Y" (session_dispatch — expect
an approval prompt unless auto_approve is set). Sweep: set interval to e.g. 5
minutes in Settings, wait for the digest.

**Known smaller items left intentionally**:
- `auto_approve` is config-file only (no UI editor) — ids are ugly; prompt-level
  confirmation covers the common case.
- Mobile tab bar has no Kanban/Calendar/Workflows tabs (drawer access only).
- Server `ui_mode` settings field remains for compat; nothing reads it.

### Lessons
- explore-agent claims about bugs can be wrong: the "session pagination bug" was actually
  correct semantics (`/sessions` returns ALL project sessions; X-Total-Count = ungrouped only).
  Verify endpoint behavior in source before "fixing" comparisons.
- Every nav view must list its items in the sidebar's lower panel (user correction): new views
  get a list panel, selection lifted to App — not main-area-only navigation.
- Chat list shows ONLY unprojected chats once Projects is a dedicated view (user correction).
- New surfaces must reuse the app's tokens (--border-soft for borders, not --bg-soft; var(--radius);
  --bg-hover hovers) and the shared views.css language — bespoke CSS looks unprofessional.
- Don't remove a top-level view (kanban) without giving its items an equally reachable home.
- Keep-mounted children must mount lazily (mount-on-first-activation), else lazy bundles all load.
