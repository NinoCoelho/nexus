
## 2026-09-04 — graphrag reindex failure (user-reported)
- **Pattern:** When refactoring label/type lookup maps consumed by NLP model output, enumerate the FULL label set from every model variant in play (en_core_web_sm AND pt_core_news_sm emit different labels — pt has MISC). Never use bare `MAP[label]` on model-produced labels; always `.get()` with a fallback.
- **Pattern:** Test the non-default language path: a change verified only against en CoreWeb silently broke the pt pipeline (the user's vault is largely Portuguese).
- **Pattern:** Long-running CLI commands must show progress (percent + running totals) from the first minute. Reuse existing streaming generators instead of the silent full-scan variant — "no output" reads as "frozen/failed" to users.

## 2026-09-20 — Chrome extension panel stopped opening after v0.2 (user-reported)
- **Pattern:** `chrome.sidePanel.open()` is user-gesture-gated and loses the gesture after ANY `await` in the same handler — never `await setOptions(...)` before `open(...)` in `action.onClicked`. Prefer `sidePanel.setPanelBehavior({openPanelOnActionClick: true})` and let Chrome open the panel natively; get per-tab instances by enabling tab-specific `setOptions({tabId})` for all tabs instead of calling `open()` yourself.
- **Pattern:** Don't swallow errors in UI-critical paths (`.catch(() => {})` on open made the failure silent). During iteration, log the catch to the SW console.
- **Pattern:** Idempotent-looking Chrome APIs (setOptions) can still disturb live UI — gate one-time sweeps with a `chrome.storage.session` flag instead of re-running them on every service-worker wake.

## 2026-09-20 — v0.3: per-tab panel visibility (user-reported "chat on all tabs")
- **Pattern:** Enabling per-tab side panel options on ALL tabs makes the panel visible everywhere once opened — per-tab `setOptions({tabId, enabled:false})` is what overrides the manifest global and hides the panel on a tab. Scope visibility by enabling only user-activated tabs (cookbook.sidepanel-site-specific is the canonical sample).
- **Pattern:** `sidePanel.open({tabId})` works even before that tab has per-tab options (falls back to the global path) — so the gesture-safe order is: call `open()` first/synchronously in the click handler, then `await setOptions` to convert the tab to its own instance.
- **Pattern:** `action.onClicked` + `openPanelOnActionClick: true` can double-fire (native toggle + own `open()`); track open state via `sidePanel.onOpened/onClosed` (Chrome 141+) and skip your own `open()` when already open, or the icon can never close the panel.
- **Pattern:** Guard optional-event APIs (`if (chrome.sidePanel.onOpened)`) at SW top level — a missing API throws at registration and kills the entire service worker.
- **Pattern:** `chrome.contextMenus.create()` is callback-style in MV3 — it does NOT return a promise, so `.catch()` on it throws ("catch is not a function"). Use the callback form with `() => void chrome.runtime.lastError` to swallow duplicate-id errors.

## 2026-09-20 — v0.4: first click didn't open; global panel leaked after reload (user-reported)
- **Pattern:** `sidePanel.open({tabId})` is REJECTED when the tab's per-panel options say `enabled:false` — you must `setOptions(tabId, enabled:true)` first, then open (retry). Keep a sync-first open() for the already-enabled case, then retry after enabling for fresh tabs.
- **Pattern:** Chrome clears per-tab sidePanel options when the extension is reloaded — a once-per-session flagged sweep goes stale. Heal instead: on every SW boot + `tabs.onActivated`/`onCreated`, compare `getOptions({tabId})` against policy and only write on mismatch (idempotent, no open-panel churn).
- **Pattern:** For "activated tab" state, always re-assert `setOptions` (getOptions can't distinguish tab-specific options from identical global defaults — a lost option would silently fall back to the shared global panel instance).

## 2026-09-20 — v0.5: global panel was the everywhere-leak (user-reported)
- **Pattern:** A manifest `side_panel.default_path` (global panel) follows the user across EVERY tab once opened — per-tab `enabled:false` did not reliably hide it. For tab-scoped panels, register NO global at all; per-tab `setOptions({tabId, enabled, path})` is then the only way a panel can exist, and per-tab panels don't leak across tabs.
- **Pattern:** `openPanelOnActionClick` + no global + no per-tab options = native click is a no-op on fresh tabs; the extension's own `onClicked` (enable → retry open) is the real activation path — gesture survives awaits in current Chrome (confirmed live).

## 2026-09-20 — v0.7: chat stopped opening after v0.6 (user-reported)
- **Pattern:** Making the click handler `async` pushed `sidePanel.open()` behind an `await` — reintroduced the v0.2 gesture bug through refactoring. Keep the open/toggle decision synchronous (in-memory state caches), and only touch storage in the async tail.
- **Pattern:** `storage.session` persists across *extension reloads* (only browser restart clears it) — stale `openTabs` + `activatedTabs` made every click take the invisible finishTab branch. Extension reload closes all panels anyway: clear open-state at SW boot.
- **Pattern:** Log gesture-path failures (`console.warn`) instead of `.catch(() => {})` — silent catches made this failure mode undiagnosable from the SW inspector.

## 2026-09-20 — v0.8: two clicks needed on fresh tabs (user-reported 3-click sequence)
- **Pattern (confirmed):** in `action.onClicked`, the user gesture does NOT survive `await`s — a retry `open()` after async work is rejected while a same-tick `open()` succeeds. 1-click open on un-enabled tabs: fire `setOptions({tabId, enabled:true})` **unawaited in the same synchronous block** right before `open()` — Chrome processes the IPC calls in submission order, so `open()` sees the enabled options without breaking the gesture chain.

## 2026-09-20 — model 400 regression: deleted [[models]] entry left default_model dangling
- **Pattern:** `DELETE /models` didn't clean `default_model`/`last_used_model`/role references — a dangling id then rides the adapter's fallback, which passed the QUALIFIED id (`provider/model`) upstream; gateways reject it. Deletes must clear/reassign references (now fixed), and the adapter fallback now strips a known `provider/` prefix to the bare upstream name.
- **Pattern:** NEVER let a test call `config_file.load()/save()` unmocked — `CONFIG_PATH` is a module constant pointing at the real `~/.nexus/config.toml`; a delete-model test silently replaced the user's models list. Monkeypatch `nexus.config_file.CONFIG_PATH` to tmp_path in any test that saves config.

## 2026-09-20 — v0.10: stale page context after in-tab navigation (user-reported wrong-video summary)
- **Pattern:** A per-tab session whose snapshot is taken only on the first message goes stale the moment the tab navigates (YouTube video → video). Track the snapshotted URL and re-extract on mismatch when sending — normalizing URLs (strip hash + `t`/`time_continue`) so seek-driven URL churn doesn't re-trigger.
- **Pattern:** The agent won't reach for the `page` tool on its own in panel sessions — it defaulted to the chrome-devtools/yt-dlp path via terminal (7 YOLO auto-approvals). Bake a standing instruction into the `<context>` preamble: "prefer the page tool over terminal/scraping for this tab."

## 2026-09-20 — v0.11: pointer-only context + agent-driven perception
- **Pattern (from Claude/Anthropic design):** browser agents perceive pages on demand (agent-driven reads, content scanned as untrusted data) — they don't stuff page text into user turns. Stuffing caused both stale answers (wrong video) and lazy behavior (model answering from history without re-reading). Preamble should carry a POINTER (url/title/excerpt/changed-flag) + tool steering; the model decides to read.
- **Pattern:** Video transcripts: prefer native caption-track extraction in the page (player API → json3 fetch, same-origin) over yt-dlp via terminal — no subprocess, no HITL approvals, works with the user's session.
- **Pattern:** Multi-tab agents need per-action tab targeting (substring match against the tab group), not just the active tab.

## 2026-09-20 — v0.12: read efficiency (cleaned extraction, targeted modes, cache)
- **Pattern:** Raw `innerText` spends the token budget on nav/footer/cookie noise. A DOM-walker cleaner (main-content root + noise-selector skip-list + block-aware line breaks) in the injected function costs nothing extra per read and makes both the excerpt and full reads higher-signal.
- **Pattern:** Give the agent *cheaper perception modes* (outline/links) so it can answer structural questions ("what sections does this page have?", "which links?") without paying for full text.
- **Pattern:** Repeated identical reads within one turn are wasted IPC + tokens — cache per (tabId, url, cap) with a short TTL (15s), invalidated on navigation; selector/mode reads stay uncached (they vary).

## 2026-09-20 — v0.12.1: injected funcs must be hermetic; empty timedtext; mode:"text"
- **Pattern:** `chrome.scripting.executeScript({func})` serializes the function ALONE — outer-scope constants/helpers (NOISE_SELECTOR, BLOCK_TAGS, pageOutline) do NOT travel and throw ReferenceError *in the page*, surfacing only as silent null results. Every injected function must be fully self-contained (constants inlined).
- **Pattern:** LLMs pass enum defaults explicitly — gate on the *semantic* default (`mode === undefined || mode === "text"`), not just absence, or the "explicit default" call takes the wrong branch.
- **Pattern:** YouTube `timedtext` fetches from extensions increasingly return HTTP 4xx with EMPTY body (missing trust/PoT tokens) — "Unexpected end of JSON input" is the signature. Fallback: drive the page's own transcript UI (expand description → click transcript button → poll `ytd-transcript-segment-renderer`) which needs no tokens. Always surface HTTP status + body length in the error.
