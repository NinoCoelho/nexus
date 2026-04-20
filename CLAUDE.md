# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Repository: `git@github.com:NinoCoelho/nexus.git` (branch: `main`).

## Commands

Backend (from `agent/`, managed by `uv`):

```bash
uv sync                                  # install deps
uv run nexus serve --port 18989          # run FastAPI server in foreground
uv run nexus daemon start | status | stop | logs  # background daemon (PID + log in ~/.nexus/)
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

`agent/src/nexus/agent/loop.py` drives a tool-calling loop over a pluggable LLM provider (`llm.py`: OpenAI-compat + native Anthropic). The system prompt is built by `prompt_builder.py` using **progressive disclosure**: only skill *names + descriptions* go in; the agent fetches full skill bodies via the `skill_manage` / `skill_view` tools when it decides to use them.

Tools wired into the loop live in `agent/src/nexus/tools/` and `agent/src/nexus/agent/*_tool.py`:
- `skill_manage` (self-authoring — create/edit/patch/delete/view skills at runtime)
- `vault_tool` (read/list/write markdown under `~/.nexus/vault/`)
- `kanban_tool` (operates on vault .md files with `kanban-plugin: basic` frontmatter — boards are just markdown, not a separate store)
- `memory_tool`, `state_tool`, `http_call`, `acp_call` (stub), `ask_user` (HITL), `terminal` (HITL)

### Self-evolution + safety

Agent-authored skills go through `skills/guard.py` — a regex static scan for credential exfil / destructive shell patterns. Failed guards roll the write back. Skills have a trust tier (`builtin`/`user`/`agent`); bundled skills from top-level `skills/` are seeded to `~/.nexus/skills/` on first run and marked `builtin`.

### HITL (human-in-the-loop)

Two channels on each session:
1. `POST /chat/stream` — per-turn SSE (deltas, tool calls, done, error).
2. `GET /chat/{sid}/events` — session-scoped SSE for out-of-band events (`user_request`, `user_request_auto`, `user_request_cancelled`). The UI opens this *before* the first POST by using a client-generated `pendingSessionId`, so approval dialogs don't miss events during the first turn. YOLO mode (`/settings`) auto-answers requests.

### Vault

`~/.nexus/vault/` is a folder of markdown files with FTS5 search (`vault_index.py`, `vault_search.py`), tag index, and a backlinks graph (`vault_graph.py`). Kanban boards are vault-native: any `.md` file whose frontmatter contains `kanban-plugin:` is interpreted as a board by both the `vault_kanban` module (Python) and `KanbanBoard.tsx` (UI). Do not add a separate kanban store — edit the vault markdown directly. `POST /vault/dispatch` creates a new chat session seeded from a vault file or kanban card and links the session id back into the card.

### Server layout

`agent/src/nexus/server/app.py` registers all FastAPI routes. Sessions are persisted via `session_store.py` (SQLite under `~/.nexus/`). Routing logic (`agent/router.py`) picks a model — `fixed` (default) uses `agent.default_model`, `auto` scores models by per-model `strengths` against a simple keyword classifier.

### Frontend

`ui/src/App.tsx` owns all chat state keyed by session id (plus a `__new__` slot for the not-yet-created session). View switches and session switches never drop in-flight `thinking` or `input` state. Views: `chat`, `vault`, `graph`, `insights`, `agentgraph`. Kanban is **not** a top-level view — it's rendered by `VaultEditorPanel.tsx` when the selected file's frontmatter declares `kanban-plugin`. All markdown rendering goes through `components/MarkdownView.tsx` (react-markdown + remark-gfm + lazy mermaid).

### Config precedence

`~/.nexus/config.toml` is canonical. Legacy env-var path (`NEXUS_LLM_BASE_URL` + `NEXUS_LLM_API_KEY` + `NEXUS_LLM_MODEL`) overrides the config file when **all three** are set. Provider API keys are referenced by env var name (`key_env`), never stored inline.

## Conventions

- Use `uv run` for all backend commands; don't invoke `python` directly.
- Agent-facing identifiers inside vault markdown (card IDs, session links) use HTML comments prefixed `<!-- nx:... -->` (e.g. `nx:id=<uuid>`, `nx:session=<sid>`).
- When referencing a vault file in agent output, format as a markdown link with a `vault://path` href — the UI intercepts this to preview inline.
- Tests use `asyncio_mode = "auto"` — async test functions don't need `@pytest.mark.asyncio`.
