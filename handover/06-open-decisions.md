# Open decisions

Things that need the product owner's call, not an engineering judgement. Each
has a recommendation and a stated cost of getting it wrong.

Do not resolve these unilaterally — but do not let them block work either; the
"current plan" column is a safe default for each.

---

## 1. Telegram — park or delete? *(blocking M6 only)*

**Status: unresolved. Park by default. Do not delete without explicit
confirmation.**

`telegram/` is 4,279 LOC — a full gateway with bindings, HITL forms over
inline keyboards and ForceReply, voice notes in and out (Piper → OGG/Opus via
PyAV), reply streaming via throttled message edits, and 12 slash commands.

The tension: when the owner scoped which clients survive, they selected the
Chrome extension and the iOS app but **not** Telegram — yet Telegram also never
appeared on any cut list, and it is **the most actively developed subsystem in
the repo** (24 commits in the last 90 days, versus 1 for dreaming and 1 for
workflows). That looks like a slip rather than an intent.

- **Current plan:** park. No native UI work, its binding repoints to the bot's
  `main_session_id` under [`plan/ws2`](plan/ws2-single-thread.md), the code
  stays and keeps running.
- **Cost of deleting wrongly:** the owner loses the only remote access path
  that needs no tunnel, no pairing and no app — on their own evidence of daily
  use.
- **Cost of keeping wrongly:** ~4.3k LOC of maintenance surface and one more
  place every session-model change has to be mirrored.

**Ask before M6.**

## 2. Does the web UI stay buildable after M5?

**Current plan: yes, permanently, for two surfaces.**

The native strategy is "native core + WKWebView for power tools": the
force-directed knowledge graph and the DuckDB dashboards stay as web views
because rebuilding `react-force-graph` and `recharts` natively is weeks of work
for surfaces used rarely.

That means `ui/` cannot be deleted — it must keep compiling (`npm run build`,
tsc) forever, even as ~95% of its components stop being reachable.

- **Alternative:** go 100% native and drop both surfaces from the UI entirely,
  leaving the knowledge graph and dashboards agent-only (queryable, not
  viewable). Smaller maintenance surface, real capability loss.
- **Decide by:** M5. Until then, keep it buildable either way.

## 3. How aggressively should GraphRAG be hidden?

**Current plan: keep retrieval, remove all of its UI.**

Hybrid retrieval (vector + FTS5 fused by reciprocal rank) and the
`vault_semantic_search` tool stay. Removed from the UI: the ontology CSV
editor, the conflict-review queue, entity-resolution staging, and the
per-folder graph wizards.

The risk: the ontology **is** the quality knob for extraction, and it lives in
the vault as editable CSVs (`vault/_system/ontology/*.csv`). If the UI goes
away, the only way to tune it is editing CSVs by hand or asking the agent via
the `ontology_manage` tool. That may be fine — or it may quietly degrade
extraction quality with no visible cause.

- **Decide by:** M5. Cheap hedge: keep one read-only "knowledge health" panel
  showing entity/relation counts and the `extraction_warning` flag, so silent
  degradation is at least visible.

## 4. Workflows: read-only forever, or a native editor later?

**Current plan: engine runs, no editor. Bots author workflows as markdown.**

The React-flow canvas is `WorkflowFlow/` plus a 3,049-LOC stylesheet — the
single largest UI artifact in the repo, for a subsystem with 1 commit in 90
days. The plan removes the editor and keeps the engine, the triggers and the
run history; workflows become vault markdown that a bot writes and a human
reads.

- **If that proves too thin**, the cheap middle is a native read-only view: the
  step list, the trigger, the last few runs, and a Run button. No graph canvas.
- **Decide by:** after M3, once there is a native surface to hang it on.

## 5. Does the iOS app get the shared Swift core, or get frozen?

**Current plan: share the core.**

[`plan/ws4`](plan/ws4-native-ui.md) §1 extracts `ios/Nexus/Core/` (1,508 LOC,
verified UIKit-free) into a SwiftPM package used by both clients. That makes
iOS nearly free to keep in sync and fixes three of its current defects as a
side effect (see [`plan/fixes-backlog.md`](plan/fixes-backlog.md) #4, #8, #9).

The alternative is freezing iOS at its current state and letting it drift. That
saves nothing in the short term (the extraction is mechanical) and costs a
working client later.

**Low risk either way; proceeding with sharing unless told otherwise.**

## 6. Who owns the Apple Developer account?

**Needed before any signed build or notarization.**

`packaging/build.sh` can sign, notarize and produce a `.pkg`, and the release
workflow does it in CI — but both need an active Apple Developer Program
membership and two certificates (Developer ID Application for the `.app`,
Developer ID Installer for the `.pkg`), plus an app-specific password for
`notarytool`. Setup is documented step-by-step in
[`/packaging/macos/README.md`](/packaging/macos/README.md).

Without it, builds fall back to **ad-hoc signing** — fine for local
development, not distributable (Gatekeeper will refuse it on another machine).

**Ask early**, because certificate issuance is not instant.

## 7. Bundle size: is ~1.7 GB acceptable?

**Current plan: accept it for now; a slimming path exists but is out of scope.**

The signed `.app` is already ~1.7 GB — bundled CPython, two spaCy models,
fastembed/ONNX Runtime, faster-whisper/CTranslate2, Piper, DuckDB, and PyAV's
FFmpeg dylibs. MLX adds more on top.

There is a real slimming opportunity worth a separate investigation: `mlx-vlm`'s
server exposes embeddings, transcription **and** speech synthesis on one
OpenAI-compatible surface, which on Apple Silicon could retire fastembed,
faster-whisper and Piper together. That is a meaningful win and a meaningful
risk (it makes three working subsystems depend on one unverified package), so
it is deliberately **not** part of this plan.

**Decide if/when bundle size becomes a complaint.**

---

## Already decided — do not reopen without cause

| Decision | Choice |
|---|---|
| Native strategy | native core + WKWebView for graph and dashboards |
| Platform scope | macOS is the product; keep the Chrome extension and the iOS app |
| Backend | stays Python (1,983 tests, and the API is already a native-client contract) |
| MLX runtime | **in-process**, not a supervised `mlx_lm.server` subprocess |
| Chat model | **one visible thread per bot** |
| Dream / Workflows | engines keep running; no dedicated UI |
| Cut immediately | the cookie-export extension (`extension/`) and the ACP bridge |
| Vault | stays the source of truth; calendar/kanban/database are frontmatter inspectors, never separate stores |
