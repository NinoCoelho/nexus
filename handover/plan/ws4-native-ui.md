# WS4 — Native macOS UI

**Audience: the developer(s) building the SwiftUI app.** Milestones **M1**
(shell), **M3** (chat), **M5** (vault + browser). Nothing here was compiled on
the machine that wrote it — treat the Swift as a specification, not as tested
code.

Strategy, already decided: **native core + WKWebView for power tools.** SwiftUI
for chat, vault, boards, calendar, browser and settings; a web view only for
the force-directed graph and the DuckDB dashboards.

---

## 1. Shared Swift core (`NexusKit`) — M1, task one

**Extract `ios/Nexus/Core/` to a SwiftPM package at `swift/NexusKit/`.**

Verified: those six files — `SSE.swift` (108), `NexusClient.swift` (471),
`Models.swift` (707), `ServerStore.swift` (116), `Keychain.swift` (57),
`Lenient.swift` (49) — import only `Foundation`, `Security` and `Observation`.
**No UIKit anywhere in `Core/`.** The move is mechanical. Point
`ios/Nexus.xcodeproj` at the package and delete the originals in the same
commit so there is never a second copy.

Three changes to make during the extraction:

1. **Memoize `ServerStore.client`.** It currently builds a fresh
   `NexusClient` — and therefore two `URLSession`s — on *every property
   access*. Cache per `activeId`.
2. **Add a loopback transport** alongside the Bearer one. The macOS app talks
   to `127.0.0.1` with no token; `LoopbackOrTokenMiddleware` bypasses auth for
   loopback clients with no proxy headers, exactly as the bundled web UI
   relies on today. Do **not** send `x-forwarded-*` headers from the app —
   loopback plus proxy headers is a 401.
3. **Close the SSE event gap.** `ChatStreamEvent.decode` (`Models.swift:455`)
   handles 13 named events and drops the rest into `.unknown`. Missing versus
   the web client: `paused_for_cooldown`, `turn_started`,
   `team_cascade_capped`, and the whole `subagent_*` family. Fix it in the
   shared core so both clients benefit.

Suggested layout:

```
swift/NexusKit/
  Package.swift
  Sources/NexusKit/
    SSE.swift  NexusClient.swift  Models.swift
    ServerStore.swift  Keychain.swift  Lenient.swift  Transport.swift
  Tests/NexusKitTests/
    SSEParserTests.swift        # frame splitting, keepalives, bare-JSON lines
    EventDecodeTests.swift      # golden fixtures per event kind
```

`SSE.swift` is worth reading before you touch it: it already tolerates the
**bare-JSON guard lines** the server emits on oversize/context-overflow paths
(a line starting `{` that is not `data:`-prefixed is yielded as an event). The
Chrome panel's `readSse` does the same. Don't "clean that up".

## 2. App shell — M1

Keep what `packaging/macos/` already solves — it is 952 LOC of working
infrastructure:

- `ServerController.swift` — spawns `Resources/python/bin/python3 bootstrap.py`,
  polls `~/.nexus/.port` then `/health`, sets `PYTHONHOME`, tees logs to
  `~/Library/Logs/Nexus/server.log`, SIGTERM→SIGKILL on quit.
- `UpdateChecker.swift`, `PreferencesWindow.swift`, `TokenDialog.swift`.
- `packaging/build.sh` — staging, code signing, notarization, `.pkg`.

Change: replace `MenuBarExtra`-only with a `WindowGroup` **plus** a retained
menu-bar item (Open Nexus, Restart Server, Show Logs, Quit). `Info.plist`
currently sets `LSUIElement=YES` (no Dock icon) — that must come out for a real
windowed app.

`HitlNotifier.swift` (305) keeps working but becomes a *fallback*: with a real
window, HITL renders in-app, and a system notification is posted only when the
app is not frontmost. Its inline Approve/Deny for `confirm` without a browser
round-trip is worth extending to `choice`.

**Design language first, components second.** The current UI reads as
unfinished because 82 stylesheets never agreed with each other. Port
`ui/src/tokens.css` (458 LOC — 4 themes × light/dark via a `--brightness`
multiplier) to a Swift `Theme` namespace, then build a dozen primitives
(bubble, card, rail row, inspector header, empty state, toolbar) **once**, and
compose every screen from those.

## 3. Chat — M3, the product

`ios/Nexus/Features/ChatView.swift` is 1,514 LOC of working SwiftUI streaming
chat. **Port and widen it; do not start over.** Also port
`PartViews.swift` (723 — image/audio/document content parts) and
`MarkdownText.swift` (257).

- **Bot rail replaces the session sidebar.** One row per bot, no expandable
  chat lists, coordinator at the top. Selecting a bot opens its single thread
  ([`ws2`](ws2-single-thread.md)).
- **Transcript**: `MessageBubble`, `ThinkingBlock`, `TypingDots`, plus a native
  collapsible tool timeline (the web equivalent is
  `ui/src/components/ActivityTimeline.tsx`, 402 LOC).
- **Composer**: queue chips (`user_enqueued` / `user_injected` /
  `queue_removed` already work end-to-end server-side), `@` mention
  autocomplete, attachments uploaded to the vault first and then referenced by
  path, `/` slash commands (`GET /commands`).
- **HITL in-window**: port `HitlSheet` for confirm/choice/text/form, and
  **implement the 409-parked resume path** (`POST /chat/{sid}/hitl/{rid}/answer`)
  that iOS lacks today. Without it, answering a parked prompt strands the turn.
- **Context meter**: `GET /sessions/{id}/usage` is already memoized on the
  row's `updated_at` (the web UI polls it every 8s). Show the zone colour and a
  "checkpoint now" action — with one endless thread this is the user's only
  window into ws2/ws3 working.
- **Turn re-attach on foreground**: `GET /chat/{sid}/turn/active` then
  `/turn/stream`. iOS already does this; keep it.

**Do not build a second chat surface.** From `tasks/lessons.md`: a full-screen
voice overlay was built alongside the working inline composer and the user
rejected it — *"when a working flow exists, evolve it incrementally; new UI
surfaces multiply failure modes."* Voice mode was rolled back twice. The one
exception worth taking is the **global-hotkey quick-ask panel**
([`ws5`](ws5-prune-and-integrate.md)), because it is a launcher into the same
thread, not a parallel chat.

## 4. Vault as Obsidian — M5

**Collapse four nav items (Vault, Calendar, Kanban, Apps) into one vault
surface.** They are already the same thing: every one is a vault markdown file
dispatched on a frontmatter key by `ui/src/components/VaultEditorPanel.tsx:31-65`.

| Frontmatter key | Native inspector |
|---|---|
| `kanban-plugin: basic` | board; cards are `- [ ] title` + `<!-- nx:id=… -->` |
| `calendar-plugin: basic` | month/week grid; events are `### Title` + `<!-- nx:start=… -->` (stored UTC ISO-8601; `timezone` is display-only) |
| `data-table-plugin: basic` | `Table` over the `## Schema` / `## Rows` fenced YAML |
| `workflow-plugin: basic` | **no editor** — read-only summary |
| folder `_data.md` | dashboard → WKWebView |

1. **Three-pane shell**: file tree · editor · inspector (backlinks, outline,
   tags). `GET /vault/tree`, `/vault/backlinks`, `/vault/tags` all exist.
2. **A real native markdown editor** — the highest-value single investment in
   the port, and the thing that makes it feel like Obsidian. SwiftUI's
   `TextEditor` is not enough: wrap `NSTextView` with syntax highlighting,
   `[[wiki-link]]` autocomplete, click-through and live preview. Link
   resolution already exists server-side in `markdown_links.py` (wiki-links
   with aliases and anchors, inline links with anchors, bare multi-segment paths,
   Obsidian-style unique-stem resolution) — call it, don't re-derive it.
3. **Search**: FTS5 is already there (`GET /vault/search`, 250 ms debounce in
   the web UI). Render native results with snippets.
4. **Keep writes atomic through the API.** Kanban, calendar and table edits go
   through `/vault/kanban`, `/vault/calendar`, `/vault/datatable` — **never**
   by rewriting markdown client-side. Those endpoints own the parse/serialize
   round-trip and fire the FTS, tag, graph and GraphRAG hooks. Saving a note is
   explicit (`PUT /vault/file`); there is no autosave, and the web editor
   suppresses the `vault.indexed` SSE echo of its own write for 750 ms —
   replicate that or the editor will fight the indexer.
5. **GraphRAG stays, hidden.** Keep hybrid retrieval (vector + FTS5 fused by
   reciprocal rank) and the `vault_semantic_search` tool. Remove from the UI:
   the ontology CSV editor, conflict review, entity-resolution staging and
   per-folder graph wizards. One optional "Knowledge graph" web view remains.

## 5. Browser / CDP — M5

Keep the architecture: **two surfaces, one byte-identical probe contract.**
`browser/probes.py` (574) is kept byte-for-byte in sync with `chrome/probes.js`
by a unit test, so the same perception layering (text → element → vision)
serves both the managed Chrome (`browser` tool, CDP) and the user's real tab
(`page` tool, extension). Read `browser-computer-use-spec.md` before changing
anything.

Native work:

- **Live view**: `GET /browser/stream?key=` is SSE with base64 JPEG frames,
  ack-paced to ~4 fps (the *ack* is the throttle — never drop un-acked frames)
  plus `info` events carrying the true CSS viewport. `ios/Nexus/Features/BrowserView.swift`
  (387) already renders this; port it.
- **Keep it watch-only by design.** On macOS the managed Chrome window is right
  there, so add "Bring Chrome to front" rather than an input channel. Note that
  watchers count as activity for the idle reaper, so an open view keeps Chrome
  alive.
- Per-bot isolation already exists: one Chrome per context (`main`, or
  `project:<id>`), resolved **server-side** from the session's `project_id`.
  The LLM never picks the context key, and the native app shouldn't either.

Fixes to land here rather than inventing new features — see
[`fixes-backlog.md`](fixes-backlog.md) items 5–7 and 12: screenshots as image
parts (the single highest-value one), `network`/`console` actions, persisted
NBIE telemetry, the `main`-port collision, and surface-portable flows.

## Verification

```bash
cd packaging/macos && swift build -c release --arch arm64
swift test --package-path swift/NexusKit
```

Manual, in order:

1. Launch the app; confirm `/health`, one streaming turn, one tool call.
2. Open a bot → send 3 messages → exactly **one** chat exists for it.
3. Answer a HITL `confirm` in-window, then a `form`; then park one and answer
   it (the 409 path).
4. Send while a turn is running → a queue chip appears, then becomes a real
   user bubble mid-turn.
5. Relaunch mid-turn → the turn re-attaches and finishes.
6. Vault: create a note, `[[link]]` to another, confirm backlinks; open a
   kanban note, move a card, confirm the markdown round-trips.
7. Browser: start a bot's browser, watch the live view, confirm the idle reaper
   does **not** kill it while you are watching.
