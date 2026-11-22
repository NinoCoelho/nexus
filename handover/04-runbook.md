# Runbook

How to run, inspect and debug a live Nexus. Setup is in
[`01-environment.md`](01-environment.md).

---

## Start / stop

```bash
cd agent
uv run nexus serve --port 18989          # foreground, always 127.0.0.1
uv run nexus daemon start | status | stop | logs
```

**After any change under `agent/src`, restart the daemon** before believing a
live report. See [`05-known-traps.md`](05-known-traps.md) #1.

Two processes must not run at once: a daemon plus a foreground `serve` means
two pollers on the same Telegram token (Telegram answers 409) and two sets of
background drivers on the same SQLite files. `daemon/manager.py` kills portless
twins on start/stop/restart, but a foreground server on a different port is
invisible to it.

## Health and first-look diagnostics

```bash
curl -s localhost:18989/health
curl -s localhost:18989/heartbeat | jq '{degraded, scheduler_status}'
curl -s localhost:18989/jobs | jq            # running subagents / background jobs
curl -s localhost:18989/models | jq '.[].id'
curl -s localhost:18989/providers | jq '.[] | {name, has_key, key_source}'
curl -s localhost:18989/routing | jq         # the default model
```

`GET /heartbeat` is the one to check when "scheduled things stopped happening":
it exposes `tick_count`, `last_tick`, `stalled`, `inflight` and `degraded`. A
frozen `last_check` plus the fire log told the whole story in minutes during
the scheduler overhaul.

Context health for a session:

```bash
curl -s localhost:18989/sessions/<sid>/usage | jq
curl -s localhost:18989/sessions/<sid>/context-stats | jq \
  '{usable_context_tokens, reserved_tokens, tool_discovery, history_recall}'
```

## Logs

| What | Where |
|---|---|
| Daemon | `uv run nexus daemon logs`, file in `~/.nexus/` |
| Packaged macOS app | `~/Library/Logs/Nexus/server.log` |
| llama-server | `~/Library/Logs/Nexus/llama-server.log` |
| LLM errors (structured) | `llm_errors` table in `sessions.sqlite` |

Useful query when a turn misbehaved:

```bash
sqlite3 ~/.nexus/sessions.sqlite \
  "SELECT created_at, error_type, status_code, provider, model, substr(message,1,120)
   FROM llm_errors ORDER BY id DESC LIMIT 20;"
```

Remember: log timestamps are **local**, SQLite `CURRENT_TIMESTAMP` is **UTC**.

## Inspecting state

```bash
# Sessions and bots
sqlite3 ~/.nexus/sessions.sqlite \
  "SELECT id, substr(title,1,40), project_id, hidden, archived, updated_at
   FROM sessions ORDER BY updated_at DESC LIMIT 20;"
sqlite3 ~/.nexus/sessions.sqlite "SELECT id, name, handle FROM projects;"

# What compaction removed (the archive is the source of truth)
ls ~/.nexus/vault/.session-memory/.parts/
head -c 400 ~/.nexus/vault/.session-memory/<sid>.md

# Spilled tool results
ls -lh ~/.nexus/vault/.tool-cache/ | tail
```

CLI equivalents: `nexus sessions`, `nexus bots`, `nexus vault`, `nexus models`,
`nexus providers`, `nexus kanban`, `nexus table`.

## Local models

```bash
curl -s localhost:18989/local/hardware | jq      # RAM, unified memory, chip
curl -s localhost:18989/local/installed | jq     # llama.cpp GGUFs
curl -s localhost:18989/local/mlx/available | jq # is the mlx extra importable
curl -s localhost:18989/local/mlx/installed | jq
curl -s localhost:18989/local/mlx/status | jq    # resident model, prefix caches, queue depth
curl -s -XPOST localhost:18989/local/mlx/unload  # free unified memory now
```

`/local/mlx/status` is the one to watch while testing MLX: `caches.*.by_type`
and `nbytes` show whether prefix reuse is happening, and `queue_depth` shows
turns waiting on the generation lock.

## The managed browser

```bash
curl -s localhost:18989/browser/instances | jq
curl -s 'localhost:18989/browser/status?key=main' | jq
curl -s 'localhost:18989/browser/nbie-report?minutes=60' | jq '.totals'
curl -s -XPOST localhost:18989/browser/stop -H 'content-type: application/json' -d '{"key":"main"}'
uv run nexus browser report
```

`nbie-report`'s `cache_hit_rate` is the number that says whether page
perception is wasting tokens. It is **in-process only** and dies with the
daemon (tracked as a fix in [`plan/fixes-backlog.md`](plan/fixes-backlog.md) #7).

Profiles: `main` → `~/.nexus/chrome-profile` on port 9223 (shared with the
chrome-devtools skill and the site-credential fill flow); every other context →
`~/.nexus/browser-profiles/<key>`. The registry is `~/.nexus/browser/instances.json`.

Chrome left running across restarts is intentional — the manager probes and
reuses it.

## Vault

```bash
curl -s 'localhost:18989/vault/search?q=foo' | jq '.[0:3]'
curl -s -XPOST localhost:18989/vault/reindex
uv run nexus vault --help
uv run nexus graphrag --help
```

The vault watcher (`[vault].watch`, default on) picks up external writes from
Syncthing / Obsidian / rsync, debounced, and re-runs the mtime-incremental
reindex. Disable with `NEXUS_DISABLE_VAULT_WATCH=1` (the test conftest sets
this).

## Common failures

| Symptom | Likely cause |
|---|---|
| Change has no effect | **stale daemon.** Restart. |
| "No model specified" / upstream 400 on a model id | a registry id was passed where an upstream model name belongs; or a dangling `default_model` after a model delete |
| Scheduled things stopped firing | check `GET /heartbeat` for `degraded` / `stalled`; a corrupt `heartbeat.db` is quarantined and recreated, with a background retry |
| Second bot's browser never starts | port/profile mismatch on retry — see [`05-known-traps.md`](05-known-traps.md) #17; check `instances.json` and for zombie Chromes |
| Telegram silent | two pollers (409), or a 401 disabled the poller — `GET /telegram/status`; re-saving the token restarts it |
| Spinner never ends | a non-serializable payload on the `done` SSE event used to do this; check the daemon log for a `json.dumps` failure |
| Local models vanished from config | a `save()` dropped a section — see [`05-known-traps.md`](05-known-traps.md) #3 |
| `.db` corruption | SQLite in a synced folder; check `~/.nexus/.stignore` |
| Context overflow errors | should be impossible by design (`hard_trim` is the guaranteed-fit backstop); if it happens, that path regressed |

## Backup and recovery

```bash
uv run nexus backup create        # whole ~/.nexus
uv run nexus backup restore <archive>
```

Vault-only undo, if `[vault].history` is enabled: a private git work-tree at
`~/.nexus/.vault-history` with per-path undo cursors
(`uv run nexus vault --help`, or right-click → Undo in the UI).

## Exposure (use sparingly)

Default is loopback-only. Two sanctioned paths:

```bash
uv run nexus tunnel start                           # Cloudflare quick tunnel + access code
uv run nexus tunnel start --provider tailscale      # Funnel (public)
uv run nexus tunnel start --provider tailscale-serve # tailnet-only, no access code
uv run nexus pair [--rotate]                        # QR + token for the iOS app
```

`/tunnel/*` and `/pair*` are **loopback-only at the route level**, so a
tunnel-authenticated client cannot take over the tunnel admin surface.
A hand-run proxy that sets `x-forwarded-*` headers is treated as tunnel traffic
and fails closed with 401 — use the managed providers.
