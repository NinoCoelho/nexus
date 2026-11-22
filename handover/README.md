# Nexus — developer handover

**Read this file first. It should take about fifteen minutes.**

You are taking over work on **Nexus**, a self-hosted personal AI agent
platform, to convert it into a native macOS application. This folder is the
complete handover: orientation, setup, the implementation plan, and the
institutional knowledge that is not visible in the code.

Everything here is verified against the source at commit `542bfb5` unless
stated otherwise. File references are `path:line`.

---

## What Nexus is, in one paragraph

A Python/FastAPI backend (~94k LOC) running an agentic tool-calling loop over a
pluggable LLM provider, plus a React web UI (~97k LOC). It stores everything
the user owns as **markdown files in a vault** (`~/.nexus/vault/`) with
full-text search, a backlinks graph and a knowledge graph over the top. The
agent writes its own skills, drives a real Chrome browser, runs scheduled
background work, and is reachable from a web UI, a Chrome side panel, an iOS
app and Telegram. It runs entirely on the user's machine and binds to loopback
by default.

## Where to start

| Order | File | Why |
|---|---|---|
| 1 | **this file** | orientation + the 30-minute tour below |
| 2 | [`01-environment.md`](01-environment.md) | get it running; includes the baseline test failures so you don't chase ghosts |
| 3 | [`03-state-of-the-work.md`](03-state-of-the-work.md) | what has been done, what is verified, what is not |
| 4 | [`05-known-traps.md`](05-known-traps.md) | **highest value per minute in this folder.** Read before writing code. |
| 5 | [`plan/README.md`](plan/README.md) | the port plan: decisions, architecture, 6 milestones |
| 6 | [`02-architecture-map.md`](02-architecture-map.md) | reference — come back when you need to find something |

Also here: [`04-runbook.md`](04-runbook.md) (operate and debug it),
[`06-open-decisions.md`](06-open-decisions.md) (what still needs the owner's
call), [`07-glossary.md`](07-glossary.md) (the project's jargon — "bot" vs
"project", loom, microcompaction, NBIE, HITL…).

## The job, in short

Five problems to solve. Full detail in [`plan/README.md`](plan/README.md).

1. **No native app.** A macOS `.app` exists but is only a menu-bar supervisor:
   it spawns a bundled CPython and opens `http://127.0.0.1:<port>/` in the
   user's default browser. There is no native window.
2. **No MLX.** Local inference goes through a `llama-server` subprocess,
   leaving the best Apple Silicon runtime unused. **A first implementation of
   the in-process MLX provider is already on this branch** — see
   [`03-state-of-the-work.md`](03-state-of-the-work.md).
3. **Too many chats.** Many visible chat sessions per bot confuses users. The
   product should be **one chat window per bot** that manages its own context
   without losing track.
4. **Vault is shallow.** It should feel like Obsidian; calendar, kanban and
   database should be inspectors on vault documents, not separate nav items.
5. **Dreaming is inert.** The nightly background agent runs, spends tokens, and
   produces nothing actionable. It is being reworked into the thing that keeps
   a single endless thread healthy.

Guiding constraint from the owner: **simpler on the surface, more capable
underneath.** Confusing power features get hidden or deleted, not left in the
nav.

## The 30-minute tour

Do these in order. It is the fastest way to build an accurate mental model.

1. **Read [`/CLAUDE.md`](/CLAUDE.md).** It is long, and it is the single most
   valuable document in the repo — a record of hard-won invariants written by
   the people who hit the bugs. You do not need to retain it; you need to know
   it exists and is searchable.
2. **Trace one chat turn.** `server/routes/chat_stream.py` (`POST /chat/stream`)
   → `server/services/chat_turn_runner.py` → `agent/loop/agent.py::run_turn_stream`
   → `agent/_loom_bridge/adapter.py` → `agent/llm/openai.py::chat_stream`. That
   path is the spine of the product.
3. **Read `agent/prompt_builder.py::build_system_prompt`.** Everything the
   model knows about itself is assembled there, and the ordering matters
   (see [`05-known-traps.md`](05-known-traps.md) #1).
4. **Open `~/.nexus/` on a running install.** The vault is just markdown. Open
   a kanban board file in a text editor and see that the board *is* the file.
5. **Skim `/tasks/lessons.md`.** A post-mortem log. Distilled into
   [`05-known-traps.md`](05-known-traps.md), but the originals have detail.

## Non-negotiables

Things that will be noticed if you break them:

- **The backend stays Python.** 191 test files / ~1,983 tests are the reason.
  The HTTP/SSE API is already a native-client contract — the iOS app consumes
  it today, including mid-turn re-attach.
- **The server binds to loopback.** There is no `--host` flag and no supported
  way to bind `0.0.0.0`. Exposure happens through a tunnel or a Tailscale bind.
  Don't "simplify" `LoopbackOrTokenMiddleware`.
- **SQLite must never live in a synced folder.** `~/.nexus/.stignore` excludes
  `*.db*` because WAL corruption across two Syncthing machines was the #1
  bootstrap killer. Markdown syncs; databases do not.
- **The vault is the source of truth.** Kanban boards, calendars, data tables
  and workflows are markdown files with a `*-plugin:` frontmatter key. Do not
  add a parallel store for any of them.
- **Secrets never enter transcripts.** Password fields are redacted at three
  choke points before persistence. Site credentials are resolved server-side
  and never passed as tool arguments.
- **One perception contract for the browser.** `browser/probes.py` is kept
  byte-identical to `chrome/probes.js` by a unit test, so the managed Chrome
  and the user's real tab behave the same. Don't fork it.

## Conventions

- `uv run` for all backend commands; never invoke `python` directly.
- Line length 100 (`ruff`). The repo is **not** ruff-clean at baseline — fix
  what you touch, don't crusade.
- `asyncio_mode = "auto"` — async tests need no `@pytest.mark.asyncio`.
- Agent-facing identifiers inside vault markdown are HTML comments prefixed
  `nx:` — e.g. `<!-- nx:id=<uuid> -->`, `<!-- nx:session=<sid> -->`.
- Vault files referenced in agent output use a `vault://path` href; the UI
  intercepts those to preview inline.
- **No native browser dialogs** in the web UI (`alert`/`confirm`/`prompt`).
  Use `components/Modal.tsx` and `useToast()`.

## Getting unstuck

- **"Is this behaviour intentional?"** → search `/CLAUDE.md` first, then
  `/tasks/lessons.md`. A surprising amount of odd-looking code is a scar.
- **"Where does X live?"** → [`02-architecture-map.md`](02-architecture-map.md).
- **"Why is my change not taking effect?"** → you almost certainly need
  `uv run nexus daemon stop && uv run nexus daemon start`. This has wasted
  real debugging time twice. See [`04-runbook.md`](04-runbook.md).
- **"This test was already failing"** → six are, at baseline. Listed in
  [`01-environment.md`](01-environment.md).
