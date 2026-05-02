---
name: data-scripting
description: Operational playbook for data-shaped work in Nexus — pick the right tool for CSV/TSV/JSON/data-table tasks, recover from common failures, and run bulk imports without burning turns. Use after `scripting-plan` has gated the request, or any time you're working with .csv / .tsv / .json / .parquet / data-tables / "import this", "analyze that", "transform these rows".
type: procedure
role: data
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# data-scripting

The "what tool, in what order, when something fails" playbook. Pair with `scripting-plan` (which forces the planning gate). This skill assumes you're already past the plan and need to actually do the work.

## Tool selection (read this first, don't guess)

| Job                                           | Tool                                                | Notes |
|-----------------------------------------------|-----------------------------------------------------|-------|
| Inspect a `.csv`/`.tsv` shape                 | `vault_csv action=schema`                           | First move on any CSV. Returns columns + row count, no data into context. |
| Sample N rows of a CSV                        | `vault_csv action=sample`                           | Default 20 rows. `mode: head|tail|random`. |
| Per-column stats / nulls / distinct counts    | `vault_csv action=describe`                         | Pass `columns` to limit. |
| SQL over a CSV (filter / join / aggregate)    | `vault_csv action=query`                            | The CSV is exposed as view `t`. Default columnar shape (~40% fewer tokens). |
| Discover joins / FKs across CSVs              | `vault_csv action=relationships`                    | Score by name similarity + value overlap. |
| Read a data-table (markdown w/ frontmatter)   | `datatable_manage action=view` or `list_rows`       | Never `vault_read` a data-table for analysis. |
| List tables in a database folder              | `datatable_manage action=list_tables`               | "Database" = folder of data-tables. |
| Bulk insert into a data-table                 | `datatable_manage action=add_rows` (or `import_csv`)| `add_rows` takes a JSON array; `import_csv` ingests a vault CSV directly. |
| Inspect a JSON file                           | `vault_read` (small) or `terminal` `head -c 4096`   | For huge JSON: `terminal` + `jq` / `python3 -c`. |
| Search vault by keyword                       | `vault_search` (FTS) or `vault_semantic_search`     | FTS for exact tokens; semantic for fuzzy intent. |
| Run a quick Python / shell one-liner          | `terminal`                                          | HITL-gated; YOLO auto-approves. Use this *instead* of pasting code. |
| Long-running pipeline / fan-out               | `spawn_subagents`                                   | Stage inputs in vault, give each child the schema verbatim, ask for one return value. |

**The most common mistake**: calling `vault_read` on a `.csv`. That dumps raw text into context (expensive) and ignores DuckDB. **Don't.** First read on any CSV is `vault_csv action=schema`.

### Dispatch by file extension (don't cross the streams)

Pick the tool from the path before you do anything else:

| Path                                                          | Tool                          |
|---------------------------------------------------------------|-------------------------------|
| `*.csv` / `*.tsv`                                             | `vault_csv`                   |
| `*.md` with frontmatter `data-table-plugin: basic`            | `datatable_manage`            |
| `*.md` with frontmatter `kanban-plugin: basic`                | `kanban_tool`                 |
| `*.md` (no plugin tag)                                        | `vault_read` / `vault_write`  |

**Concrete failure to avoid** (from a real session): the agent called `vault_csv action=schema` on `clinic/clinical_notes.md` and `clinic/prescriptions.md` — both datatables. `vault_csv` returned a redirect hint pointing at `datatable_manage`, but if you ignore the hint, you'll silently fail to record the data the user asked for. **When `vault_csv` returns `hint.tool: datatable_manage`, switch tools and continue — don't stop.**

## Failure recovery — concrete rules

These are calibrated from real session failures. Follow them; don't improvise.

### `vault_csv` returns `ModuleNotFoundError`

Almost always `pytz` on a TZ-aware timestamp. Treat it as a **permanent fail in this session** — pivot once, don't retry the same call.

- Pivot: run via `terminal` with `python3 -c "import csv, json; ..."` reading the file directly. Stdlib `csv` handles ISO timestamps as strings.
- Or: `vault_csv action=query` with `CAST(col AS VARCHAR)` to bypass the timezone parser.

Do **not** retry the same `vault_csv` call expecting it to start working. Past sessions burned 6 retries on this.

### `terminal unavailable: handler not wired`

Often transient (the handler binds late). Recovery:

1. Retry once with a trivial command: `terminal command='echo ok'`.
2. If that succeeds → re-issue the original command.
3. If it fails again → look for a tool-only path before falling back to "run it yourself":
   - CSV analytics → `vault_csv`
   - Data-table CRUD → `datatable_manage`
   - File read → `vault_read` / `vault_list`
4. Only after both retries fail and no tool-only path exists, tell the user: *"Terminal isn't responding right now. Here's the script to run yourself: …"* — and label the code block `# manual fallback`.

The dispatch screenshot showed an agent giving up on the first error. Don't.

### `vault_csv query` SQL error

DuckDB error messages name the column. Read the error, inspect with `action=schema`, retry once with the fix. If it fails again, switch to `terminal` + `python3` — don't retry blindly.

### Sub-agent context limit

Caused by sub-agent re-fetching schema, listing /tmp, re-counting rows before doing real work. Fix at spawn time:

- Stage inputs **inside the vault** (not `/tmp` — sub-agents can't always reach it).
- Include the target table schema **verbatim** in the sub-agent prompt.
- Tell the sub-agent its job is exactly one thing: "call `add_rows` once with the JSON below, then return the row count".

## Bulk-import recipe (CSV → data-table)

The largest token-sink in past sessions was a bulk import done turn-by-turn. The recipe:

1. **Inspect the source.** `vault_csv action=schema path=<csv>`. Note columns + row count.
2. **Inspect the target.** `datatable_manage action=view path=<table.md>`. Note schema fields + types.
3. **Map columns.** Build a mental column map (source → target). Confirm in one line of prose if non-obvious.
4. **Try the one-shot path first.** If `datatable_manage action=import_csv` exists with the column map, use it. One tool call, done.
5. **Fallback for non-trivial transforms.** When column mapping isn't 1:1 (renames, casts, derived fields, joining a second CSV), do this:
   - `vault_csv action=query` with a SELECT that produces the exact target shape. Save the result to a vault JSON file via `terminal` (`vault_csv ... > vault/imports/staged.json`).
   - Pass that JSON to `datatable_manage action=add_rows` (split into batches of ≤500 if huge — single call otherwise).
6. **Don't fan out unless necessary.** A single `add_rows` call with 1000 rows beats 4 sub-agents each calling `add_rows` with 250. Spawn sub-agents only when the per-row work is non-trivial (LLM-driven enrichment, OCR, web lookup).

## Lookups — never trust a single page

When the user asks "show me X / find Y / does Z exist," **use `datatable_manage action=find_rows`**, not `list_rows`. `find_rows` accepts:

- `where: {field: value}` — exact match on one or more fields.
- `q: "<substring>"` — case-insensitive substring across every text/textarea field plus `_id`. The natural "by name" mode.

`list_rows` is for *iterating the whole table* (paginated, default 100 per page). It is **not** a lookup tool. If you scan one page of `list_rows` and the row isn't there, you have not proven the row doesn't exist — you've proven the row isn't on that page.

**Concrete failure to avoid** (from a real session): user asked "show me John Doe" against a 145-row patients table. Agent called `list_rows` (got 100 rows), didn't see John Doe on page 1, answered "no patient named John Doe in the database." John Doe was on page 2. Two rules:

1. Use `find_rows q="John Doe"` for any name lookup.
2. If you do use `list_rows` for a lookup and the response says `truncated: true`, you **must** either iterate the remaining pages or pivot to `find_rows`. Never claim "not in the database" with `truncated: true` in the same response.

When a lookup genuinely returns zero matches, frame the response with what was searched: *"I searched all 145 patients by name and substring — no match for 'John Doe'."* That's both more honest and gives the user a foothold to correct the query (typo, alternate spelling).

## Multi-table writes — plan, then verify

When the user asks for a single conceptual change that fans out across multiple tables ("add patient John Doe with phone, a clinical note about back pain, and a Naproxen prescription"):

1. **Enumerate the writes upfront** before the first tool call. List the target tables and what goes in each. If you can't name them all, ask once.
2. **Execute in dependency order** (parents before children that ref them) so ref fields can carry the parent `_id`.
3. **Verify at the end.** Before claiming the request is done, call `datatable_manage action=list_rows` (or `view`) on every table you wrote to and confirm the new row is there with the expected fields. Report the row counts back to the user.
4. **If any write failed, say so.** Don't claim a partial success as complete. The user can't see your tool errors — if you abandoned a sub-task because a tool returned an error, surface that explicitly: *"I added the patient row but couldn't write the prescription because …"*.

## Anti-patterns (the audit's findings)

- **Code theater.** Pasting ` ```python ` blocks instead of calling a tool. The single most-disliked behaviour. If you want to run Python, run it via `terminal`.
- **`vault_read` on a `.csv`** before `vault_csv schema`. Pulls raw rows into context — expensive and unnecessary.
- **Re-reading the same file across turns.** Once `vault_csv schema` returned the columns, those columns don't change. Cache them in your reasoning.
- **"I don't have access to your files"** — false when the file is in the vault. You do. Use `vault_csv` / `vault_read`.
- **"Please share the file"** for a path the user already named.
- **Producing record-shaped output (clinical timeline, order list, patient summary) from a `vault_list` alone.** That's hallucination. Read the data first.
- **Retrying the same failing tool call 3+ times.** Two strikes — pivot.
- **Spawning sub-agents that re-discover schema.** Always include the schema in the spawn prompt.

## What good looks like

> User: "Reconstruct patient 289624's clinical history from `Clinica Junior/289624/`."
>
> Turn 1: Plan + `vault_csv schema` on `25-04-2026-patient.csv`.
> Turn 2: `vault_csv schema` on the other two CSVs (parallel if the runner supports it).
> Turn 3: `vault_csv query` joining patient/scheduling/records on `id_patient`/`id_event_schedule`, ordered by `start_date`.
> Turn 4: Final answer — a markdown timeline. **All field values come from the query result, none from memory.**

> User: "Import these 1k rows into the bugs table."
>
> Turn 1: Plan + `vault_csv schema path=imports/bugs.csv` and `datatable_manage view path=data/bugs.md` (parallel).
> Turn 2: `datatable_manage action=import_csv path=data/bugs.md source=imports/bugs.csv mapping={...}`. Done.
