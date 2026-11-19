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


# ── Additional skill documents (served via /bootstrap/skills/{name}.md) ──

_VAULT_SKILL = """\
---
name: nexus-vault
description: Read, search, and write notes in the Nexus vault via the `nexus vault` CLI. Use when the user asks to store or find information, notes, docs, or knowledge in Nexus.
---

# Nexus vault access

The vault is a folder of markdown notes (`~/.nexus/vault/`) with FTS5
full-text search, tags, and backlinks. All commands below work offline
(direct file access) — the daemon does not need to be running.

## Commands

```sh
nexus vault list [subdir]            # list files (add --json? no: plain table)
nexus vault read <path>              # print a file to stdout
nexus vault write <path> [--from f]  # write file (stdin when --from omitted)
nexus vault search "<query>" -n 20   # FTS5 full-text search
nexus vault mv <src> <dst>           # move/rename
nexus vault rm <path> [-r -y]        # delete
nexus vault mkdir <path>             # create directory
nexus vault backlinks <path>         # files linking to a note
nexus vault tags                     # all tags with counts
```

## Rules

1. Prefer **updating existing notes** over creating near-duplicates —
   search first (`nexus vault search`), then read (`nexus vault read`),
   then write.
2. Write plain markdown. Frontmatter tags: `---\\ntags: [a, b]\\n---`.
3. Reference other notes with wiki-links `[[note-name]]` — backlinks and
   the knowledge graph pick them up.
4. For structured tabular data do NOT abuse markdown tables — create a
   Nexus data table instead (see the `nexus-tables` skill) or a kanban
   board (see `nexus-kanban`).

## Special vault formats (they have dedicated CLIs)

| Frontmatter marker | What it is | CLI |
|---|---|---|
| `kanban-plugin: basic` | Kanban board (lanes + cards) | `nexus kanban …` |
| `data-table-plugin: basic` | Typed table with DuckDB SQL | `nexus table …` |
| `workflow-plugin: basic` | Automation workflow (triggers + steps) | `nexus trigger …` (in-chat) |

Don't hand-edit those files' frontmatter via `vault write` — use the
dedicated CLI so the structure stays valid.

## Self-install

Same procedure as `nexus-delegation`: save as
`~/.claude/skills/nexus-vault/SKILL.md` (Claude Code) or append to your
`AGENTS.md` (OpenCode / Codex), then verify with
`nexus vault list | head`.
"""

_KANBAN_SKILL = """\
---
name: nexus-kanban
description: Create, read, and update Nexus kanban boards via the `nexus kanban` CLI. Use when the user asks to track tasks, move cards, or manage boards in Nexus.
---

# Nexus kanban

Boards are vault markdown (`kanban-plugin: basic`) — lanes and cards,
rendered in the Nexus UI and editable from any chat. All commands work
offline against the vault files.

## Commands

```sh
nexus kanban boards [--json]                     # list boards
nexus kanban show <path> [--full] [--json]       # view lanes + cards
nexus kanban create <path> --title "Sprint" --lanes "Todo,Doing,Done"
nexus kanban add <path> <lane> "<card title>" --body "details"
nexus kanban move <path> <card-id-or-title> <lane>
nexus kanban update <path> <card> --title … --body …
nexus kanban lane <path> "<new lane title>"
nexus kanban rm <path> <card> [-y]
```

`<path>` is the board's vault path (e.g. `boards/work.md`). Card
references accept the card id **or** its title (substring ok). Lanes
accept id or title.

## Typical flow (agent as task manager)

```sh
nexus kanban boards --json                      # find the right board
nexus kanban show boards/work.md                # see current state
nexus kanban add boards/work.md doing "Fix login bug" --body "from report #42"
nexus kanban move boards/work.md "Fix login bug" done
```

## Rules

1. `show` before mutating — respect existing lanes and conventions.
2. Card bodies accept full markdown (lists, code fences).
3. For structured tabular work use `nexus table` instead; boards are for
   task/status tracking.

## Self-install

Same procedure as `nexus-delegation` (save as your host's skill / append
to AGENTS.md), then verify with `nexus kanban boards --json`.
"""

_TABLES_SKILL = """\
---
name: nexus-tables
description: Query and manage Nexus structured data tables with DuckDB SQL via the `nexus table` CLI. Use when the user asks to store structured/relational data, run SQL analysis, import CSVs, or build small databases in Nexus.
---

# Nexus data tables (SQL)

Tables are vault markdown (`data-table-plugin: basic`) with a typed
schema; queries run on DuckDB against the table aliased as `t`. Tables
in one folder form a database; fields can reference other tables
(`kind: ref`). No daemon needed for CLI access.

## Commands

```sh
nexus table list [--json]                          # databases + tables
nexus table view data/orders.md [--json]           # schema + row count + 3 samples
nexus table sql data/orders.md "SELECT status, COUNT(*) AS n, SUM(total) AS revenue FROM t GROUP BY status" [--json]
nexus table rows data/orders.md -n 25 [--json]     # browse raw rows (CRUD, not analysis)
nexus table find data/orders.md "acme" [--json]    # substring lookup
nexus table add data/orders.md '{"title":"o1","total":120}' [--json]
nexus table update data/orders.md <row-id> '{"status":"paid"}'
nexus table rm data/orders.md <row-id> -y
nexus table create data/orders.md '{"fields":[{"name":"title","kind":"text"},{"name":"total","kind":"number"}]}'
nexus table import-csv data/orders.md ~/data.csv [--dry-run] [--json]
```

## Analysis workflow

1. `view` first — schema, kinds, relations, row count.
2. `sql` for everything analytical (GROUP BY, JOIN via ref fields,
   aggregates). Results auto-summarize past 30 rows.
3. `rows`/`find` only to confirm a row before `update`/`rm`.
4. `import-csv --dry-run` before a real import.

## Rules

1. `create_table` only after `list` confirms no table with the same path
   exists — prefer evolving an existing table.
2. `rm` is irreversible — `find` the row and show it to the user first
   when the request is ambiguous.
3. Dates are `YYYY-MM-DD`; booleans true/false; numbers unquoted.
4. In `sql`, the table is always `t`: `FROM t`.

## Self-install

Same procedure as `nexus-delegation` (save as your host's skill / append
to AGENTS.md), then verify with `nexus table list --json`.
"""


def _all_skills() -> dict[str, str]:
    return {
        SKILL_NAME: delegation_skill_markdown(),
        "nexus-vault": _VAULT_SKILL,
        "nexus-kanban": _KANBAN_SKILL,
        "nexus-tables": _TABLES_SKILL,
    }
