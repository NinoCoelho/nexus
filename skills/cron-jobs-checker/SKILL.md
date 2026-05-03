---
name: cron-jobs-checker
description: Use this whenever you need to check scheduled cron jobs, diagnose failures, retry missed runs, and fix broken jobs. Prefer over manually reading logs one by one.
type: procedure
role: operations
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use
- User asks "check jobs", "check cron", "what ran last night", "retry failed jobs"
- Need to diagnose why a cron job didn't run or produced errors
- Want to re-execute jobs that failed or were missed

## Steps

### 1. Read the crontab

```bash
crontab -l
```

Parse each line into: schedule, command, log file (from `>>` redirect).

### 2. Check each job's log freshness

For every job, compare the log file's modification time against the expected last run time (based on schedule and current date):

```bash
# Get modification time (macOS)
stat -f "%Sm" -t "%Y-%m-%d %H:%M" /tmp/<logfile>.log

# Get modification time (Linux)
stat -c "%y" /tmp/<logfile>.log | cut -d'.' -f1

# Get current date
date
```

**Classification:**
- Log modified since the last scheduled run -> job **ran**. Check for errors inside the log (next step).
- Log NOT modified -> job **didn't fire** or **silent failure**.

### 3. Inspect logs for errors

```bash
tail -50 /tmp/<logfile>.log
```

Common failure patterns to grep for:
- `command not found` -> PATH issue (node/npx live in `/opt/homebrew/bin/` on macOS or `/usr/local/bin/` on Linux)
- `ModuleNotFoundError` / `ImportError` -> Python dependency missing in cron env
- `Traceback` / `Error` / `error` -> script-level failure
- `exit code` non-zero -> check stderr
- 0 items fetched -> partial failure (RSS/API issue, not crash)

### 4. Diagnose and classify each job

Present a table to the user:

| Job | Schedule | Ran? | Status | Issue |
|---|---|---|---|---|
| ... | ... | OK/MISS | OK / Partial / Broken | ... |

### 5. Retry failed jobs

For each broken/missed job, run it manually with the correct environment:

**Python jobs:**
```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
python3 <script-path> 2>&1 | tail -30
```

**Node.js jobs:**
```bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
/opt/homebrew/bin/node <script-path> 2>&1 | tail -30
```

Check the output. If it succeeds, the root cause was cron PATH -- proceed to step 6.
If it fails, diagnose the error (missing module, API key, network issue) and fix the script.

### 6. Fix cron PATH (if that was the root cause)

Prepend to crontab:

```bash
# Edit crontab safely:
(crontab -l | grep -v "^PATH="; echo "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin") | crontab -
```

Alternatively, use full binary paths in each cron entry.

### 7. Verify fix

After fixing, optionally do a dry-run of the failing job to confirm it works with the corrected environment.

## Gotchas
- **Cron PATH is minimal** -- only `/usr/bin:/bin`. Homebrew binaries (`node`, `npx`, `ffmpeg`, etc.) live in `/opt/homebrew/bin/` (macOS) or `/usr/local/bin/` (Linux) which cron does NOT include. This is the #1 cause of "command not found" failures.
- **Silent failures** -- cron captures stdout/stderr to the log file only if `>> log 2>&1` is present. Missing redirect = silent death.
- **Python shebangs** -- scripts using `#!/usr/bin/env python3` work in cron because `/usr/bin/python3` is in default PATH. But pip-installed packages may go to locations not in cron's `PYTHONPATH`.
- **`*/2` day schedule** -- `0 6 */2 * *` means days 2,4,6... of month, NOT "every 2 days from now."
- **Network in cron** -- jobs that call external APIs may fail if the machine was asleep at the scheduled time. cron does NOT wake the machine; `anacron` or `launchd` would be needed for that.
