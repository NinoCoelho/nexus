# Nexus

A self-evolving agentic platform. Proprietary agent core with runtime skill authoring (Hermes-style markdown skills), CLI + web UI, pluggable providers, optional auto model routing, and ACP as a secondary lane for external agents.

## What it is

- **Self-evolving** — the agent can create, edit, and patch its own skills at runtime via a `skill_manage` tool. Skills are markdown with YAML frontmatter under `~/.nexus/skills/<name>/`. A regex guard scans agent-authored skills for credential exfil and destructive patterns; dangerous writes roll back.
- **Progressive disclosure** — the system prompt carries only skill names + descriptions. The agent pulls full skill bodies on demand.
- **Multi-provider** — any OpenAI-compatible endpoint (OpenAI, Together, OpenRouter, Groq, LM Studio, Ollama, vLLM…) plus native Anthropic. Configured via `~/.nexus/config.toml` or the web Settings panel.
- **Auto routing (optional)** — classify each task (coding / reasoning / trivial / balanced) and pick the best configured model by per-model strength scores. Off by default.
- **CLI + web** — both a terminal CLI (`nexus …`) and a chat-forward web UI.
- **ACP-ready** — external agents reachable through an `acp_call` tool (currently a stub; knowspace-gateway port is M2).

## Quick start

### Prereqs
- Python 3.11+ with [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Node 20+

### 1. Install + configure

```bash
cd agent
uv sync

# first-time config (writes ~/.nexus/config.toml with sensible defaults)
uv run nexus config init

# add your keys to the environment (the config references key_env vars, not inline keys)
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...

# inspect / edit
uv run nexus config show
uv run nexus providers list
uv run nexus models list
uv run nexus routing set auto        # optional — default is "fixed"
```

Alternatively, keep the old env-var quick path (`NEXUS_LLM_BASE_URL` + `NEXUS_LLM_API_KEY` + `NEXUS_LLM_MODEL`) — they override the config file if all three are set.

### 2. Run

```bash
# backend
uv run nexus serve --port 18989

# frontend (separate terminal)
cd ../ui
npm install
npm run dev     # http://localhost:5174
```

### 3. Or skip the web UI

```bash
uv run nexus chat              # interactive TUI chat with the agent
```

On first run, bundled skills from `./skills/` are copied into `~/.nexus/skills/` and marked `trust="builtin"`.

## CLI reference

```
nexus serve [--port 18989] [--host 127.0.0.1]
nexus chat  [--session <id>] [--model <id>] [--context <str>]

nexus config init | show | path
nexus providers list | add <name> --base-url <url> [--key-env <VAR>] | remove <name>
nexus models    list | add <id> --provider <p> --model <name> [--tags ...] [--strengths ...] | remove <id> | set-default <id>
nexus routing   set <fixed|auto>
nexus skills    list | view <name> | remove <name>
```

## Architecture

See `PLAN.md` for the full design.

```
agent/           FastAPI + tool-calling loop + skill registry + skill_manage + skills_guard
agent/src/nexus/ cli.py, config_file.py, agent/router.py, agent/llm.py (provider registry)
ui/              React 19 + Vite, chat-forward layout + Settings drawer
skills/          bundled skills seeded into ~/.nexus/skills on first run
```

Heritage: ai-monitor (helyx) for the agent loop + registry + session shape; hermes-agent patterns for self-evolving skills; knowspace patterns for the ACP bridge (still stubbed).

## Palette

Warm dark slate + copper + sage. All tokens in `ui/src/tokens.css`.

## API

| Route | Purpose |
| --- | --- |
| `POST /chat` | `{session_id?, message, context?}` → `{session_id, reply, trace, skills_touched}` |
| `GET /skills` | list skills |
| `GET /skills/{name}` | full SKILL.md |
| `GET /config` · `PATCH /config` | config read/merge (secrets redacted) |
| `GET /providers` | list providers + key status |
| `GET /models` · `POST /models` · `DELETE /models/{id}` | model CRUD |
| `GET /routing` · `PUT /routing` | routing mode + default model |
| `DELETE /sessions/{id}` | reset |
| `GET /health` | liveness |

## Status

MVP. Single-agent. In-memory sessions. No multi-agent routing split yet. ACP bridge stubbed. No persistent memory beyond the filesystem skill store. Auto-routing heuristic is intentionally simple (keyword classifier + strength scores).

## License

MIT.
