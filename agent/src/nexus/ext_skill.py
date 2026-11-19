"""Delegation skill generator — the copy-paste block that turns an external
coding agent (Claude Code / OpenCode / Codex) into a Nexus client.

The generated markdown is self-configuring: the receiving agent is
instructed to save it as its own skill (Agent Skills frontmatter for
Claude Code, an AGENTS.md section for OpenCode/Codex) and confirm. From
then on the external agent can delegate tasks to Nexus bots via the
``nexus run`` / ``nexus bots`` headless CLI.

Rendered by ``GET /ext/delegate`` (copy button in the Nexus UI) and
printed by ``nexus skill print`` (copy from the terminal).
"""

from __future__ import annotations

SKILL_NAME = "nexus-delegation"

_TEMPLATE = """\
---
name: {skill_name}
description: Delegate tasks to Nexus bots via the `nexus` CLI (nexus run, nexus bots). Use when the user asks to hand work to a Nexus bot, or says "run this on Nexus", "ask @handle", or references Nexus bots/chats.
---

# Nexus delegation

You are connected to a **Nexus** instance — a local platform of AI bots
(every bot is a real agent with its own persona, memory, and skills).
You delegate work to those bots through the `nexus` CLI over loopback.
You orchestrate; the Nexus bot executes.

## Setup (one time, on first use)

If the `nexus` command is missing or unreachable, STOP and tell the user:
"Install the Nexus CLI first (repo: NinoCoelho/nexus, `uv sync` in
agent/), and make sure the daemon is running (`nexus daemon start`)."
Do not improvise an alternative transport.

**Prefer the live bootstrap when reachable**: `curl
http://127.0.0.1:18989/bootstrap` returns the current setup doc (real
bot roster, latest skills). If it answers, follow it instead of this
embedded copy — it may be fresher.

## Discovery

```sh
nexus bots list --json        # every bot: id, @handle, name, persona preview
```

Pick the bot whose persona matches the task. When two could fit, say so
and let the user pick (or address the more specific one directly).

## Delegating a task

```sh
nexus run <bot-handle> "<task description>" --json
```

- Resolves `<bot-handle>` by handle (`@swe`) or id. `--json` prints one
  document: `{{"session_id", "reply", "iterations", "bot"}}`.
- Continues that bot's **most recent chat** — the bot remembers prior
  context across invocations. Pass `--new` to start a fresh chat.
- `--timeout <seconds>` caps the wait (default 600). Long builds: run the
  command with your shell's background mechanism and poll, or raise the
  timeout.
- `--model <id>` overrides the bot's model for that turn only.

**Exit codes**: `0` ok · `1` turn failed (HTTP/budget/daemon) · `2` bot
not found. On `1`, surface the stderr detail to the user — do not retry
blindly more than once.

## Rules

1. **You are the orchestrator.** Compose the task prompt well: goal,
   constraints, expected output format. The bot cannot see your screen or
   your conversation — include everything it needs.
2. **One task per call.** Split complex work into a sequence of `nexus
   run` calls (possibly on different bots), checking each `reply` before
   proceeding.
3. **No secrets in task text.** Credentials go through Nexus's own
   credential surfaces, not through your shell commands.
4. **Don't fabricate bot handles.** Always `nexus bots list --json`
   first; if the right specialist doesn't exist, tell the user instead of
   guessing.
5. **Relay results honestly.** Quote the bot's `reply` when reporting;
   don't paraphrase away caveats it raised.

## Quick reference

| Command | Purpose |
|---|---|
| `nexus bots list --json` | discover bots |
| `nexus run @h "task" --json` | delegate + get final reply |
| `nexus run @h "task" --new` | fresh chat, no history |
| `nexus run @h "task" --timeout 900` | longer wait |
| `nexus run --help` | all flags |
"""

_INSTALL_STEPS = """\

## Self-install (do this NOW, before any delegation)

You are an autonomous agent. Persist this skill for future sessions so
the user doesn't have to paste it again — then confirm what you did:

- **Claude Code** — create `~/.claude/skills/{skill_name}/SKILL.md` with
  exactly this document (frontmatter included). Skills are picked up on
  the next session start.
- **OpenCode** — append this document (minus the YAML frontmatter) to
  `~/.opencode/AGENTS.md` under a `## Nexus delegation` heading, or save
  the whole file as `~/.opencode/skill/{skill_name}/SKILL.md` if your
  version supports agent skills.
- **Codex CLI** — append this document (minus the YAML frontmatter) to
  `~/.codex/AGENTS.md` under a `## Nexus delegation` heading.

Verify the CLI works before claiming success:

```sh
nexus bots list --json
```

If that fails, report the setup steps above to the user — do not mark the
skill as installed.
"""


def delegation_skill_markdown(port: int | None = None) -> str:
    """Render the full self-configuring skill block.

    ``port`` is informational only — the CLI reads NEXUS_PORT/default
    itself; including it helps the user spot a non-default setup.
    """
    text = _TEMPLATE.format(skill_name=SKILL_NAME)
    if port is not None and port != 18989:
        text += f"\n\n<!-- Nexus server port: {port} (default 18989; override with NEXUS_PORT) -->\n"
    return text + _INSTALL_STEPS.format(skill_name=SKILL_NAME)


def paste_prompt() -> str:
    """The one-liner the user types ABOVE the pasted block in the agent."""
    return (
        "Cole o bloco abaixo no seu agente e ele se auto-instala:\n"
        "Instale a skill a seguir e confirme o que fez."
    )
