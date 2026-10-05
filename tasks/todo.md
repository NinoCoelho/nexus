# Scheduler / Heartbeat Reliability Overhaul — 2026-10-04

## Summary
User's "check email every 30 min" calendar routine almost never fired.
Root causes found (live-verified): scheduler loop froze behind inline LLM
sweeps (sequential awaits, no timeout), heartbeat.db corruption from
Syncthing-synced SQLite (138 silent bootstrap failures = zero scheduling),
tz-naive crash in sweep_missed, twin daemons from racing restarts, and
`_conflicts-quarantine` calendars firing duplicate events. Workflow
schedule triggers were dead code (croniter missing, engine ref never wired).

## Checklist
- [x] A: runtime repair — twin daemons killed, clean single daemon, `~/.nexus/.stignore` (no `*.db*` sync)
- [x] B1: self-healing bootstrap (`_open_heartbeat_store` quarantine+recreate, `_retry_heartbeat_bootstrap` 60s loop, `/heartbeat` exposes `scheduler_status` + `degraded`)
- [x] B2: sweep_missed tz-naive regression test (fix was already uncommitted in repo)
- [x] B3: `_`-prefixed dirs excluded from list_calendars (quarantine dupes)
- [x] B4: twin-daemon kill on start/stop/restart + port-free wait; restarter = frequent `nexus daemon restart` (248× in history), now race-proof
- [x] B5: wake catch-up — sleep-gap detection in loop; fire-window events fire once when overdue (test added)
- [x] B6: ig-engagement-scanner schedule fixed to `0 9 * * * America/New_York`
- [x] C1: loom scheduler — per-driver task + 300s timeout + skip-if-running
- [x] C2: loom scheduler — status()/last_tick/tick_count/stalled watchdog surface
- [x] C3: coordinator sweep + dream cycle detached (create_task + in-flight flags)
- [x] C4: loom cron — `HH:MM` daily + trailing IANA tz (`0 9 * * * America/New_York`)
- [x] D: workflow schedule triggers wired (croniter dep, engine/store refs via setters in `_startup_workflows`)
- [x] E: tests — loom 40 passed (cron+scheduler), nexus 1511 passed / 26 skipped
- [x] E: deploy — loom editable-installed into daemon venv, daemon restarted, ticks flowing, single process
- [x] Docs: CLAUDE.md "Heartbeat / scheduling reliability model" + lessons.md

## Review / Results
- Daemon: single process, `GET /heartbeat` shows tick_count advancing,
  stalled=false, degraded=false; workflow_schedule ticking with engine ref.
- Email routine: fires on the 30-min boundary inside 08:00–23:00 window
  (verified once per gap by test + live fire log), duplicates from
  quarantine gone (list_calendars filter).
- Deployment note: daemon venv loom is now an EDITABLE install of
  ~/Code/loom (`uv pip install -e`); nexus tool install was already
  editable to the repo. Restarting the daemon picks up both.
- Known limitation (documented): plain-cron schedules still silently skip
  slots missed while the machine sleeps; interval/fire-window schedules
  catch up.
- Pre-existing bug spotted (not fixed here): routes/chat.py SSE
  `to_sse()` crashes when SessionEvent.data contains a ChatMessage —
  see daemon log `TypeError: Object of type ChatMessage is not JSON serializable`.
