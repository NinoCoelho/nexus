# Nexus → native macOS app: implementation handoff

**Audience: a developer picking up this port on an Apple Silicon Mac, who has
not seen the discussion that produced it.** Everything here is verified against
the source at commit `542bfb5`; file:line references are from that tree.

Read this file first, then the workstream file for whatever you're assigned.

| File | Contents |
|---|---|
| `README.md` (this) | Goals, decisions, current state, architecture, sequencing, environment setup, verification |
| [`ws1-mlx-provider.md`](ws1-mlx-provider.md) | In-process MLX provider |
| [`ws2-single-thread.md`](ws2-single-thread.md) | One chat thread per bot |
| [`ws3-dream-context-curator.md`](ws3-dream-context-curator.md) | Why dreaming is inert, and its rework into the context curator |
| [`ws4-native-ui.md`](ws4-native-ui.md) | Swift core, app shell, chat, vault, browser |
| [`ws5-prune-and-integrate.md`](ws5-prune-and-integrate.md) | What to delete/hide, Apple-native integrations |
| [`fixes-backlog.md`](fixes-backlog.md) | 12 standalone defects found during review, each independently shippable |

---

## 1. What we're building and why

Nexus today is a Python/FastAPI backend (~94k LOC, 383 modules, 191 test files /
~1,983 tests) plus a React 19 + Vite web UI (~97.5k LOC, 451 files). It already
ships a macOS `.app`, but that app is only a **menu-bar supervisor**:
`packaging/macos/Sources/Nexus/ServerController.swift` spawns a bundled CPython
and `NexusApp.swift` then calls
`NSWorkspace.shared.open("http://127.0.0.1:<port>/")`, handing the UI to the
user's default browser. There is no native window.

Five problems to solve:

1. **No native app.** The UI is a browser tab, and it reads as unfinished —
   ~120 top-level components accreted over 585 commits with no unifying design
   (82 stylesheets, 26.9k LOC of CSS).
2. **No MLX.** Local inference goes through a supervised `llama-server`
   subprocess, leaving the best Apple Silicon runtime unused.
3. **Too many chats.** Many visible sessions per bot confuses users. The
   product should be **one chat window per bot** that manages its own context
   without losing track.
4. **Vault is shallow.** It should feel like Obsidian; calendar / kanban /
   database should be inspectors on vault documents, not separate nav items.
5. **Dreaming is inert.** It runs, spends tokens, and produces nothing
   actionable — and it is the natural home for the context-archival work a
   single endless thread needs. See [`ws3`](ws3-dream-context-curator.md).

Guiding constraint: **simpler on the surface, more capable underneath.** Every
confusing power feature is either hidden or deleted, not left in the nav.

## 2. Decisions already made

These were settled with the product owner. Do not relitigate them without
asking; do raise it if implementation shows one is wrong.

| Decision | Choice |
|---|---|
| Native strategy | **Native core + WKWebView for power tools.** SwiftUI for chat, vault, boards, calendar, browser, settings. Web view only for the force-directed graph and the DuckDB dashboards. |
| Platform scope | **macOS is the product.** Keep the **Chrome extension** and the **iOS app**. Windows/Linux/Docker packaging and the web UI stop being maintained targets. |
| MLX runtime | **In-process provider** — `mlx_lm` imported directly, not a supervised HTTP server. |
| Backend | **Stays Python.** 1,983 tests are the reason; the HTTP/SSE API is already a native-client contract. |
| Delete now | Cookie-export extension (`extension/`) and the ACP bridge. |
| Dream / Workflows | Keep the engines running; **no dedicated UI**. |

**Open question, still unanswered — do not act on it:** whether `telegram/`
(4,279 LOC, the most actively developed subsystem at 24 commits/90d) is parked
or deleted. Current plan **parks** it: no native UI work, its binding repoints
to the bot's main thread ([`ws2`](ws2-single-thread.md)), code stays and keeps
running. Ask the product owner before deleting.

## 3. Five mistakes already made — do not repeat them

The first draft of this plan contained five errors. All are verified; the
workstream docs reflect the corrections. They are listed here because each one
is an easy trap to fall into independently.

1. **`CACHE_BREAKPOINT_MARKER` is in `agent/prompt_builder.py:31`**, not
   `agent/llm/prompt_cache.py` (which only imports it). Same for
   `split_cache_zones` (`:804`) and `strip_cache_marker` (`:818`).
2. **`LLMProvider.chat_stream` has no `extra_payload` parameter**
   (`agent/llm/types.py:164-171`), and `LoomProviderAdapter` calls it with only
   `tools/model/max_tokens` (`agent/_loom_bridge/adapter.py:232`). There is
   **no per-call channel to tell a provider which bot a generation is for.**
   Use the existing `agent.context.CURRENT_SESSION_ID` ContextVar (set by
   `ChatTurnRunner`, visible in the provider because it's the same task). Do
   **not** widen the signature — 4 providers, the adapter, and ~6 tests.
3. **Cross-turn prefix KV caching does not work as-is.** The whole system
   prompt is assembled into **one** SYSTEM message placed *first*
   (`agent/loop/_builder.py:290-293`), and the volatile zone inside it contains
   `_time_location_block()` (`agent/prompt_builder.py:745`), which changes every
   minute. The first mismatching token is ~15K in, before turn 1. See
   [`ws1`](ws1-mlx-provider.md) for what is and isn't achievable.
4. **`agent/discovered_windows.py` is the wrong seam for context windows.**
   `overflow.known_context_window` (`agent/loop/overflow.py:257-288`) does an
   exact match, then a longest-**substring** match over `KNOWN_WINDOWS`, and
   only *then* consults discovery. `"qwen3": 128_000` is a literal key
   (`overflow.py:227`), so `mlx-community/Qwen3-30B-A3B-4bit` resolves to 128K
   and never reaches discovery. Write `context_window` onto the `[[models]]`
   config entry instead — `_builder._model_context_window:303-308` checks that
   **first**.
5. **Never name an MLX provider `local-*`.**
   `local_llm/manager.py::cleanup_stale_config()` (`:897-940`) deletes every
   provider named `local` or prefixed `local-` that is absent from the
   llama-server process table `_servers`, prunes its `[[models]]` rows, and
   clears a `local/`/`local-`-prefixed `default_model`. An in-process provider
   is never in `_servers`, so it would be wiped on every boot. Use `mlx-<slug>`
   and add a regression test.

## 4. Current state

| Area | Size | Note |
|---|---|---|
| `agent/src/nexus` | 93.8k LOC / 383 `.py` | FastAPI + loom agent loop |
| `agent/tests` | 41.8k LOC / 191 files | ~1,983 tests — the regression net |
| `ui/src` | 97.5k LOC / 451 files | 82 CSS files, 26.9k LOC of CSS |
| `agent/src/nexus/server/routes` | 17.5k LOC / 52 modules | ~300 endpoints |
| `ios/` | 6.4k LOC / 20 `.swift` | SwiftUI, zero third-party deps |
| `chrome/` | 5.9k LOC | no-build MV3 side panel |
| `packaging/macos` | 952 LOC Swift | bundled-Python supervision, signing, notarization |
| `skills/` | 50 skills / 12k LOC | markdown + scripts |

Four leverage points that shape every decision below:

- **The API is already a native-client contract.** The iOS app consumes it
  today, including turn re-attach (`routes/chat.py:650-714`), queue events,
  HITL forms and the browser screencast. The macOS app is the same job on a
  bigger canvas, not a new protocol.
- **`ios/Nexus/Core/` is 1,508 LOC of exactly the right thing** and imports
  only `Foundation`, `Security` and `Observation` — **no UIKit anywhere**.
  Extracting it to a shared SwiftPM package is mechanical and is task one.
- **Context management is already strong.** Microcompaction to
  `vault://.tool-cache` pointers, soft auto-compact with a growth watermark,
  the LLM-free `hard_trim` backstop, FTS5 `history_recall` over the `.parts`
  archive, idle checkpointing, estimator calibration. Extend it; do not replace
  it.
- **The browser/CDP subsystem is a crown jewel, not a rewrite target.**
  `browser/probes.py` (574 LOC) is kept byte-identical to `chrome/probes.js`
  with a test guarding drift, so one perception contract (text → element →
  vision) serves both the managed Chrome and the user's real tab.

## 5. Target architecture

```
┌──────────────────────── Nexus.app ─────────────────────────┐
│  SwiftUI                                                   │
│  ┌──────────┬───────────────────────────────────────────┐  │
│  │ Bot rail │  one thread per bot · vault · inspectors  │  │
│  │ ◉ main   │  ┌─────────────────────────────────────┐  │  │
│  │ ◉ @bot-a │  │ WKWebView  (graph · dashboards)     │  │  │
│  │ ◉ @bot-b │  └─────────────────────────────────────┘  │  │
│  └──────────┴───────────────────────────────────────────┘  │
│         │ NexusKit (SwiftPM) ── shared with iOS            │
│         │   SSE · NexusClient · Models · ServerStore       │
├─────────┼──────────────────────────────────────────────────┤
│         ▼ loopback HTTP/SSE  127.0.0.1:<port>              │
│  Embedded CPython + FastAPI  (contract unchanged)          │
│  ┌──────────────┬──────────────┬───────────────────────┐   │
│  │ agent loop   │ vault + FTS  │ browser (CDP)         │   │
│  │ + MLX        │ + GraphRAG   │ ← → chrome extension  │   │
│  └──────────────┴──────────────┴───────────────────────┘   │
│  Apple-native: EventKit · AppIntents · Spotlight ·         │
│  Share extension · global hotkey                           │
└────────────────────────────────────────────────────────────┘
```

The backend stays in-process in the bundle and stays loopback-only. **Nothing
about `LoopbackOrTokenMiddleware` changes** — the native app is a loopback
client and bypasses auth exactly as the bundled web UI does today.

## 6. Sequencing

Six milestones, each independently shippable.

| # | Milestone | Contains | Why here |
|---|---|---|---|
| **M1** | Native shell talks to the server | ws4 §1–2 | Nothing else can be built or judged until a window exists |
| **M2** | One thread per bot | ws2 + the ws3 trigger fix | Backend-only, testable behind the existing web UI; de-risks the UX bet before any native chat is written |
| **M3** | Native chat | ws4 §3 | The product. Ships as a usable app. |
| **M4** | MLX | ws1 | Independent of the UI. Needs M2 for the per-bot cache key to be worth anything. |
| **M5** | Vault + browser | ws4 §4–5 | The second-biggest surface; the Obsidian feel |
| **M6** | Curate, integrate, prune | ws3 (full) + ws5 | Pruning last, so nothing is deleted that M3–M5 turn out to need |

**M2 before M3 is the important ordering.** It is a backend change, testable
against 1,983 existing tests and the current web UI, and it is the decision
most likely to need adjustment once lived with.

Keep the web UI buildable through M5 so there is always a working fallback; the
two power-tool surfaces (graph, dashboards) keep it alive afterwards.

## 7. Environment setup

```bash
# Backend. Loom is pulled from GitHub by default; for local work clone it as a
# sibling (../../loom) and swap in the editable copy after sync.
cd agent
uv sync
uv pip install -e ../../loom          # only if you cloned the sibling
uv run pytest --ignore=tests/live     # baseline, see below
uv run ruff check src tests           # line-length 100

# MLX (Apple Silicon only) — see ws1 for why this is an extra, not a dep
uv sync --extra mlx

# Native
cd packaging/macos && swift build -c release --arch arm64

# Web UI (power tools only, after M5)
cd ui && npm install && npm run build  # tsc must stay clean
```

**Baseline before you judge anything:** `uv run pytest --ignore=tests/live`
gives **1,983 passed with 6 pre-existing failures** at `542bfb5` (confirmed in
a clean worktree of HEAD — `test_server_sse` yolo and
`test_anthropic_oauth_headers` among them). The repo is **not** ruff-clean at
baseline either. Establish your own baseline before attributing a failure to
your change.

**Restart the daemon after any `agent/src` change** before treating a live
report as a code bug:

```bash
uv run nexus daemon stop && uv run nexus daemon start
```

A stale daemon has masked real bugs at least twice (`tasks/lessons.md`). Probe
that new code is live with a known-new endpoint rather than assuming.

## 8. Verification

Per-workstream verification is in each workstream file. End-to-end, on a Mac,
in this order — every step is something that has broken before:

1. Restart the daemon (§7).
2. Launch `Nexus.app`; confirm `/health`, one streaming turn, one tool call.
3. Fresh bot → send 3 messages → confirm exactly **one** chat exists for it.
4. Force a checkpoint; confirm the bot's memory artifacts update and the
   context meter drops a zone.
5. `history_recall` a fact from before the checkpoint; confirm the `part:` ref
   resolves.
6. MLX: a tools turn on an `mlx-community` 4-bit model, then a second turn.
   Confirm intra-turn prefill reuse and that a concurrent turn **queues**
   rather than failing.
7. Browser: `read` → `find_element` → `click` on a real page; confirm the
   screenshot arrives as an image part, not a path (`fixes-backlog.md` #6).
8. Vault: create a note, `[[link]]` to another, confirm backlinks; open a
   kanban note and move a card; confirm the markdown round-trips.
9. Reload / relaunch mid-turn; confirm re-attach via
   `/chat/{sid}/turn/active` + `/turn/stream`.

## 9. Risks

- **Scope.** This is a multi-month program. M1–M3 is the minimum that produces
  something better than today; M4–M6 are additive.
- **97.5k LOC of UI is being retired, not ported.** Keep it buildable (§6).
- **MLX tool calling depends on per-architecture parsers.** A model whose chat
  template can't be parsed will emit prose where tool calls belong. Validate
  parser selection at model-activation time and refuse to set an unparseable
  model as `default_model`.
- **`mlx-vlm` tool calling is unverified.** Do not promise vision + tools on
  MLX until someone confirms it. [`ws1`](ws1-mlx-provider.md) defers it.
- **Bundle size.** The signed `.app` is already ~1.7 GB (bundled CPython,
  spaCy models, fastembed/ONNX, faster-whisper/CTranslate2, piper, duckdb,
  PyAV's FFmpeg dylibs). MLX adds more. A separate investigation worth running:
  `mlx-vlm`'s server exposes embeddings, transcription *and* speech on one
  OpenAI-compatible surface, which could retire fastembed, faster-whisper and
  piper on Apple Silicon. Not part of this plan.
- **Nothing in this plan was verifiable on the machine where it was written**
  (Windows, no `uv`, no Swift toolchain, npm registry blocked by a corporate
  proxy). Treat every code-level claim as read-from-source but unrun.
