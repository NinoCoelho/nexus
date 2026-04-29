# Nexus knowledge base

User-facing reference for "how do I X in Nexus?" questions. Each section
below stands alone — the agent retrieves them via BM25 (`nexus_kb_search`)
and answers from one or two of them at a time.

## configure a new model

Models are declared in `~/.nexus/config.toml` and registered against a
provider. The fastest path is the **Settings → Models** drawer in the UI:
search Hugging Face / OpenRouter, click *Register*, enter your API key,
and the model appears in the model picker.

Manual TOML form (e.g. to add OpenAI's `gpt-4o`):

```toml
[providers.openai]
base_url     = "https://api.openai.com/v1"
api_key_env  = "OPENAI_API_KEY"   # or use_inline_key = true to store in secrets.toml
type         = "openai_compat"

[[models]]
id           = "openai/gpt-4o"
provider     = "openai"
model_name   = "gpt-4o"
tags         = ["chat"]
tier         = "balanced"
context_window = 128000

[agent]
default_model = "openai/gpt-4o"
```

Then either restart Nexus or hit *Reload* in Settings. Any OpenAI-compatible
endpoint works — Together, OpenRouter, Groq, vLLM, LiteLLM, Ollama. Native
Anthropic uses `type = "anthropic"`.

## inline keys vs env vars

Two ways to give a provider its API key:

- `api_key_env = "OPENAI_API_KEY"` — Nexus reads from the named environment
  variable at startup. Best when you already manage secrets via your shell.
- `use_inline_key = true` (and leave `api_key_env = ""`) — Nexus reads from
  `~/.nexus/secrets.toml` under the provider's name. Mode is enforced to
  `0600`. Set via Settings → Models → key field, or directly:

  ```toml
  # ~/.nexus/secrets.toml
  [keys]
  openai = "sk-..."
  ```

Both are equally functional; pick whichever matches your secret-management
habit.

## change theme / colors / appearance

Open Settings → Appearance. The dropdown sets the active theme; the
chosen value persists to `~/.nexus/ui_settings.json` and is applied as a
`data-theme` attribute on `<html>` so all components pick it up. Custom
themes live as CSS files under `ui/src/themes/` in a dev checkout — add
your `.css`, register it in the theme registry, and rebuild the UI
(`npm run build` in `ui/`).

Font size, density, and accent color are also under Appearance. None of
these knobs touch `config.toml` — they're UI-local.

## update the vault GraphRAG index

The vault uses two indices:

1. **FTS5 + tags + backlinks** — fully automatic; a file save updates the
   index synchronously on the next request.
2. **GraphRAG** (entities, relations, embeddings) — needs an explicit
   re-index for bulk changes. Trigger it via:

   - **UI:** Settings → GraphRAG → *Reindex vault*.
   - **CLI:** `uv run nexus vault reindex` from the `agent/` directory.
   - **Agent:** the `vault_curator` skill can do it incrementally.

A single edited file is re-extracted next time the agent reads it; full
re-index is only needed after restoring a backup, switching extraction
models, or editing the ontology. Status shows in the UI's GraphRAG tab.

## manage skills

Skills live as directories in `~/.nexus/skills/<name>/SKILL.md`. The agent
discovers them by scanning that folder; only `name` + `description` enter
the system prompt — full bodies are loaded on demand.

- **List:** `nexus skills list` (or look at `~/.nexus/skills/`).
- **Install from path or git:** `nexus skills install ./path/to/skill`
  or `nexus skills install https://github.com/you/repo.git --subdir skills/foo`.
- **Remove:** `nexus skills remove <name>`. Bundled skills get re-seeded
  on next start — use `--force` to keep them gone for one session.
- **Edit in place:** edit the `.md` file. Changes are picked up on next
  request without a restart for user/agent skills.
- **Trust tier:** stored in `<skill>/.meta.json`. `builtin` for skills
  shipped with Nexus, `user` for ones you installed, `agent` for ones the
  model wrote at runtime. All three pass the same regex security guard
  before being enabled.

The agent itself can author skills via the `skill_manage` tool — the guard
catches credential exfil and destructive shell patterns and rolls back on a
failed scan.

## set up the public tunnel

Nexus binds to `127.0.0.1` only. To share access to your phone or a
teammate, start a tunnel:

```
uv run nexus tunnel start
```

The first run downloads `cloudflared` (no Cloudflare account needed) and
prints two things: a public URL and an 8-char access code formatted
`XXXX-XXXX`. The phone visits the URL, types the code, and gets a
session cookie. The long token never appears in the URL.

- `nexus tunnel status` — see the active URL and remaining session.
- `nexus tunnel stop` — tear down the tunnel and revoke the access code.

The auth gate splits requests on whether proxy headers are present:
loopback bypasses auth; tunnel-routed requests require the cookie. Tunnel
admin endpoints are loopback-only, so even a logged-in remote can't take
over the tunnel.

## audio transcription

`Settings → Transcription` chooses local (faster-whisper) or remote
(OpenAI-compatible `/v1/audio/transcriptions`). Local is the default; it
ships pre-cached embedding/spaCy models in the bundled `Nexus.app` so
first-run works offline. Remote needs a `base_url` and an API key.

Recordings made in chat trigger transcription automatically; the live
waveform + cancel button are in the chat input area.

## human-in-the-loop (HITL) and YOLO

Two tools require user approval: `ask_user` (confirm/choice/text prompts)
and `terminal` (shell commands). Approvals are streamed to the UI as SSE
events on `/chat/{sid}/events` and rendered as modal dialogs.

Toggle YOLO mode in Settings to auto-approve everything for unattended
runs. Terminal commands always show the command before running, even in
YOLO, so you can still cancel mid-flight.

When the browser tab is closed, Web Push (VAPID) delivers the approval
prompt as a system notification — register the subscription in Settings →
Notifications.

## backup and restore

```
uv run nexus backup create        # writes ~/.nexus/backups/<timestamp>.tar.gz
uv run nexus backup list
uv run nexus backup restore <file>
```

A backup contains the entire `~/.nexus/` tree: vault, skills, sessions
DB, config, secrets, GraphRAG store. Restore is destructive — it wipes
the existing tree before extracting. Use `--dry-run` to preview.

Skill: the bundled `vault-curator` skill can also export individual vault
folders to markdown bundles.

## daemon mode

Run Nexus headless in the background:

```
uv run nexus daemon start    # writes pid + log to ~/.nexus/
uv run nexus daemon status
uv run nexus daemon logs     # tails the log
uv run nexus daemon stop
```

For autostart on login: macOS uses `launchctl` (see `nexus daemon install`),
Linux uses systemd, Windows uses NSSM. The macOS `Nexus.app` bundle
handles its own autostart via Login Items + a menu bar tile.

## demo model (`tags: ["demo"]`)

If your `~/.nexus/config.toml` was seeded with a `demo` provider and a
`demo/<name>` model tagged `["demo"]`, you're using a temporary demo LLM
the maintainer ships with the bundle. It's intended for evaluation and
asking questions about Nexus itself — not for production workloads.

To replace it: open Settings → Models, register your own provider + model,
set it as the default, and reload. The demo entries can stay (in case you
want to switch back) or be deleted from the TOML.

## settings precedence

When the same setting appears in multiple places, this is the resolution
order (highest wins):

1. Environment variables (only the legacy three: `NEXUS_LLM_BASE_URL`,
   `NEXUS_LLM_API_KEY`, `NEXUS_LLM_MODEL` — overrides only when **all
   three** are set).
2. `~/.nexus/config.toml` — canonical source for everything else.
3. Built-in defaults from `agent/src/nexus/config_schema.py`.

Per-session UI tweaks (theme, layout) live in `~/.nexus/ui_settings.json`
and are independent. Secrets always come from `~/.nexus/secrets.toml`
(via `use_inline_key`) or the named env var (`api_key_env`), never from
`config.toml`.

## where things live on disk

```
~/.nexus/
├── config.toml          # providers, models, agent settings
├── secrets.toml         # mode 0600 — inline API keys
├── ui_settings.json     # theme, density, layout
├── access_token         # persistent loopback token
├── host.json            # bind host/port preferences
├── vault/               # markdown notes, kanban boards, calendars
├── skills/              # one dir per skill, with SKILL.md + .meta.json
├── sessions.db          # SQLite WAL — chat history
├── graphrag/            # entity/relation/embedding store
├── backups/             # snapshots from `nexus backup create`
└── logs/                # daemon + cloudflared logs
```

## reload after editing config.toml

Most changes require a restart. Exceptions:

- **Skill edits** are picked up on the next agent request.
- **Vault file edits** are indexed on next read.
- **Theme/appearance** apply immediately.
- **Provider/model edits** need either *Reload providers* in Settings or a
  full restart of the daemon / app.

If something looks stale, `uv run nexus daemon stop && uv run nexus daemon start`
is always safe.
