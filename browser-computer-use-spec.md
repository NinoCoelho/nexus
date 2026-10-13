# Embedded Browser (Computer-Use) — Technical Spec

Portability spec for Qorvi's browser control subsystem, written so it can be
re-implemented in any stack (Python/Node/Go/Rust). It is ~350 LOC of host code
plus a WebSocket client; no browser engine is embedded.

## 1. Model

A **real Chrome/Chromium instance** is launched as a separate OS process with a
**dedicated user profile** and a CDP (Chrome DevTools Protocol) debugging port.
The host app talks to it over two channels:

| Channel | Protocol | Use |
|---|---|---|
| `http://127.0.0.1:<port>/json/*` | HTTP | liveness probe, target discovery, new tab |
| `ws://127.0.0.1:<port>/devtools/page/<targetId>` | WebSocket (JSON-RPC) | all commands (navigate, evaluate, input, screenshot) |

Why not Puppeteer/Playwright? Zero dependency weight and full control; the whole
surface is ~15 CDP methods. (If you already ship Playwright, you can implement
this spec on top of it and skip §3 — the action set, events, and endpoints stay
identical.)

## 2. Process lifecycle

- **Probe first**: `GET /json/version` (1.5s timeout). If 200 → reuse existing
  Chrome (crash-safe, idempotent).
- **Auto-launch** if probe fails:
  ```
  <chrome-binary>
    --remote-debugging-port=9223
    --user-data-dir=~/.qorvi/chrome-profile     # dedicated profile, never the user's daily profile
    --no-first-run --no-default-browser-check
    --window-size=1440,900                       # fixed logical viewport; UI scales clicks to this
    about:blank
  ```
  Binary discovery order: macOS app bundle paths → `/usr/bin/google-chrome` /
  `chromium` → `which`. Poll `/json/version` every 250ms up to 10s.
- **Port choice**: 9223 (9222 is Chrome's conventional devtools port; using a
  non-default port avoids hijacking a user's own debug session).
- The Chrome process is **left running** across app restarts (probe reuses it);
  kill it explicitly if your product wants hermetic cleanup.

## 3. CDP connection management

1. `GET /json/list` → JSON array of targets. Filter `type == "page"`.
2. Target selection: prefer a target with `url == "about:blank"` (avoids
   fighting a restored tab), else the **last** page in the list. If no pages:
   `PUT /json/new?about:blank` then re-list.
3. Connect WebSocket to the target's `webSocketDebuggerUrl`
   (`max_size: 64MB` — screenshots can be large).
4. **JSON-RPC framing**: `{"id": n, "method": "...", "params": {...}}` in;
   responses matched by `id` into a `pending: dict[int, Future]` map. A single
   reader task (`async for` on the socket) resolves futures; on socket error it
   fails all pending with `{"error": {"message": "connection closed"}}`.
5. Per-call timeout: 30s. `error` key in response → raise with `message`.
6. **Readiness check before reuse**: socket open AND `/json/version` reachable;
   otherwise close + reconnect (see gotchas: in `websockets>=14` use
   `ws.protocol.state is State.OPEN`; `.closed` was removed).

Only **one connection + one lock** (`asyncio.Lock` around `ensure()`). All
actions call `ensure()` first — lazy connect, transparent reconnect.

## 4. Action set (the tool contract)

A single tool named `browser` with an `action` enum — keeps the LLM tool list
small. JSON-Schema parameters (union of all action params):

```
action: enum[navigate, read, dom_text, click, type, scroll, screenshot, js, wait_for, info]
url?: string            # navigate (bare host gets https:// prefixed)
selector?: string       # CSS selector (click, type, wait_for)
text?: string           # type
x?, y?: number          # click (viewport coordinates, alternative to selector)
delta_y?: number        # scroll (positive = down)
expression?: string     # js (await-capable)
timeout_seconds?: number (default 8)  # wait_for
```

Semantics per action (CDP methods in parens):

- **navigate** — `Page.enable`, `Page.navigate {url}`, then poll
  `location.href` every 250ms up to 10s until it differs from `about:blank`;
  return `{url, title}` from `info`. (A fixed sleep is NOT enough: on cold
  profile start the navigation lands late.)
- **info** — `Runtime.evaluate` of `location.href` and `document.title`.
- **read** — single `Runtime.evaluate` with `returnByValue: true` returning
  `JSON.stringify({url, title, text: body.innerText.slice(0, 8000),
  links: first 80 <a> {t: text(60), h: href}})`. One round-trip, token-bounded.
- **dom_text** — `body.innerText.slice(0, 12000)` (bigger, link-free).
- **click** — if `selector`: evaluate an IIFE that `scrollIntoView({block:'center'})`,
  `getBoundingClientRect()`, returns `{x: center, y: center}` (null if not
  found → error result). Then **real input events**, not `el.click()`:
  `Input.dispatchMouseEvent` `{type: "mousePressed", x, y, button: "left",
  clickCount: 1}` followed by `type: "mouseReleased"`. Sleep ~600ms, return
  `{url, title}`. Real events matter for SPAs and shadow-DOM handlers.
- **type** — if `selector`, focus it via JS first. Then
  `Input.insertText {text}` (one call for the whole string — dispatching
  per-char `Input.dispatchKeyEvent` is slow and breaks IME/composition).
- **scroll** — `Input.dispatchMouseEvent` `{type: "mouseWheel", x: 400, y: 400,
  deltaX: 0, deltaY: <delta_y>}`.
- **screenshot** — `Page.captureScreenshot {format: "png"}` → base64 → decode
  to `~/.qorvi/browser/shot-<ms>.png`. Keep newest 20, delete older. Return
  `{screenshot: <abs path>, url, title}`.
- **js** — `Runtime.evaluate {expression, returnByValue: true, awaitPromise:
  true}`; surface `exceptionDetails.exception.description` as error.
- **wait_for** — poll `!!document.querySelector(selector)` every 250ms until
  `timeout_seconds`, then error.

All action results are returned to the LLM as **JSON strings**; failures return
`ToolResult(is_error=True)` with a `browser error: ...` message rather than
throwing (the loop survives and can retry).

## 5. Event bridge (live view)

Every action (success or error) emits a **`browser_event`** on the app's event
bus (session-scoped SSE + global notification SSE):

```json
{"action": "navigate", "url": "https://example.com/", "title": "Example Domain"}
{"action": "click", "x": 512, "y": 300, "url": "...", "title": "..."}
{"action": "screenshot", "screenshot": "~/.qorvi/browser/shot-1731...png", "url": "..."}
{"action": "error", "error": "element not found: a"}
```

The current chat session id is carried via a **contextvar** set around each
agent turn, so browser activity during a turn is attributed to that session's
stream. Manual (human) actions emit with a pseudo-session `*browser*`.

## 6. HTTP surface (for the UI / external clients)

```
GET  /browser/status            → {connected, cdp_port, url?, title?} | {connected:false, error}
POST /browser/action            body = action params (same as §4) → action result JSON
GET  /browser/screenshot        → image/png (captures one first if none); 503 on failure
GET  (SSE) /notifications/events → filter kind == "browser_event"
```

UI panel (`#/browser`): URL bar, status dot (poll status 5s), `<img
src=/browser/screenshot?ts=...>` refreshed on every `browser_event` and after
manual actions, action bar (scroll ±600, read, type), last-20 event log.

**Human takeover by click**: the `<img>` is clicked at pixel (px, py); UI
scales to the fixed logical viewport: `x = px / imgWidth * 1440`,
`y = py / imgHeight * 900`, then `POST /browser/action {action:"click", x, y}`.
Because the viewport is pinned at launch (`--window-size=1440,900`), scaling is
deterministic regardless of window DPI.

## 7. Agent-loop integration

- Tool registered in the same registry as `terminal`/`http_call`; the agent
  discovers it via tools/list and drives the loop
  perceive → act → verify: `read`/`screenshot` → reason → `click`/`type` →
  `screenshot` to confirm.
- Vision loop note (v1 limitation): screenshot returns a **path**, not an
  image part — vision models consume it via textual tool results today; to
  make it a true computer-use vision loop, return the PNG as a multimodal
  `content_part` on the tool result message instead.
- Gate: config `[browser] enabled` (default true), `cdp_port`. Disabled ⇒
  tool not registered, endpoints 404.

## 8. Gotchas discovered during implementation

1. `websockets>=14`: `.closed` is gone — use `protocol.state`.
2. Restored tabs in a reused profile: `/json/list` returns several pages;
   always prefer `about:blank` target or the **last** one, else you navigate
   a stale background tab while reading another.
3. `Page.navigate` resolves before commit — poll `location.href`.
4. `Input.insertText` over per-key events for typing.
5. CDP target WebSocket is **per-page**; browser-level WS requires flat
   session protocol (`Target.attachToTarget` + `sessionId`) — unnecessary
   complexity for a single-tab automation surface.
6. `Runtime.evaluate` JSON.stringify pattern: return composite reads in one
  round-trip; keep `returnByValue: true` or you get remote object refs.
7. Screenshot retention cap (20 files) prevents unbounded disk growth on
  long agent runs.
