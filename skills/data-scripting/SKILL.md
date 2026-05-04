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

## The 4-step analysis loop (mandatory for all analytical tasks)

Every analytical task follows this exact sequence. Do not skip steps.

### Step 1: UNDERSTAND — metadata only, no rows

Gather schema, types, distributions, row counts. **No raw data enters context.**

| Goal | Tool call |
|---|---|
| CSV shape | `vault_csv action=schema` → columns, types, row_count |
| CSV column stats | `vault_csv action=describe` → count, nulls, distinct, min/max/mean, top values |
| Data-table structure | `datatable_manage action=view` → schema, types, relations, row_count + 3 sample rows |
| Cross-file joins | `vault_csv action=relationships` → scored column pairs |

**Never** call `list_rows`, `find_rows`, or `query` without `summarize` in step 1. These return raw rows. You don't need raw rows to understand a data model.

### Step 2: PLAN — what to extract and how to validate

Write a 3-line plan stating:
1. What data to extract (which columns, filters, joins)
2. What transformations/aggregations to apply
3. How to validate the results (row counts, sanity checks, cross-totals)

The plan lives in your reasoning text. Keep it short.

### Step 3: EXECUTE — generate code, run locally

**For complex analysis**: use `analyze`.

- `vault_csv action=analyze path=<csv> script="..."`
- `datatable_manage action=analyze path=<table.md> script="..."`

The variable `t` is a pandas DataFrame with all rows. Pre-loaded: `duckdb`, `pandas` (as `pd`), `numpy` (as `np`). Available validation helpers:
- `assert_row_count(min=None, max=None)` — check row count bounds
- `assert_no_nulls(columns=None)` — check for nulls
- `assert_unique(column)` — check for duplicates
- `assert_range(column, min=None, max=None)` — check numeric bounds

Use `print()` to output your report. Output capped at ~4000 chars. 30-second timeout.

**For simple aggregations**: use `query` with GROUP BY. Results >30 rows are auto-summarized.

- `vault_csv action=query path=<csv> sql="SELECT ... GROUP BY ..."`
- `datatable_manage action=query path=<table.md> sql="SELECT ... GROUP BY ..."`

### Step 4: EVALUATE — read the report, present to user

You receive only the `print()` output or query summary. Evaluate:
- Do the numbers make sense? (cross-check with row counts from step 1)
- Did validation pass? (look for PASS/FAIL lines)
- Is the answer complete, or do you need another analysis pass?

If validation failed → adjust the script and re-run (back to step 3).

## Tool selection (read this first, don't guess)

| Job | Tool | Notes |
|---|---|---|
| Inspect a `.csv`/`.tsv` shape | `vault_csv action=schema` | First move on any CSV. Returns metadata only. |
| Per-column stats | `vault_csv action=describe` | count, nulls, distinct, min/max/mean, top values. |
| Sample N rows of a CSV | `vault_csv action=sample` | Default 20 rows. `mode: head\|tail\|random`. |
| **Complex analysis** | `vault_csv action=analyze` | Python script with pandas DataFrame `t`. Prints report. |
| Simple SQL aggregation | `vault_csv action=query` | GROUP BY queries. Auto-summarizes >30 rows. |
| Discover joins / FKs | `vault_csv action=relationships` | Score by name similarity + value overlap. |
| Data-table structure | `datatable_manage action=view` | Schema + row_count + 3 sample rows. **No full dump.** |
| **Complex table analysis** | `datatable_manage action=analyze` | Python script with pandas DataFrame `t`. Prints report. |
| Simple table SQL | `datatable_manage action=query` | Auto-summarizes >30 rows. |
| Paginated row browsing | `datatable_manage action=list_rows` | Default 25, max 200. For CRUD verification, not analysis. |
| Lookup by value | `datatable_manage action=find_rows` | `where` + `q`. Default 25, max 200. For single-row lookups only. |
| Bulk insert | `datatable_manage action=add_rows` / `import_csv` | One-shot when possible. |
| Inspect a JSON file | `vault_read` (small) or `terminal` + `head -c 4096` | For huge JSON: `jq` / `python3 -c`. |
| Search vault by keyword | `vault_search` (FTS) or `vault_semantic_search` | FTS for exact tokens; semantic for fuzzy. |
| Shell one-liner | `terminal` | HITL-gated; YOLO auto-approves. |

**The most common mistake**: calling `vault_read` on a `.csv`. That dumps raw text into context (expensive) and ignores DuckDB. **Don't.** First read on any CSV is `vault_csv action=schema`.

**The second most common mistake**: using `list_rows` or `find_rows` for analysis. These return raw rows to context. Use `query` with GROUP BY or `analyze` with Python instead. The model should never receive raw datasets — only summaries and reports.

### Dispatch by file extension (don't cross the streams)

| Path | Tool |
|---|---|
| `*.csv` / `*.tsv` | `vault_csv` |
| `*.md` with frontmatter `data-table-plugin: basic` | `datatable_manage` |
| `*.md` with frontmatter `kanban-plugin: basic` | `kanban_tool` |
| `*.md` (no plugin tag) | `vault_read` / `vault_write` |

**When `vault_csv` returns `hint.tool: datatable_manage`**, switch tools and continue — don't stop.

## The `analyze` action — writing effective scripts

### Template

```python
# Validate input matches expectations from step 1
assert_row_count(min=1)
print(f"Rows: {len(t)}, Columns: {list(t.columns)}")

# Core analysis
result = t.groupby('category').agg(
    count=('id', 'count'),
    total=('amount', 'sum'),
).reset_index()
print("## Results")
print(result.to_markdown(index=False))

# Cross-validation
print(f"Validation: total rows={len(t)}, grouped sum={result['count'].sum()}")
```

### Rules

1. **Always start with validation** — `assert_row_count` or a manual check against the count from `schema`.
2. **Always print your results** — unprinted computations are lost.
3. **Keep it focused** — one script does one analysis. If you need a second angle, make a second `analyze` call.
4. **Handle messy data** — use `pd.to_numeric(..., errors='coerce')` for numeric columns that might have strings.
5. **Don't import extra libraries** — only `duckdb`, `pandas`, `numpy` are available. If you need something else, use `terminal` instead.

## Failure recovery — concrete rules

### `vault_csv` returns `ModuleNotFoundError`

Almost always `pytz` on a TZ-aware timestamp. Treat as **permanent fail** — pivot once.

- Pivot: `vault_csv action=query` with `CAST(col AS VARCHAR)` to bypass the timezone parser.
- Or: `vault_csv action=analyze` — pandas reads timestamps as strings by default.

Do **not** retry the same `vault_csv` call expecting it to start working.

### `terminal unavailable: handler not wired`

Often transient. Recovery:

1. Retry once with `terminal command='echo ok'`.
2. If that succeeds → re-issue.
3. If it fails again → use `vault_csv` / `datatable_manage` tools instead.

### `analyze` script error

Read the `error` field in the response. Common causes:
- KeyError: column name mismatch (check schema from step 1)
- TypeError: data type mismatch (use `pd.to_numeric` or explicit casts)
- Timeout: script too complex — break into smaller steps

Fix and re-run. Two strikes → switch to `query` with SQL or `terminal`.

### `vault_csv query` SQL error

DuckDB error messages name the column. Read the error, inspect with `action=schema`, retry once with the fix. If it fails again, switch to `analyze`.

### Sub-agent context limit

Caused by sub-agent re-fetching schema. Fix at spawn time:
- Stage inputs **inside the vault**.
- Include the target table schema **verbatim** in the sub-agent prompt.
- Tell the sub-agent exactly one thing: "call `add_rows` once with the JSON below, then return the row count".

## Bulk-import recipe (CSV → data-table)

1. **Inspect the source.** `vault_csv action=schema path=<csv>`.
2. **Inspect the target.** `datatable_manage action=view path=<table.md>`.
3. **Map columns.** Confirm the mapping in one line of prose.
4. **One-shot.** `datatable_manage action=import_csv path=<table.md> source=<csv> mapping={...}`. Done.
5. **Fallback for transforms.** Use `vault_csv action=analyze` to produce the transformed data, then `add_rows`.

## Lookups — never trust a single page

When the user asks "show me X / find Y / does Z exist," use `datatable_manage action=find_rows`:
- `where: {field: value}` — exact match.
- `q: "<substring>"` — case-insensitive substring across text fields + `_id`.

`list_rows` is for **paginated browsing** (CRUD verification), not lookups. If you scan one page and don't find it, you haven't proven it doesn't exist.

**Concrete failure to avoid**: user asked "show me John Doe" against a 145-row table. Agent called `list_rows` (got 25 rows), didn't see John Doe, answered "not found." John Doe was on page 2.

Rules:
1. Use `find_rows q="John Doe"` for any lookup.
2. If `truncated: true`, you **must** iterate or pivot to `find_rows`.
3. Frame zero-match responses: *"I searched all 145 patients by name — no match for 'John Doe'."*

## Multi-table writes — plan, then verify

1. **Enumerate writes** upfront. List target tables and content.
2. **Execute in dependency order** (parents before children).
3. **Verify.** Call `datatable_manage action=view` on every table you wrote to. Check `row_count` matches expectation.
4. **Surface failures.** If any write failed, say so explicitly.

## Anti-patterns

- **Pulling raw rows into context for analysis.** Use `analyze` (Python) or `query` with GROUP BY. The model should receive reports, not datasets.
- **Using `view` to get all rows.** `view` returns schema + 3 sample rows. For analysis, use `analyze` or `query`.
- **Code theater.** Pasting ` ```python ` blocks instead of calling a tool. If you want to run Python, use `analyze`.
- **`vault_read` on a `.csv`** before `vault_csv schema`. Pulls raw rows into context — expensive and unnecessary.
- **Re-reading the same file across turns.** Cache schema in your reasoning.
- **"I don't have access to your files"** — false when the file is in the vault.
- **"Please share the file"** for a path the user already named.
- **Producing record-shaped output from a `vault_list` alone.** That's hallucination. Read the data first.
- **Retrying the same failing tool call 3+ times.** Two strikes — pivot.
- **Spawning sub-agents that re-discover schema.** Include schema in spawn prompt.

## What good looks like

> User: "What's the monthly revenue trend for 2025? Which products drive the most revenue?"
>
> Turn 1: `vault_csv action=schema path=shop/orders.csv` + `vault_csv action=describe path=shop/orders.csv` (parallel).
> Turn 2: Plan + `vault_csv action=analyze path=shop/orders.csv script="..."`
>   (script filters to 2025, groups by month and by product, prints two tables, validates totals).
> Turn 3: Evaluate the printed report, present formatted answer to user.

> User: "Reconstruct patient 289624's clinical history from `Clinica Junior/289624/`."
>
> Turn 1: `vault_csv action=schema` on all three CSVs.
> Turn 2: `vault_csv action=analyze` joining patient/scheduling/records, printing a timeline.
> Turn 3: Final answer from the printed report.

> User: "Import these 1k rows into the bugs table."
>
> Turn 1: `vault_csv action=schema path=imports/bugs.csv` + `datatable_manage action=view path=data/bugs.md` (parallel).
> Turn 2: `datatable_manage action=import_csv path=data/bugs.md source=imports/bugs.csv mapping={...}`. Done.
