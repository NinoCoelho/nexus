# Environment setup

Target: a working backend, a working test run, and a known-good baseline you
can measure your own changes against.

Assumed host for the port work: **macOS on Apple Silicon**. The backend itself
runs on Linux and Windows too, but MLX and the native app do not.

---

## 1. Prerequisites

| Tool | Why | Install |
|---|---|---|
| `uv` | the only sanctioned way to run backend commands | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Python 3.12 or 3.13 | `requires-python = ">=3.12,<3.14"` | `uv` can fetch it |
| Node 20+ / npm | the web UI (kept buildable as a fallback) | nodejs.org or `brew install node` |
| Xcode + command-line tools | the native app | App Store |
| Google Chrome | the managed-browser subsystem | chrome.com |
| `git` | also used at runtime by the optional vault-history feature | preinstalled |

Optional: `tailscale` (for the tailnet bind / iOS client), `poppler`
(`brew install poppler`, only for scanned-PDF OCR).

## 2. Clone

Nexus depends on **loom**, the agentic-core framework. By default `uv sync`
pulls it from GitHub, so a single clone works. For local development against
loom, clone it as a **sibling**:

```
<parent>/
  loom/     # git@github.com:NinoCoelho/loom.git
  nexus/    # this repo
```

```bash
git clone git@github.com:NinoCoelho/nexus.git
cd nexus
git fetch origin
git checkout handover/macos-port     # this branch
```

## 3. Backend

```bash
cd agent
uv sync                              # installs everything, pulls loom from GitHub
uv pip install -e ../../loom         # ONLY if you cloned the sibling, to swap in the editable copy
```

`uv sync` is slow the first time: it pulls spaCy with two pinned model wheels,
`fastembed` (ONNX Runtime), `faster-whisper` (CTranslate2), `piper-tts`,
`duckdb` and PyAV. There is **no torch** anywhere in the tree — all local ML is
ONNX, CTranslate2, or an HTTP endpoint.

Apple Silicon only, for the MLX work:

```bash
uv sync --extra mlx                  # mlx + mlx-lm
```

That extra carries a `sys_platform == 'darwin' and platform_machine == 'arm64'`
marker, so it resolves to nothing elsewhere instead of failing. The provider
import is also guarded, so a missing extra logs a warning rather than crashing.

Other optional extras: `bedrock` (boto3), `markitdown`, `ocr`, `pdf`, `dev`.

## 4. First run

```bash
cd agent
uv run nexus config init             # writes ~/.nexus/config.toml
uv run nexus config show
```

You need at least one model. Either add a cloud provider key, or install a
local model. There is no bundled default in a source checkout.

```bash
# Cloud: set the env var the config references, e.g.
export ANTHROPIC_API_KEY=...

# Or on Apple Silicon, a local MLX model (needs the `mlx` extra):
uv run nexus serve --port 18989      # then, in another shell:
curl -s 'http://127.0.0.1:18989/local/mlx/available'
curl -s 'http://127.0.0.1:18989/local/mlx/hf/search?q=qwen3&limit=5'
curl -s -XPOST 'http://127.0.0.1:18989/local/mlx/download' \
     -H 'content-type: application/json' -d '{"repo_id":"mlx-community/Qwen3-4B-4bit"}'
# then POST /local/mlx/activate {"slug":"qwen3-4b-4bit"}
```

Run the server:

```bash
uv run nexus serve --port 18989      # foreground, always 127.0.0.1
# or
uv run nexus daemon start            # background; pid + log in ~/.nexus/
uv run nexus daemon status
uv run nexus daemon logs
```

Then `http://127.0.0.1:18989/` for the bundled UI, or `uv run nexus chat` for a
terminal TUI.

## 5. Tests — and your baseline

```bash
cd agent
uv run pytest --ignore=tests/live    # the whole suite
uv run pytest tests/test_router.py::test_name   # one test
uv run ruff check src tests          # line-length 100
```

**Establish your own baseline before blaming your change.** At `542bfb5` the
suite is **~1,983 passed with 6 pre-existing failures**, confirmed in a clean
worktree of HEAD. Known offenders include `test_server_sse` (the yolo case) and
`test_anthropic_oauth_headers`. `tests/live/` is excluded because it hits the
network.

The repo is **not** ruff-clean at baseline either. Lint what you touch.

```bash
# Record a baseline you can diff against:
uv run pytest --ignore=tests/live -q > /tmp/baseline.txt 2>&1 ; tail -5 /tmp/baseline.txt
```

### Tests that can damage your real config

`local_llm/manager.py::_load_config` builds its path inline from `Path.home()`
rather than from a patchable module constant. A test that calls it unmocked
**rewrites your real `~/.nexus/config.toml`** — this has happened (a
delete-model test silently replaced the user's model list). Patch
`nexus.config_file.CONFIG_PATH` to `tmp_path`, or replace
`_load_config`/`_save_config` with an in-memory pair as
`tests/test_mlx_config_survives_cleanup.py` does.

## 6. Web UI

```bash
cd ui
npm install
npm run dev      # Vite on http://localhost:1890, expects the API on :18989
npm run build    # tsc + vite; type errors fail the build
```

There is **no lint or test script for the UI** — `npm run build` is the only
gate. The API base comes from `VITE_NEXUS_API` (default
`http://localhost:18989`).

Note: `npm install` can be blocked on corporate networks by a registry proxy.
If you see `403 ... DevMediaBlock` on a transitive package, you are on such a
network; the UI cannot be built there and that is a policy decision, not a bug
to route around.

## 7. Native app

```bash
cd packaging/macos
swift build -c release --arch arm64
```

`packaging/build.sh` drives the full pipeline: stages a
`python-build-standalone` CPython into `Contents/Resources/python`, copies
`site-packages`, pre-caches the fastembed and spaCy models, stitches in the
Swift binary, then code-signs every nested binary and `.dylib`, notarizes and
produces a `.pkg`. Signing config lives in `packaging/build.conf` (git-ignored;
see `build.conf.example`) and needs an Apple Developer account. Without it the
build falls back to ad-hoc signing.

The signed `.app` is currently **~1.7 GB** and the `.pkg` ~600 MB.

## 8. Chrome extension

Local-only by design; never on the Web Store.

```bash
uv run nexus chrome install    # copies to ~/.nexus/chrome-extension, opens the guided page
uv run nexus chrome doctor     # verifies server, files, extension heartbeat
```

Then Chrome → `chrome://extensions` → Developer mode → Load unpacked →
`~/.nexus/chrome-extension`.

## 9. Where state lives

Everything under `~/.nexus/` (overridable with `NEXUS_HOME`):

| Path | Contents |
|---|---|
| `config.toml` | canonical config. Plaintext — treat as private. |
| `secrets.toml` | credential store, mode 0600 |
| `vault/` | **the user's data** — markdown, synced |
| `vault/.tool-cache/` | spilled tool results (microcompaction pointers) |
| `vault/.session-memory/` | rolling per-session summaries + `.parts/` archives |
| `sessions.sqlite` | sessions, messages, FTS, HITL, projects/bots, Telegram bindings |
| `vault_index.sqlite`, `vault_meta.sqlite` | vault FTS; tags + backlinks |
| `graphrag/` | knowledge-graph triple store + manifest |
| `dream_state.sqlite`, `workflow_runs.sqlite`, `heartbeat.db` | background state |
| `site_credentials.db` + `keys/` | Fernet-encrypted site logins, 0600 |
| `skills/` | the agent's skills (bundled ones seeded on first run) |
| `models/llm/`, `models/mlx/` | local model weights |
| `client_token`, `.port`, `.host`, daemon pid/log | runtime |

Back it up with `uv run nexus backup create` / `restore`.

## 10. Useful CLI surface

```
nexus serve | chat | config (init|show) | daemon (start|status|stop|logs)
nexus models | providers | sessions | vault | kanban | table | bots
nexus skills | skill | graphrag | trajectories | backup
nexus browser | chrome | cookies | tunnel | pair
```
