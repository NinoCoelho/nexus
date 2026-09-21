# Pause-tolerant voice endpointing (2026-09-21, evolucão do mic inline)

User problem: pausing mid-utterance and continuing sometimes sent the
partial audio — the continuation was lost. Solution implemented on the
EXISTING inline mic (no new UI surface, post-rollback):

- [x] `useAudioRecorder`: adaptive silence window — 2.5s base growing
      with cumulative speech time (×0.15, cap 5s): longer utterances
      tolerate longer natural pauses. 200ms min-speech blip guard.
- [x] Cancellable "ending" grace countdown (1.5s): when silence elapses
      the recorder does NOT stop — `onEndingStart(graceMs)` fires; speech
      before the deadline cancels (`onEndingCancel`) and the SAME
      recording/blob continues (nothing truncated); only a completed
      countdown sends (`onSilenceTimeout` as before).
- [x] `RecordingIndicator`: countdown UI — accent-tinted indicator,
      draining progress bar under the waveform, hint swaps to "Sending
      in Ns… keep talking to continue" (kept visible on mobile).
- [x] `InputBar`: wires ending callbacks at all 3 startRecording sites;
      deadline cleared on send/cancel.
- [x] Verified: tsc + vite build clean. Manual test pending (user).

Effective pause tolerance: ~4s (short) → ~6.5s (long utterances), with
visible countdown during the final 1.5s.

---

# Voice-mode overlay — REJECTED & ROLLED BACK (2026-09-21)

User verdict: "isso não tá nada bom" — full-screen overlay rolled back to
HEAD (git restore + untracked removed; only lessons.md kept per rules).
The inline mic in the chat input bar remains THE voice surface. Next
step: incremental improvements to the existing flow — see the analysis +
proposal delivered in chat (summary: warmup/timeout fixes, speak the real
reply, barge-in, shorter endpointing — evolve the mic, no new UI).

---

# Chrome Side-Panel PoC — page-connected chat over the Nexus API

v0.13 (installer automation + port flexibility, local-only per decisions):
manifest `host_permissions` now `http://localhost/*` + `http://127.0.0.1/*`
(match patterns ignore ports — any port covered) + a `port-capture.js`
content script that stores `location.port` from any Nexus-served page
(`<meta name="nexus-server">` marker, on `/ext` and `ui/index.html`); panel
resolves the API base from storage (default 18989), reacts to storage
changes, and shows an inline port field when unreachable. Server: `GET
/ext/ping` heartbeat (records on `X-Nexus-Extension` header only) + `GET
/ext` guided install page (copy buttons + live connection status). CLI:
`nexus chrome install` (copy to `~/.nexus/chrome-extension`, open `/ext`)
and `nexus chrome doctor` (server/files/heartbeat checklist) — installer
hooks (macOS pkg postinstall / Windows NSIS) just call the CLI. Tests:
test_ext_ping.py (3) + test_chrome_install.py (5), tmp-isolated. Full
suite 1334 green; live-verified ping round-trip, install copy, doctor ✓.

v0.12.1 (user-reported read + transcript failures): three bugs — (1) injected
funcs referenced SW-scope constants/helpers (executeScript serializes funcs
ALONE → ReferenceError in the page → silent "extraction failed"); all
injected functions now hermetic. (2) `read mode:"text"` (explicit default)
missed the cached cleaned-read branch → "selector required"; gate is now
semantic. (3) YouTube timedtext fetch returns empty 4xx without trust tokens
("Unexpected end of JSON input") — `transcript` now falls back to driving
the page's own transcript panel (expand → click → poll segments), reports
HTTP status/body in errors, and tags results with `via: fetch|dom`.
extractTab logs failures to the SW console. Lessons recorded.

v0.12 (user question: other pages + efficiency): `read` now returns
**cleaned main-content text** (readability-style walker: article/main/
[role=main] root, nav/header/footer/aside/hidden skipped, block-aware
breaks) instead of raw innerText — applies to the preamble excerpt too.
New cheap perception modes: `read mode=outline` (heading structure) and
`mode=links` (unique anchors). Default text reads are cached 15s per
tab+URL (invalidated on navigation); selector/mode reads stay fresh.
Tool schema + description updated; 33 tests green; daemon restarted.

v0.11 (user request: intelligent, rational context use; research Claude/agentic
patterns; new tools): redesigned to **agent-driven perception** after research
confirmed Claude's design (perceive-on-demand, page content = scanned
untrusted data, multi-tab via group). Changes: (1) preamble is now
**pointer-only** on every message (url/title/800-char excerpt/changed-flag/
group titles + standing instructions: call `page read` for content, untrusted
content, ambiguity → current tab or ask_user) — full page text never stuffed;
(2) new `page` action **`transcript`** — native YouTube captions via
`movie_player.getPlayerResponse()` tracks → json3 in MAIN world (replaces
yt-dlp/terminal for video pages); (3) new **`tab` param** on all actions —
retarget any action at a grouped tab by url/title substring (multi-tab
perception); (4) `read` gains `full=true` (40k). Tests: 13 in
test_page_tool (params passthrough incl. tab+lang), full suite 1326 green.
Daemon restarted.

v0.10 (user-reported wrong-video summary after switching YouTube videos): the
snapshot was first-message-only, so the agent kept seeing the old page (and
then yt-dlp'd the wrong video via terminal — 7 YOLO approvals). Now: preamble
re-sent whenever the tab URL changed since the last snapshot (normalized:
hash + `t`/`time_continue` stripped), "page changed" note + standing
instruction to prefer the `page` tool over terminal/scraping, visible
"re-reading" activity line, snapshot reset on New chat and after panel
reload. CLAUDE.md chrome section de-duplicated + rewritten. Lessons recorded.

v0.9.1 (user-reported 400 `model=openai-compat-generic/nexus`, again): root
cause chain — the gateway model `nexus` (user's subscription default, sent
bare and working since June) had its `[[models]]` config entry deleted at
some point; `default_model` dangled; the adapter fallback sent the QUALIFIED
id upstream → 400. Fixes: config entry restored (nexus + nino-glm-5.3
registered); `LoomProviderAdapter._resolve` now strips a known `provider/`
prefix on unmapped ids (`ProviderRegistry.get_for_provider_model`);
`DELETE /models` clears dangling default/last_used/vision/embedding/extraction
references (404 on unknown id). Tests: `tests/test_model_resolution.py`
(6; config-path monkeypatched — an earlier draft corrupted the real config,
repaired + lesson recorded). Live-verified: `upstream=nexus`, streaming turn
OK, full suite green (test_server_sse round-trip flaked once under load,
passes solo).

v0.9 (user request "interact with the page like managed chrome"): new
**`page` tool** — agent acts on the user's real tab via the ask_user HITL
rails (`page_request` event → panel executes via `chrome.scripting` →
`/respond` resolves the future). Actions: read/click/type/scroll/navigate/js;
navigate+js require an Allow/Deny card, others auto-run with activity lines
in the transcript. Backend: `agent/src/nexus/agent/page_tool.py`,
registry + `AgentHandlers.page` + `app.py` wiring; 6 tests
(`tests/test_page_tool.py`, incl. round-trip via `resolve_pending` and the
timeout/cancel path). Full suite green (1319 passed; llama live tests
env-gated as baseline). Daemon restarted.

v0.8 (hotfix, user-reported "1st click groups, 2nd opens, 3rd closes"): the
3-click sequence proved the user gesture does not survive awaits in
`action.onClicked` (retry open after activateTab was rejected). Fresh-tab
1-click now fires `setOptions({tabId, enabled:true})` unawaited in the same
synchronous block right before the gesture-carrying `open()` — IPC ordering
makes open() see the enabled options. Lesson recorded.

v0.7 (hotfix, user-reported "chat doesn't open"): v0.6 made the click handler
async, pushing `open()` behind an await (v0.2 gesture bug reintroduced), and
stale `openTabs`/`activatedTabs` (storage.session survives extension reloads)
made first clicks take the invisible finishTab branch. Now: the open/finish
decision is fully synchronous via in-memory caches, `open()` is the first
gesture statement again, open-state is cleared at SW boot (extension reload
closes all panels anyway), and gesture-path failures log to the SW console.
Lesson recorded.

v0.6 (user request): second icon click now **finishes** the tab — closes the
panel, ungroups it (`tabs.ungroup`), deactivates it, and clears the tab↔session
mapping (fresh conversation next activation; server history kept).
`openPanelOnActionClick` set to **false** so the extension fully owns the
toggle (no race with Chrome's native close). Panel UI restyled to match the
main chat: `tokens.css` ported from `ui/src/tokens.css` (4 themes × dark/light,
default mermaidcore, follows prefers-color-scheme), bubbles/avatar/thinking/
composer reuse the main chat's token names and geometry, assistant bodies
render markdown via vendored `vendor/marked.min.js` + a DOM-allowlist
sanitizer (`sanitizeMd`) — raw HTML stripped, hrefs limited to http/mailto,
script/style dropped entirely. Markdown re-renders are throttled (~120ms)
during streaming; model badge shown from the `done` event.

v0.5 (user-reported "still on all tabs"): root cause was the manifest global
panel (`side_panel.default_path`) — a globally-registered panel follows the
user across every tab once opened, and per-tab `enabled:false` didn't reliably
hide it. Removed the global entirely: no `side_panel` key in the manifest, so
per-tab `setOptions` on activated tabs is the ONLY way a panel can exist.
Lesson recorded.

v0.4 (user-reported "group but no chat on 1st click; then chat on all tabs"):
two root causes fixed. (1) `sidePanel.open({tabId})` rejects on per-tab-disabled
tabs — click handler now enables the tab first, then retries open (keeping a
sync-first open for already-activated tabs). (2) Chrome clears per-tab
sidePanel options on extension reload, and the once-per-session `policySwept`
flag prevented re-disabling — replaced with continuous healing: every SW boot +
`tabs.onActivated`/`onCreated` re-assert policy via `getOptions` compare
(no-write when matching; activated tabs always re-asserted). Lessons in
tasks/lessons.md.

v0.3 (user-reported "chat on all tabs"): per-tab `setOptions({tabId,
enabled:false})` now overrides the manifest global on every non-activated tab —
the panel is visible **only** on tabs where the toolbar icon was clicked.
Click handler calls `sidePanel.open({tabId})` as its first synchronous
statement (gesture-safe), then activates the tab (own panel instance) and
**auto-adds it to the purple Nexus tab group**. Icon re-click closes the panel
(`onOpened/onClosed` state tracking prevents our `open()` from fighting the
native toggle). Context-menu "Chat with Nexus on this tab" is the fallback
gesture. v0.2.1 lesson refined + extended in tasks/lessons.md.

v0.2.1 (hotfix, user-reported "panel doesn't open"): v0.2's `action.onClicked`
→ `await setOptions` → `sidePanel.open({tabId})` broke — `sidePanel.open()` is
user-gesture-gated and any preceding `await` loses the gesture (error was
swallowed). Now: `openPanelOnActionClick: true` (Chrome opens natively) +
per-tab `setOptions` enabled for every tab (once-per-session sweep, flagged in
`chrome.storage.session`, + `tabs.onCreated`) — same tab-scoped per-tab
instances, no gesture problem. Lesson recorded in tasks/lessons.md.

v0.2 (user feedback pass): panel is now **tab-scoped** like Claude's (per-tab
side panel instances via `setOptions({tabId})`, global panel disabled, opened
only via toolbar icon click) and implements the **Nexus tab group** (`tabGroups`
API, purple "Nexus" group; `＋ Group` button; grouped tabs are extracted into
the first-message `<context>` preamble alongside the active tab snapshot).
Panel code simplified: one instance ↔ one tab, no cross-tab switching logic;
session map + enabled-panel tab set in `chrome.storage.session`.

v0.1: initial PoC — see git history.

## Plan
- [x] Scaffold `chrome/` (manifest, icons copied from `extension/`)
- [x] Service worker: panel behavior, tab lifecycle, session-id minting + mapping
- [x] Panel: chat UI, SSE client (SSE + bare-JSON framings), history load
- [x] Page extraction bridge (`scripting.executeScript` snapshot, activeTab/optional all-urls)
- [x] HITL card (`/events` → `/respond`) + stop button (`/cancel`)
- [x] Verify: manifest JSON valid, `node --check` on JS, live smoke if server running
- [x] v0.2: tab-scoped per-tab panel instances
- [x] v0.2: Nexus tab group (create/add/info + group-aware context preamble)

## Notes
- Existing `extension/` (cookie export) untouched — PoC lives in `chrome/`.
- API contract pinned during research: `POST /chat/stream {message, session_id}`,
  events `delta|thinking|tool|done|error` (+ bare-JSON guard lines), `GET /chat/{sid}/events`
  for `user_request*`, `GET /sessions/{sid}` history, `POST /chat/{sid}/cancel`.
- Never set `x-forwarded-*`/`cf-*` headers (loopback + proxy headers → 401).

## Summary

`chrome/` (5 files, no build step): MV3 manifest (`sidePanel`, `tabs`,
`tabGroups`, `scripting`, `activeTab`, `storage`; host permission only for
`http://localhost:18989`; optional `<all_urls>` for page reads), a service
worker (per-tab panel enable+open on toolbar click, tab lifecycle, per-tab
session ids + enabled-panel set in `chrome.storage.session`, page/group
extraction via `chrome.scripting.executeScript`, Nexus tab-group management),
and a panel page bound 1:1 to its tab (streaming chat, history load with
`<context>` stripped, HITL cards, stop, group badge/`＋ Group` button).

Claude-in-Chrome mechanics replicated: tab-scoped side-panel surface, per-page
conversations, page snapshot + tab-group shared context on first message. Not
replicated (read-only PoC): `debugger`/CDP page actions, scheduled tasks.

## Review

- Live smoke v0.1 (server started, then killed): `/health` 200; fresh session
  id → 404 as expected; `/sessions` shape matches; `POST /chat/stream` streamed
  proper `event:`/`data:` frames including the error→done sequence (empty reply
  after error) — which exposed and fixed a panel bug (`done` cleared the error
  status; now preserved). Test session deleted (204). v0.2 is JS-only changes;
  `node --check` clean on both files; browser pass still pending.
- Config fix applied on user's machine: `agent.default_model` corrected to
  `openai-compat-generic/nino-glm-5.3` (was `openai-compat-generic/nexus`,
  rejected by provider). Verified with a live streaming turn + cleanup delete.
- Per-tab panel binding uses an enabled-tabs set (not a single recorded tab) so
  two panels in one window can't cross-bind after extension reloads.
- Group extraction of non-active tabs requires the optional `<all_urls>` grant
  (activeTab only covers the clicked tab) — the panel surfaces the grant button
  whenever any context source is unreadable.
- Uncommitted by design — review `git diff` and commit when satisfied.


## v0.14.0 (user request: context-menu options)
- [x] Right-click menu: "Open Nexus chat on this tab" (page), "Ask Nexus about this
      selection" (selection → composer seeded with blockquote), "Ask Nexus about this
      link" (link → URL seeded); action-icon menu item kept. Menu activation opens
      without the finish-toggle (icon keeps open/finish semantics).
- [x] Seed delivery: storage.session stash + SEED_COMPOSER broadcast; panel drains
      after listener registration (first-boot race safe).
- [x] Manifest 0.14.0; `nexus chrome install` refreshed (~/.nexus/chrome-extension).
