---
name: coding-agents
description: Delegate self-contained coding work to external coding-agent CLIs (Claude Code, Codex, OpenCode) via agent_delegate or terminal. Use when the user asks to "hand this to claude/codex/opencode", wants a second model's implementation, or needs a large batch refactor done in a repo.
type: procedure
role: engineering
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# coding-agents

Delegate self-contained coding tasks to external coding agents running headlessly. Nexus stays the orchestrator: you frame the task, pick the agent, collect the output.

## When to delegate

- The task is **self-contained** (clear input → code changes/output) and long-running: batch refactors, migrations, "write the implementation of this spec", mechanical multi-file edits.
- You want a **second model's take**: alternative implementation, independent review, comparison.
- You should NOT delegate interactive work — anything that needs back-and-forth with the user mid-task, or that you can finish faster with a direct `terminal` command.

## Preferred path: `agent_delegate`

If `[delegate]` is configured in `~/.nexus/config.toml`, call the `agent_delegate` tool (discover it with `tool_search` if hidden):

- `agent`: the configured name (`claude`, `codex`, `opencode`, ...)
- `task`: a **self-contained prompt** — include the goal, file paths, constraints, and what "done" looks like. The delegate has no access to this conversation.
- `cwd`: the repo directory to work in.
- Large outputs come back as a `vault://.tool-cache/...` pointer — read the file with `vault_read` if you need the full transcript.

## Fallback: `terminal` (when `agent_delegate` is not configured)

Headless invocations, run with `cwd` set to the target repo:

| Agent | Command |
|---|---|
| Claude Code | `claude -p "<task>" --output-format json` |
| Codex | `codex exec "<task>"` |
| OpenCode | `opencode run "<task>"` |

Notes per agent:

- **claude**: `-p` is headless (non-interactive); `--output-format json` returns a JSON envelope whose `result` field holds the final message (parse it before summarizing). `--permission-mode acceptEdits` lets it edit files without per-edit prompts; `--dangerously-skip-permissions` removes all prompts — only with explicit user approval (it triggers a confirmation card through `agent_delegate`, and through `terminal` it shows the command for approval anyway).
- **codex**: `codex exec` runs the task non-interactively with the workspace-write sandbox by default; add `--full-auto` for no-approval mode (bypass flag — user confirmation).
- **opencode**: `opencode run` executes the prompt in the current directory and prints the response; use `--model provider/model` to pick a model.

Always quote the task. For long prompts, write the task to a file first (`vault_write`, then pass it via the agent's file/arg mechanism) to avoid quoting pitfalls.

## Choosing the agent

- Default to whatever the user names. Otherwise: **claude** for nuanced refactors and codebase reasoning, **codex** for precise mechanical edits and fast iteration, **opencode** when the user prefers its provider/model setup or has it configured with a specific model.
- If a delegate fails (command not found, auth error, timeout), report the exact error — don't silently retry with another agent's API keys or flags.

## After the run

- `cd` effects are per-call; each delegate starts fresh in `cwd`. Verify the outcome in the repo (e.g. `git diff`, `git status` via `terminal`) before claiming success.
- Surface a concise summary plus the pointer to the full transcript. Never paste multi-thousand-line output into chat.
