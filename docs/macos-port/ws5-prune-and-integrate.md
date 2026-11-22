# WS5 — Prune, hide, and integrate with macOS

**Audience: the developer doing the cleanup and the Apple-native work.**
Milestone **M6** — deliberately last, so nothing is deleted that M3–M5 turn
out to need.

Evidence basis: commit counts below are `git log --since=90.days` at `542bfb5`.
"Dead" means verified unreferenced by grep across `ui/src` (`.ts`, `.tsx`,
`.css`), not merely unused-looking.

---

## 1. Delete

**Approved by the product owner:**

- `extension/` — the cookie-export extension + native messaging host.
  **0 commits/90d.** Also drop `nexus cookies setup-chrome`,
  `routes/cookies.py` and the Settings → Cookies section if nothing else uses
  the cookie store. (Note: `[scrape]` may read imported cookies — check before
  removing the store itself.)
- The **ACP bridge** — `tools/acp_call.py` (58 LOC), the ACP paths in
  `broker/`, and loom's `acp` extra in `agent/pyproject.toml`.

**Additionally recommended, each verified dead:**

| Target | Evidence |
|---|---|
| `ui/src/components/CoordinatorBubble/` (1,413 LOC: `index.tsx` 309, `Bubble.tsx` 122, `BubblePanel.tsx` 403, `.css` 579) | imported nowhere outside its own directory. CLAUDE.md records the floating bubble as removed from the nav; the code stayed. |
| `ui/src/hooks/useCoordinatorBubbleStatus.ts` (126) | only referenced by the above |
| `ui/src/components/InsightsView.css` (401) | zero references to `InsightsView` anywhere in `ui/src` |
| `dream/rehearse.py` (231) | [`ws3`](ws3-dream-context-curator.md) |
| MCP Apps `ui://nexus/*` resources + `McpAppSandbox` | once kanban, tables and dashboards render natively, the sandboxed-iframe pathway has no job |
| The `ui_mode` settings field (`server/settings.py:56`, `server/schemas.py:131`, `routes/settings.py:21,54,64`) | nothing reads it |

**Do not delete the UI wholesale.** Keep `ui/` buildable through M5 as a
fallback, and permanently for the two power-tool surfaces (graph, dashboards).

### Documentation that actively misleads — already fixed

For the record, since it was repaired as part of writing this plan:
`agent/src/nexus/features.py` **does not exist** (the subscription
feature-gating system was deleted in `b4f6779`), yet `docs/features.md`
documented it as live and CLAUDE.md's last convention line instructed
contributors to edit it. Both corrected. If you find other references to
`FEATURE_TOOLS` / `FEATURE_ROUTES` / `_should_register`, they are stale too.

## 2. Keep the engine, remove the UI

| Subsystem | Evidence | Where it surfaces instead |
|---|---|---|
| Dreaming | 1 commit/90d backend, 0 in `DreamView` | reworked per ws3; output is the bot memory doc + thread journal |
| Workflows | 1 commit/90d; `routes/workflows.py` 1,485 LOC + `WorkflowFlow.css` 3,049 LOC | read-only summary on the vault note; bots author them as markdown via tools |
| Heartbeat | — | one "Scheduled" list inside Settings |
| GraphRAG internals | 0 commits/90d in `graphrag_manager` | retrieval stays; ontology editor / conflict review / folder-graph wizards go |
| Skill wizard | `SkillWizard.tsx` 927 LOC | the agent authors skills via `skill_manage`; no wizard |
| Teams / round-robin | — | parked (ws2) |
| Trajectories, NBIE report | — | debug endpoints only |

Deleting a *view* must not unregister the *tools*. `build_tool_registry` has no
feature gating any more (that was `features.py`), so removing a UI surface is
purely a frontend change plus leaving the routes in place.

## 3. Unresolved — ask before acting

**`telegram/` (4,279 LOC).** The most actively developed subsystem at
**24 commits/90d**. It was not selected as "keep" in the scoping questions, but
it was also never placed on a cut list. Current plan: **park** — no native UI
work, its binding repoints to `main_session_id` (ws2 §Visibility), code stays
and keeps running. **Do not delete it without explicit confirmation from the
product owner.**

## 4. Kept as-is

Tunnel + read-only share links + web push + APNs, MCP client and server mode,
`agent_delegate` + `delegate_status`, site credentials, vault history, the vault
import wizard, OCR, TTS, transcription, and all 50 bundled skills.

---

## 5. Apple-native integrations

This is where "native" stops being cosmetic. These are new capabilities, not
ports, and they are the strongest argument for the whole exercise.

1. **Global-hotkey quick-ask panel** — a Spotlight-style panel that posts into
   the selected bot's thread and streams the reply. The single highest-value
   macOS affordance for an agent. (The sanctioned exception to "no second chat
   surface" — it is a launcher into the *same* thread.)
2. **App Intents / Shortcuts** — expose "Ask \<bot\>", "Add to vault",
   "Run \<workflow\>" as system intents. Makes Nexus scriptable from
   Shortcuts, Automator and Siri with no Nexus UI at all.
3. **Spotlight indexing of the vault** (`CSSearchableIndex`) — vault notes
   become findable from system search, with deep links back into the app.
   `vault_watch.py` already debounces external changes and re-indexes; hang
   the Spotlight update off the same signal rather than inventing a watcher.
4. **Share extension + Services menu** — send a selection, URL or file from any
   app to a bot. This is the natural replacement for much of what the Chrome
   extension's context menus do.
5. **EventKit two-way calendar** — the vault calendar already stores UTC
   ISO-8601 with RRULE recurrence, so mapping to Calendar.app is bounded, and
   it makes calendar triggers visible where the user already looks. Reminders
   likewise for kanban lanes. Caution: `_`-prefixed calendar directories
   (`_conflicts-quarantine`, `_system`) are excluded from `list_calendars`
   because Syncthing conflict copies must never fire as duplicate events —
   preserve that exclusion in any sync you build.
6. **Native notifications with inline actions** — `HitlNotifier.swift` already
   does Approve/Deny inline for `confirm`; extend to `choice`.
7. **Quick Look + Finder integration** for vault files.

### What *not* to integrate

Do not add a third browser-perception surface (e.g. a WKWebView the agent
drives). The two existing surfaces share one byte-identical probe contract
guarded by a test; a third would triple the drift risk for no new capability.

## Verification

```bash
cd agent && uv run pytest --ignore=tests/live   # establish your own baseline first
cd ui && npm run build                          # tsc must stay clean after deletions
```

After each deletion batch, `npm run build` is the gate — it is the only check
the UI has (there is no lint or test script for `ui/`). For backend deletions,
grep for the symbol across `agent/src` **and** `agent/tests` before removing;
several tests import internals directly.

For the integrations, there is no automated coverage to add: verify by hand
that the hotkey panel reaches the right bot thread, a Shortcut round-trips, a
vault note appears in Spotlight within a few seconds of being written, and a
`confirm` notification's Approve button resolves the turn without opening a
window.
