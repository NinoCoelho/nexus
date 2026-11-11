---
name: coding-agents
description: Delegate self-contained coding work to external coding-agent CLIs (Claude Code, Codex, OpenCode) via agent_delegate + delegate_status. Use when the user asks to "hand this to claude/codex/opencode", wants a second model's plan or implementation, or needs a large batch refactor done in a repo.
type: procedure
role: engineering
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# coding-agents

Delegate self-contained coding tasks to external coding agents running headlessly. Nexus stays the orchestrator: you frame the task, pick the agent, dispatch, monitor, collect.

## When to delegate

- The task is **self-contained** (clear input → code changes/output) and long-running: batch refactors, migrations, "write the implementation of this spec", mechanical multi-file edits, doc passes.
- You want a **second model's take**: alternative plan, independent review, comparison.
- You should NOT delegate interactive work — anything that needs back-and-forth with the user mid-task, or that you can finish faster with a direct `terminal` command.

## Preferred path: `agent_delegate` → `delegate_status`

If `[delegate]` is configured in `~/.nexus/config.toml`, both tools are core (always visible).

### Async by default (builds, edits, anything > ~2 min)

Call `agent_delegate` **without** `wait` — the default is fire-and-monitor:

1. The call returns immediately: `{job_id, log_ref, status: "started"}`.
2. Output streams **live** into `log_ref` (`vault://.tool-cache/delegate-<agent>-<ts>.md`) — follow progress anytime with `vault_read`.
3. Poll completion with `delegate_status {job_id}` (returns `running` / `done` / `failed` / `killed` / `timed_out`, exit code, output excerpt, log tail).
4. The job also appears in the UI's running-tasks indicator and can be killed there (or via the jobs API) without losing the log.

**Never block the turn on a long build.** Sync (`wait: true`) is only for quick queries where you need the answer inside this turn (e.g. "what does this function do?").

**Never re-send a delegation because a poll timed out.** If the job is `running`, it is alive — keep monitoring. Only re-delegate when the status is terminal (`failed` / `killed` / `timed_out`) and you understand the cause.

### The user's plan → approve → build flow

When the user asks for work via a coding agent and wants to review before execution:

1. **Plan**: `agent_delegate {agent, task: "<plan-only prompt — analyze, propose, do NOT edit files>"}`
2. Monitor with `delegate_status`; when done, read the plan (log_ref), analyze it, contrapose where needed — **max 2 delegate interactions**; if no grave divergence, present the plan to the user as-is.
3. **Build**: only after the user approves — `agent_delegate {agent, task: "<full implementation prompt>"}`, monitor, report.

### Task framing

`task` must be **self-contained** — the delegate has no access to this conversation. Include: goal, repo paths (`cwd`), constraints, and what "done" looks like. For long prompts, write the task to the vault first and paste its content.

- `agent`: configured name (`claude`, `codex`, `opencode`, ...).
- `cwd`: the repo directory to work in.
- Large outputs come back as a `vault://.tool-cache/...` pointer — read with `vault_read`.

## Choosing the agent

- Default to whatever the user names. Otherwise: **claude** for nuanced refactors and codebase reasoning, **codex** for precise mechanical edits and fast iteration, **opencode** when the user prefers its provider/model setup or has it configured with a specific model.
- If a delegate fails (command not found, auth error, timeout), report the exact error — don't silently retry with another agent's API keys or flags.

## Fallback: `terminal` (when `agent_delegate` is not configured)

| Agent | Command |
|---|---|
| Claude Code | `claude -p "<task>" --output-format json` |
| Codex | `codex exec "<task>"` |
| OpenCode | `opencode run "<task>"` |

Headless invocations, run with the working directory set to the target repo. **Never run a long task synchronously in the terminal tool** — the default timeout (~60s) kills the wrapper while the agent keeps running orphaned. Instead: write the task to a file, launch detached with output to a log, then poll:

```sh
opencode run "$(cat /tmp/task.txt)" > /tmp/delegate.log 2>&1 &
echo $!   # pid — poll with: ps -p <pid> && tail /tmp/delegate.log
```

Notes per agent:

- **claude**: `-p` is headless; `--output-format json` returns a JSON envelope whose `result` field holds the final message. `--permission-mode acceptEdits` lets it edit files without per-edit prompts; `--dangerously-skip-permissions` removes all prompts — only with explicit user approval.
- **codex**: `codex exec` runs non-interactively with the workspace-write sandbox by default; `--full-auto` removes approvals (bypass flag — user confirmation).
- **opencode**: `opencode run` executes the prompt in the current directory; `--model provider/model` picks a model.

## After the run

- Each delegate starts fresh in `cwd`. Verify the outcome in the repo (`git diff`, `git status` via `terminal`) before claiming success.
- Surface a concise summary plus the pointer to the full transcript. Never paste multi-thousand-line output into chat.
