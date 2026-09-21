# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Repository: `git@github.com:NinoCoelho/nexus.git` (branch: `main`).

## Repo layout

Nexus depends on **Loom** (the agentic-core framework). By default `uv sync`
pulls it straight from GitHub (`NinoCoelho/loom`, `main` branch) via the
`[tool.uv.sources]` override in `agent/pyproject.toml` — the one-line
installer works on a fresh host with no second clone.

For local development against a sibling checkout, clone loom next to nexus:

```
<parent>/
  loom/    # git@github.com:NinoCoelho/loom.git
  nexus/   # this repo
```

…then after `uv sync`, run `uv pip install -e ../../loom` from
`nexus/agent/` to swap in the editable local copy.

## Commands

Backend (from `agent/`, managed by `uv`):

```bash
uv sync                                  # install deps (needs ../../loom sibling)
uv run nexus serve --port 18989          # run FastAPI server in foreground (always 127.0.0.1)
uv run nexus daemon start | status | stop | logs  # background daemon (PID + log in ~/.nexus/)
uv run nexus tunnel start | stop | status # public tunnel (Cloudflare, `--provider tailscale` Funnel, or `--provider tailscale-serve` tailnet-only/no-code) with login-form auth
uv run nexus chat                        # interactive TUI chat
uv run nexus config init | show          # ~/.nexus/config.toml bootstrap
uv run pytest                            # full test suite (asyncio_mode=auto)
uv run pytest tests/test_router.py::test_name  # single test
uv run ruff check src tests              # lint (line-length 100)
```

Frontend (from `ui/`):

```bash
npm install
npm run dev       # Vite dev server on http://localhost:1890; expects API at http://localhost:18989
npm run build     # tsc + vite build; type errors fail the build
```

UI reads the API base from `VITE_NEXUS_API` (defaults to `http://localhost:18989`). There is no lint/test script for the UI; rely on `npm run build` (tsc) to catch type errors.

## Architecture

Nexus is a self-evolving single-agent platform with a Python FastAPI backend and a React 19 + Vite frontend. State lives under `~/.nexus/` (config, skills, vault, sessions DB, daemon pid/log).

### Agent loop

`agent/src/nexus/agent/loop/` (package) drives a tool-calling loop over a pluggable LLM provider (`llm.py`: OpenAI-compat + native Anthropic). The system prompt is built by `prompt_builder.py` using **progressive disclosure**: only skill *names + descriptions* go in; the agent fetches full skill bodies via the `skill_manage` / `skill_view` tools when it decides to use them. Context compaction strategies (auto / summarize / aggressive) live in `loop/compact.py` and `loop/summarize.py`.

Tools wired into the loop live in `agent/src/nexus/tools/` and `agent/src/nexus/agent/*_tool.py`:
- `skill_manage` (self-authoring — create/edit/patch/delete/view skills at runtime)
- `vault_tool` (read/list/write markdown under `~/.nexus/vault/`)
- `kanban_tool` (operates on vault .md files with `kanban-plugin: basic` frontmatter — boards are just markdown, not a separate store)
- `memory_tool`, `state_tool`, `http_call`, `acp_call` (loom ACP bridge), `ask_user` (HITL), `terminal` (HITL)

### Dreaming

`agent/src/nexus/dream/` is a scheduled background agent that runs during idle periods. Four-phase cycle: consolidation → insight extraction → skill refinement → scenario rehearsal. State tracked in `dream_state.sqlite`. The engine is triggered by the `dream_trigger` heartbeat driver; the UI exposes a Dream view (`ui/src/components/DreamView/`). Skill suggestions are staged for user review, not auto-created.

### Voice acknowledgments

`agent/src/nexus/voice_ack.py` generates spoken confirmations for voice-input turns (start ack + completion ack), synthesized via Piper TTS. Gated by `[tts].ack_enabled`; uses `[tts].ack_model` for low-latency LLM calls. Language auto-detected via `langdetect`.

### Calendar alarms

`agent/src/nexus/alarm_store.py` (SQLite) persists per-occurrence alarm state (ringing / acknowledged / snoozed). The UI renders a stack of alarm cards (`AlarmNotification.tsx`) with countdown timers, snooze/dismiss actions. Alarm SSE events flow through the global notification channel.

### Self-evolution + safety

Agent-authored skills go through `skills/guard.py` — a regex static scan for credential exfil / destructive shell patterns. Failed guards roll the write back. Skills have a trust tier (`builtin`/`user`/`agent`); bundled skills from top-level `skills/` are seeded to `~/.nexus/skills/` on first run and marked `builtin`.

### HITL (human-in-the-loop)

Two channels on each session:
1. `POST /chat/stream` — per-turn SSE (deltas, tool calls, done, error).
2. `GET /chat/{sid}/events` — session-scoped SSE for out-of-band events (`user_request`, `user_request_auto`, `user_request_cancelled`). The UI opens this *before* the first POST by using a client-generated `pendingSessionId`, so approval dialogs don't miss events during the first turn. YOLO mode (`/settings`) auto-answers requests.

### Vault import wizard

`agent/src/nexus/server/routes/vault_import.py` + `agent/src/nexus/vault_import_parsers.py` implement a universal import flow: drag-and-drop zip files, folders, or individual files onto the vault tree (or use the upload button). Multi-step modal (`ui/src/components/ImportModal/`) walks through file selection → export format detection → optional LLM processing → confirm → SSE-streamed progress. ChatGPT / Claude / Gemini conversation exports are auto-detected by JSON structure inspection (not filenames) and converted to per-conversation markdown files. CSV files can be promoted to DuckDB-backed data-table apps via an LLM analysis step. Large files (>1 MiB) use `write_file_bytes` (32 MiB limit) instead of `write_file` (1 MiB limit). Temp extraction lives in `~/.nexus/tmp/zip-import/` (1-hour TTL auto-cleanup).

### Vault

`~/.nexus/vault/` is a folder of markdown files with FTS5 search (`vault_index.py`, `vault_search.py`), tag index, and a backlinks graph (`vault_graph/` package). External writes (Syncthing/rsync/Obsidian) are picked up by `vault_watch.py` — a debounced watchdog observer that re-runs the mtime-incremental reindex, invalidates graph caches, and schedules GraphRAG after changes settle (config `[vault] watch`, default on; env `NEXUS_DISABLE_VAULT_WATCH=1` to kill, set in test conftest). Kanban boards are vault-native: any `.md` file whose frontmatter contains `kanban-plugin:` is interpreted as a board by both the `vault_kanban` module (Python) and `KanbanBoard.tsx` (UI). Do not add a separate kanban store — edit the vault markdown directly. `POST /vault/dispatch` creates a new chat session seeded from a vault file or kanban card and links the session id back into the card.

### Knowledge graph

Two graph systems over the vault, plus per-folder isolated graphs:

- **Link graph** (`vault_graph/` package): nodes = vault files, edges from markdown links. Link *parsing* has a single source of truth — `markdown_links.py` (wiki-links `[[note|alias]]`, `[[folder/note]]`, anchored `](note.md#sec)`, bare path mentions, code fences stripped, Obsidian-style stem resolution). Both `vault_graph/builder.py` and `vault_index.py` consume it — do not re-add local regexes. `/vault/graph` supports `scope=all|file|folder|tag|search|entity`, degree-ranked `max_nodes` cap (default 300), and a `graphrag` health summary.
- **GraphRAG** (`agent/graphrag_manager/` over loom's `GraphRAGEngine`): chunk → embed → entity/relation extraction → SQLite triple store with conflict review + staged entity resolution (loom ≥ commit `7ffc4bb`). Ontology source of truth is the vault (`vault/_system/ontology/*.csv`); `_system/` paths are excluded from indexing. When an extraction model is configured but unresolvable, the manager falls back to the builtin spaCy+fastembed extractor **loudly** (`extraction_warning` in `/graph/knowledge/health`).
- **Retrieval is hybrid**: `/graph/knowledge/query` merges vector hits with FTS5 (`vault_search`) via reciprocal-rank fusion — keyword-only matches surface as `source: "fts"` results.
- **UI** (`ui/src/components/UnifiedGraph/`): one canvas, three modes (knowledge/vault/agent + folder sub-tabs). Renderer is **2D by default** (`GraphCanvas2D`, canvas 2D, draggable nodes, no WebGL); 3D is a settings toggle (`GraphSettings.renderer`, persisted in localStorage). Mode hooks fetch lazily on first tab visit.

### Server layout

`agent/src/nexus/server/app.py` registers all FastAPI routes. Sessions are persisted via `session_store.py` (SQLite under `~/.nexus/`). Model routing uses the configured `agent.default_model` (the old `fixed`/`auto` router modes were removed).

### Frontend

`ui/src/App.tsx` owns all chat state keyed by session id (plus a `__new__` slot for the not-yet-created session). View switches and session switches never drop in-flight `thinking` or `input` state. Views: `chat`, `vault`, `graph`, `agentgraph`, `heartbeat`, `dream`, `workflows`. Kanban is **not** a top-level view — it's rendered by `VaultEditorPanel.tsx` when the selected file's frontmatter declares `kanban-plugin`. All markdown rendering goes through `components/MarkdownView.tsx` (react-markdown + remark-gfm + lazy mermaid).

### Page interaction (`page` tool)

`agent/src/nexus/agent/page_tool.py` gives the agent read/act access to the user's **real browser tab** in side-panel sessions — the counterpart of the `chrome-devtools` skill's managed Chrome (CDP port 9223). It rides the `ask_user` HITL rails: `PageHandler.invoke` registers a pending future on the session broker, publishes a `page_request` SessionEvent (`{request_id, action, params}`), and blocks; the Chrome panel receives it over `GET /chat/{sid}/events`, executes it via `chrome.scripting.executeScript`, and resolves it through the generic `POST /chat/{sid}/respond` (answer is a JSON string — `RespondPayload.answer` is `str`). Actions: `read` (cleaned main-content text — a readability-style walker (article/main/[role=main] root, nav/header/footer/aside/hidden skipped, block-aware breaks) replaces raw innerText; `mode: outline|links` for heading-structure / anchor-only reads; `selector` for a raw element read; `full=true` for 40k; results cached 15s per tab+URL and invalidated on navigation), `transcript` (native captions of the current video page — `movie_player.getPlayerResponse()` caption tracks fetched as json3 in MAIN world; **prefer over yt-dlp/terminal for YouTube**), `click` (selector or visible text), `type` (native value-setter + input/change events, optional Enter), `scroll`, `navigate`, `js` (eval in MAIN world). The `tab` param (url/title substring) retargets any action at a **grouped** tab — multi-tab perception like Claude's page group. `navigate`/`js` show an Allow/Deny card in the panel; everything else auto-executes and logs an activity line. The conversation preamble is **pointer-only** (url/title/800-char excerpt + changed-flag): the model is taught to call `read`/`transcript` on demand instead of relying on stuffed page content — agent-driven perception, with page content framed as untrusted data. No panel listening → timeout (clamped 5–180s) with an explanatory error. Handler is wired in `app.py` (`agent._page_handler`), registered in `build_tool_registry` next to ask_user, late-bound via `AgentHandlers.page`. Note: synthetic DOM events (`isTrusted: false`) — some sites ignore them; use `js` for those.

### Chrome side panel (`chrome/`)

**Distribution (local-only by design):** the extension is never on the Chrome Web Store. `nexus chrome install` (agent/src/nexus/chrome_install.py, CLI group `chrome`) copies the sources to `~/.nexus/chrome-extension/` and opens the server's guided page at `GET /ext` (routes/ext.py — load-unpacked steps with copy buttons + live "Extension connected ✓" status). `nexus chrome doctor` verifies server/files/heartbeat. Installers (macOS pkg postinstall / Windows NSIS run-after) just invoke the CLI. `GET /ext/ping` is the heartbeat: the extension SW pings it on boot/startup with an `X-Nexus-Extension` version header (reads never overwrite last-seen). Port handling: `host_permissions` are `http://localhost/*` + `http://127.0.0.1/*` (match patterns ignore ports — any port covered); a content script (`port-capture.js`) on those origins stores `location.port` into `chrome.storage.local.nexusPort` whenever the user opens a Nexus-served page (detected via the `<meta name="nexus-server">` marker, present on `/ext` and in `ui/index.html`); the panel resolves the base from that storage (default 18989), reacts to `chrome.storage.onChanged`, and offers an inline port field when the server is unreachable. One stored port — last-visited server wins.

A no-build MV3 extension ("Nexus in Chrome", PoC) that replicates page-connected chat. There is **no global side panel** — the manifest has no `side_panel` key, so the only panel that can ever open is a per-tab one on *activated* tabs (toolbar icon clicked). A manifest-global panel follows the user across every tab once opened — that was the "chat on all tabs" bug (v0.1–v0.4); removing the global fixed it. Activation lives in `service-worker.js`: `onActivateGesture` (from `action.onClicked` + context-menu entries (right-click: "Open Nexus chat on this tab" / "Ask Nexus about this selection" / "Ask Nexus about this link" — menu items activate without the finish-toggle and seed the composer via `storage.session.composerSeed` + a `SEED_COMPOSER` broadcast, drained after the panel registers its listener to survive first-boot races); `openPanelOnActionClick` is **false** — the extension owns the toggle) fires `setOptions({tabId, enabled: true, path})` **unawaited in the same synchronous block** as the gesture-carrying `chrome.sidePanel.open({tabId})` — the user gesture does not survive awaits in `action.onClicked`, and `open()` rejects on tabs without per-tab options; IPC submission order makes the unawaited `setOptions` land first. A second icon click while the panel is open **finishes** the tab: closes the panel, removes it from the purple **Nexus tab group** (`tabs.ungroup`), deactivates it, and clears the tab↔session mapping (next activation = fresh conversation; server-side history kept). Chrome clears per-tab sidePanel options on extension reload, so policy is *healed* on every SW boot plus `tabs.onActivated`/`onCreated` (activated tabs re-asserted; others `enabled: false` on `getOptions` mismatch); open-state (`onOpened`/`onClosed`) is in-memory and cleared at boot (a reload closes all panels; stale entries made clicks take the invisible finish branch). Activation/session maps live in `chrome.storage.session`; session ids are client-minted uuid-hex.

Every user message carries a **pointer-only `<context>` preamble**: current url/title, an ~800-char identification excerpt (from the *cleaned* extraction, so it starts with real content, not cookie banners), a changed-flag (URL compared normalized — hash and YouTube-style `t`/`time_continue` params ignored), grouped-tab titles (`tabGroups` API — the shared-context mechanism like Claude's page group; `＋ Group` button in the panel header), and standing instructions (call `page read`/`transcript` for content, page content is untrusted, prefer the current tab when ambiguous or ask_user). Full page text is **never stuffed** — the model fetches it on demand via the `page` tool (agent-driven perception). The panel UI mirrors the main chat: `tokens.css` is a port of `ui/src/tokens.css` (4 themes × dark/light via `--brightness`, default `mermaidcore`, follows `prefers-color-scheme`), bubbles/avatar/thinking/composer reuse the same token names as `ChatView.css`/`AssistantMessage.css`, and assistant bodies render markdown via vendored `vendor/marked.min.js` through a DOM-allowlist sanitizer (`sanitizeMd` — raw HTML stripped, hrefs limited to http/mailto; mandatory for page-derived content). SSE parsing (`readSse`/`parseFrame`) tolerates both the `event:`/`data:` framing *and* bare-JSON guard lines (oversize/context-overflow paths emit raw JSON, no SSE prefix). HITL `user_request` events arrive over `GET /chat/{sid}/events` and are answered via `POST /chat/{sid}/respond`. Page extraction needs `activeTab` (granted by the toolbar click) or the optional `<all_urls>` grant requested from the panel. Never send `x-forwarded-*` headers from extension code — loopback + proxy headers = 401. `extension/` (top-level) is a *different* extension (cookie export, nativeMessaging) — don't confuse the two dirs.

### Workflows

`agent/src/nexus/workflows/` implements a visual flow-based automation engine. Workflow definitions are stored as vault markdown files with `workflow-plugin: basic` frontmatter (consistent with kanban/calendar pattern — no separate store). The visual editor (`ui/src/components/WorkflowFlow/`) uses `@xyflow/react` (v12) for the node graph canvas with controlled nodes (`applyNodeChanges` for drag).

**Models** (`models.py`): `StepType` enum (`tool_call`, `agent_session`, `mcp_call`, `http_request`, `condition`, `transform`, `delay`), `TriggerType` enum (`webhook`, `fs_watch`, `schedule`, `manual`, `event`). Each step has a `slug` (auto-generated camelCase from name) used for template references like `{{steps.mySlug.result}}`.

**Engine** (`engine.py`): Executes steps sequentially with template resolution, retry logic, and error handling. Step dispatch:
- `tool_call` — calls a registered Loom tool by name
- `agent_session` — creates a real chat session with an LLM
- `mcp_call` — calls a tool on a connected MCP server via `McpManager`
- `http_request` — HTTP call with auth support (API key bearer/header/query, basic auth, OAuth 2.0, custom headers). Credentials resolved at runtime via `secrets.resolve()`
- `transform` — three modes: `template` (standard `{{...}}` substitution), `llm` (LLM transform without tools/reasoning), `script` (Python `exec()` with `data` variable)
- `condition` — evaluates expression, branches to `then_step` or `else_step`
- `delay` — async sleep

**Parser** (`parser.py`): Reads/writes vault markdown with YAML frontmatter. The `StepConfig.to_dict()` method only serializes non-default fields to keep the markdown clean.

**Triggers**: Five trigger types, each with its own driver. Webhook triggers generate tokens and receive at `POST /workflow/trigger/{token}`. Schedule triggers use a cron-based heartbeat driver. FS watch uses `watchdog>=4.0`. Event triggers subscribe to the internal event bus. Manual triggers are user-initiated.

**API** (`server/routes/workflows.py`): CRUD endpoints for workflow definitions, webhook receiver, run history, and manual trigger endpoint.

**UI**: React-flow canvas with custom node types (`TriggerNode`, `StepNode`, `ConditionNode` as diamond with true/false ports, `AddNode`). Left sidebar palette with draggable step/trigger icons. ConfigPanel slides in on node selection. `TemplateInput` component provides `{{steps.slug...` autocomplete with Tab to accept. Node positions are controlled state persisted via `applyNodeChanges`. Rearrange button (⇅) resets to auto-computed layout. Debounced saves (500ms) prevent "Saving…" from blocking typing.

**Run history**: Separate SQLite `~/.nexus/workflow_runs.sqlite` tracks run state and per-step outputs. SSE events (`workflow.run_completed`) published via the global event bus.

### Config precedence

`~/.nexus/config.toml` is canonical. Legacy env-var path (`NEXUS_LLM_BASE_URL` + `NEXUS_LLM_API_KEY` + `NEXUS_LLM_MODEL`) overrides the config file when **all three** are set. Provider API keys are referenced by env var name (`key_env`), never stored inline.

### Feature flags

The nexus-llm subscription gating system (`features.py`, `FeatureGateMiddleware`, `FEATURE_TOOLS`/`FEATURE_ROUTES`) was **removed** (commit `b4f6779`) — all tools and routes register unconditionally. What remains:

- **UI mode** (`settings.json`, per-user `ui_mode: normal | advanced`): normal hides knowledge, heartbeat, dream views. The frontend hook is `useFeatures` (`ui/src/hooks/useFeatures.ts`) — despite the name it now only handles `ui_mode`.
- Server auth is loopback-or-token (see "Network model + sharing security" below); there is no per-feature route gating.

### Network model + sharing security

The server **always** binds to `127.0.0.1`. There is no `--host` flag and no supported way to expose it on `0.0.0.0`. Remote access is only via a tunnel that runs as a local client connecting *to* the loopback bind:

- `nexus tunnel start` (managed Cloudflare Quick Tunnel — auto-downloads `cloudflared` on first use, no signup required), or `nexus tunnel start --provider tailscale` (Tailscale Funnel — public internet, needs the `tailscale` CLI logged in; stop resets the funnel config), or `nexus tunnel start --provider tailscale-serve` (tailnet-only: no access code — tailscaled authenticates peers at the network layer, the middleware trusts proxied clients via `TunnelManager.trusts_proxied_clients()`; activation fails closed if a funnel entry targets the Nexus port, and hand-enabling funnel afterwards would make the no-auth path public — don't), or
- bring-your-own — ssh `-L` etc. targeting `localhost:18989`. Note: manually-run proxies that set `x-forwarded-*` headers (e.g. a hand-typed `tailscale funnel`) are treated as tunnel traffic and fail closed with 401 — use the managed providers so the code/cookie flow is minted.

The auth gate is `LoopbackOrTokenMiddleware` in [agent/src/nexus/server/app.py](agent/src/nexus/server/app.py). It splits requests on whether proxy headers (`x-forwarded-for` / `x-forwarded-host` / `cf-ray` / `cf-connecting-ip`) are present:

- **No proxy headers + loopback IP** → bundled UI talking to its own server, bypass auth.
- **Proxy headers present** → request came through a tunnel; require the cookie unless the path is in the explicit tunnel-public allowlist (`/tunnel/redeem`, `/tunnel/auth-status`, SPA shell + assets).

Sharing flow ([agent/src/nexus/tunnel/manager.py](agent/src/nexus/tunnel/manager.py)) generates **two secrets per activation**:
1. **Long token** (32-byte urlsafe) — set as `HttpOnly Secure SameSite=Strict` cookie after redemption. Never appears in URLs.
2. **Short access code** (8 base32 chars, formatted `XXXX-XXXX`, formatted-display only — confusable chars `0/O/1/I/L` excluded) — typed on the phone's `TunnelLoginScreen`. Travels only in the POST body of `/tunnel/redeem`. Per-IP rate-limited (8 wrong attempts / 10 min) on top of the already-large entropy.

`/tunnel/start|stop|status|install` are loopback-only at the route level (`_require_loopback`), so even a tunnel-authenticated client can't take over the tunnel admin surface. FastAPI's auto-`/docs`, `/redoc`, `/openapi.json` are disabled (single-user app, not a public API). Baseline browser-hardening response headers are set by `SecurityHeadersMiddleware`.

## Conventions

- Use `uv run` for all backend commands; don't invoke `python` directly.
- Agent-facing identifiers inside vault markdown (card IDs, session links) use HTML comments prefixed `<!-- nx:... -->` (e.g. `nx:id=<uuid>`, `nx:session=<sid>`).
- When referencing a vault file in agent output, format as a markdown link with a `vault://path` href — the UI intercepts this to preview inline.
- Tests use `asyncio_mode = "auto"` — async test functions don't need `@pytest.mark.asyncio`.
- **Never use native browser dialogs** (`window.alert`, `window.confirm`, `window.prompt`). They're blocking, unstyled, and inconsistent across platforms. Use the system standards instead: `components/Modal.tsx` (`kind: "confirm" | "prompt"`) for confirmations and text input, and `useToast()` from `toast/ToastProvider` for non-blocking notifications (with a `detail` field and optional `action` button for follow-ups like copy-to-clipboard).
- When adding new feature-gated tools or routes, register them in `features.py` (`FEATURE_TOOLS` / `FEATURE_ROUTES`) and gate the registration with `_should_register()`.
