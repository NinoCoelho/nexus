# Nexus

A self-evolving agentic platform. Proprietary agent core with runtime skill authoring (Hermes-style markdown skills), CLI + web UI, pluggable providers, optional auto model routing, and ACP as a secondary lane for external agents.

## What it is

- **Self-evolving** — the agent can create, edit, and patch its own skills at runtime via a `skill_manage` tool. Skills are markdown with YAML frontmatter under `~/.nexus/skills/<name>/`. A regex guard scans agent-authored skills for credential exfil and destructive patterns; dangerous writes roll back.
- **Progressive disclosure** — the system prompt carries only skill names + descriptions. The agent pulls full skill bodies on demand.
- **Multi-provider** — any OpenAI-compatible endpoint (OpenAI, Together, OpenRouter, Groq, LM Studio, Ollama, vLLM…) plus native Anthropic. Configured via `~/.nexus/config.toml` or the web Settings panel.
- **Auto routing (optional)** — classify each task (coding / reasoning / trivial / balanced) and pick the best configured model by per-model strength scores. Off by default.
- **CLI + web** — both a terminal CLI (`nexus …`) and a chat-forward web UI with chat, vault, kanban, graph, agent-graph, and insights views.
- **Vault-native knowledge** — markdown files under `~/.nexus/vault/` with FTS5 search, backlinks, and kanban boards (any `.md` with `kanban-plugin:` frontmatter is a board — no separate store).
- **Human-in-the-loop** — agent can call `ask_user` to request confirmation/choice/text, and `terminal` to request shell commands; YOLO mode auto-answers for unattended runs.
- **ACP-ready** — external agents reachable through an `acp_call` tool (currently a stub; knowspace-gateway port is M2).

## Quick start

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/NinoCoelho/nexus/main/install.sh | bash
```

Clones into `~/nexus` (override with `NEXUS_DIR=…`), installs [uv](https://docs.astral.sh/uv/) if missing, runs `uv sync` + `npm install`, writes a default `~/.nexus/config.toml`, and drops a `nexus` launcher into `~/.local/bin/`. Env overrides: `NEXUS_DIR`, `NEXUS_REF`, `NEXUS_NO_UI=1`, `NEXUS_NO_INIT=1`.

After install:

```bash
export OPENAI_API_KEY=sk-...        # and/or ANTHROPIC_API_KEY etc.
nexus daemon start                  # http://localhost:18989
cd ~/nexus/ui && npm run dev        # http://localhost:1890
```

### Manual install

Prereqs: Python 3.11+ with [uv](https://docs.astral.sh/uv/) (`brew install uv`), Node 20+.

```bash
git clone git@github.com:NinoCoelho/nexus.git
cd nexus/agent
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

#### Option A: Daemon mode (recommended)

```bash
# Start the daemon in the background
uv run nexus daemon start

# Check daemon status
uv run nexus daemon status

# View daemon logs
uv run nexus daemon logs

# Stop daemon when done
uv run nexus daemon stop
```

#### Option B: Install as system service

```bash
# Install as user service (auto-starts on login)
uv run nexus daemon install --user

# Uninstall service
uv run nexus daemon uninstall --user
```

#### Option C: Manual server

```bash
# Start server manually
uv run nexus serve --port 18989

# Frontend (separate terminal)
cd ../ui
npm install
npm run dev     # http://localhost:1890
```

### 3. Or skip the web UI

```bash
uv run nexus chat              # interactive TUI chat with the agent
```

On first run, bundled skills from `./skills/` are copied into `~/.nexus/skills/` and marked `trust="builtin"`.

## CLI reference

### Daemon Management

```bash
# Start/stop/restart daemon
nexus daemon start [--host 127.0.0.1] [--port 18989] [--detach/--no-detach]
nexus daemon stop
nexus daemon restart

# Check daemon status with real-time metrics
nexus daemon status

# Install/uninstall system service
nexus daemon install [--user|--system]     # systemd/launchd service
nexus daemon uninstall [--user|--system]

# View daemon logs
nexus daemon logs [--lines 50] [--follow]   # Press 'q' to quit follow mode
```

### Server & Chat

```bash
nexus serve [--port 18989] [--host 127.0.0.1]
nexus chat  [--session <id>] [--model <id>] [--context <str>]
```

### Configuration

```bash
nexus config init | show | path
nexus providers list | add <name> --base-url <url> [--key-env <VAR>] | remove <name>
nexus models    list | add <id> --provider <p> --model <name> [--tags ...] [--strengths ...] | remove <id> | set-default <id>
nexus routing   set <fixed|auto>
nexus skills    list | view <name> | remove <name>
```

### Advanced Commands

```bash
# Session management
nexus sessions list | show <id> | export <id> | import <path>

# Vault operations
nexus vault ls | search <query> | reindex | tags | backlinks <path>

# Kanban boards
nexus kanban boards | list [--board default]

# Analytics
nexus insights [--days 30] [--json]
```

## Daemon Features

The Nexus daemon provides robust background process management with comprehensive monitoring and control:

### **Key Benefits**
- **Auto-restart**: Configurable auto-restart on crash via system services
- **Resource Monitoring**: Real-time CPU, memory, and process tracking
- **Centralized Logging**: All daemon output to `~/.nexus/nexus-daemon.log`
- **Cross-platform**: Works on Linux (systemd), macOS (launchd), and Windows
- **Graceful Shutdown**: Proper process cleanup and PID file management

### **Service Installation**
- **User Service**: Runs as current user, auto-starts on login
- **System Service**: Runs at system level (requires root/admin)
- **Auto-management**: System service manager handles restarts and monitoring

### **Log Management**
- **View Logs**: `nexus daemon logs` shows recent log entries
- **Follow Mode**: `nexus daemon logs --follow` for real-time monitoring
- **Secret Redaction**: Automatic masking of API keys and credentials
- **Log Rotation**: Configurable log file management

### **Process Control**
- **Status Monitoring**: Live process information with resource usage
- **Graceful Stops**: Proper SIGTERM handling and cleanup
- **PID Tracking**: Automatic PID file creation and cleanup
- **Port Management**: Automatic port binding and conflict resolution

## Architecture

See `PLAN.md` for the full design.

```
agent/           FastAPI + tool-calling loop + skill registry + skill_manage + skills_guard
agent/src/nexus/ cli.py, daemon.py, config_file.py, agent/router.py, agent/llm.py (providers),
                 vault.py + vault_kanban.py, tools/ (kanban, vault, memory, http, ask_user, terminal)
ui/              React 19 + Vite — chat, vault (with KanbanBoard), graph, agent graph, insights
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
| `POST /chat/stream` | SSE per-turn stream (deltas, tool calls, done) |
| `GET /chat/{sid}/events` | SSE out-of-band events (`user_request`, cancellations) |
| `GET /graph` | agent/skill/session graph for the UI agent-graph view |
| `GET /insights` | token/cost/model/tool analytics |
| `GET /vault/*` · `POST /vault/dispatch` | vault read/search + spawn session from file/card |
| `GET /health` | liveness |
| `GET /metrics` | daemon performance metrics (CPU, memory, uptime) |

## Daemon Integration

The daemon seamlessly integrates with all existing API endpoints and maintains full compatibility with the web UI and CLI commands. When running as a daemon:

- **All endpoints available**: `/chat`, `/health`, `/config`, etc.
- **Auto-restart resilience**: System services automatically restart crashed daemons
- **Resource isolation**: Daemon runs independently of terminal sessions
- **Logging continuity**: All activity logged to centralized file with rotation support

## Status

MVP with comprehensive daemon management. Single-agent. In-memory sessions. No multi-agent routing split yet. ACP bridge stubbed. No persistent memory beyond the filesystem skill store. Auto-routing heuristic is intentionally simple (keyword classifier + strength scores).

### Daemon Status
✅ **Production-ready daemon functionality**:
- Full process lifecycle management (start/stop/restart/status)
- Cross-platform service installation (systemd/launchd/Windows)  
- Real-time monitoring with resource metrics
- Centralized logging with secret redaction
- Graceful shutdown and cleanup
- Auto-restart capabilities via system services
- Seamless API endpoint compatibility

## License

MIT.
