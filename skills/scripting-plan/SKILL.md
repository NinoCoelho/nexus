---
name: scripting-plan
description: Use before any data/scripting task — CSV/TSV/JSON/SQL/parquet, "import X", "analyze Y", "count rows", "join these tables", "transform this file". Forces a 3-line plan plus a tool call in the same turn so the agent doesn't burn turns on prose, and bans inert code blocks.
type: procedure
role: planning
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# scripting-plan

Use **before** any task that touches structured data (CSV/TSV/JSON/SQL/parquet/data-tables) or asks for analysis, transformation, counting, joining, importing, deduplicating, or scripting. The most common past failure is *prose pretending to be action* — the model says "I will do X, Y, Z" and stops, or pastes a `python` snippet that never runs. This skill prevents that.

For the "what tool actually does the work" decisions and recovery recipes, follow up with `data-scripting`. This skill is the planning gate; that one is the operational playbook.

## The hard rules

1. **Every "I will / vou / let me / vamos" turn carries a tool call in the same turn.** Prose-only turns are forbidden. If you can't pick a tool, call `vault_csv action=schema` as a default first move.

2. **No inert code blocks.** Fenced ```python``` / ```bash``` / ```sql``` blocks in chat do *not* execute. Show code only when (a) it just executed via a tool call you also emitted this turn, (b) the user explicitly asked to *see* code, or (c) you've labelled it `# manual fallback` because a tool error proves execution is impossible.

3. **No record values without a tool result behind them.** Don't produce dates, IDs, names, amounts, counts, or summaries unless a tool call in *this conversation* returned it.

4. **The plan is three lines max.** Format:
   ```
   Plan: (1) inspect <file> with schema  (2) <verify step>  (3) <target action>
   ```
   Then immediately make the first tool call.

5. **Step 2 starts with another tool call, not prose.**

6. **For analytical tasks, follow the 4-step loop:**
   - Step 1: `schema` + `describe` → understand data model (metadata only, no rows)
   - Step 2: Write plan with extraction targets and validation criteria
   - Step 3: `analyze` with Python script that extracts, validates, and prints report
   - Step 4: Evaluate printed report, present to user

## Trigger phrases

Any of these means this skill is in scope:

- *import / load / ingest / bring in* (CSV, TSV, JSON, parquet, dump, export)
- *analyze / summarise / count / aggregate / group / rank / top N / bottom N*
- *join / merge / match / correlate / relate / link*
- *clean / dedupe / normalize / fix / transform / convert*
- *script / pipeline / batch / for each*
- mentions of `.csv` / `.tsv` / `.json` / `.parquet` / `.xlsx` / `.sql` / a data-table folder
- "Reconstruct / show / list / find / pull" rows / records from data the user owns

## Default first move (when in doubt)

| Situation | First tool call |
|---|---|
| User named a CSV path | `vault_csv action=schema path=<...>` |
| User named a data-table | `datatable_manage action=view path=<...>` |
| User named a vault folder | `datatable_manage action=list_databases folder=<...>` |
| User named a JSON file | `vault_read path=<...>` (small) or `terminal command='wc -c <path> && head -c 2000 <path>'` (large) |
| Free-form ask without a path | `vault_search query=<keywords>` first, then `ask_user` if needed |

## Analysis planning template

When the user asks for analysis, your plan must include **extraction** and **validation**:

```
Plan: (1) schema + describe on <files> to understand shape
      (2) analyze with script that extracts <what>, validates <how>
      (3) evaluate report, present results
```

### Validation criteria to include in your plan

- **Row count**: "expect N rows after filtering" (compare with schema row_count)
- **Cross-totals**: "sum of groups should equal grand total"
- **Null checks**: "critical columns X, Y should have no nulls"
- **Range checks**: "numeric column should be within [min, max]"

These get baked into your `analyze` script as `assert_*` calls.

## Anti-patterns

- **Pulling raw data into context.** Never use `list_rows`, `find_rows`, or `query` (without summarize) to retrieve datasets for in-context reasoning. Use `analyze` — the model generates code, server executes, only the report comes back.
- **Code theater.** Writing pandas / SQL as text "for clarity" without calling a tool. Forbidden.
- **"You'll need to run this yourself"** when `analyze` or `terminal` is available. Try the tool first.
- **"Please share the file"** for a path the user already gave or that lives in the vault.
- **Re-reading the same schema across turns.** Cache in your reasoning after the first call.
- **Generating record-shaped output after only listing files.** That's hallucination. Read the data first.

## What good looks like

> User: "What's the monthly revenue trend for 2025?"
>
> Assistant (one turn):
> "Plan: (1) `vault_csv schema` + `describe` on orders.csv  (2) `analyze` to filter 2025, group by month, validate totals  (3) present trend"
> *(emits `vault_csv action=schema path=shop/orders.csv` + `vault_csv action=describe path=shop/orders.csv` in the same turn)*

> User: "Reconstruct the clinical history for patient 289624."
>
> Assistant (one turn):
> "Plan: (1) `schema` on the three CSVs  (2) `analyze` to join and filter by patient, print timeline  (3) format and present"
> *(emits `vault_csv action=schema path=Clinica Junior/289624/25-04-2026-patient.csv` in the same turn)*

> User: "Import these 1k rows into the data-table."
>
> Assistant (one turn):
> "Plan: (1) `schema` on source, `view` on target  (2) `import_csv` with mapping  (3) verify row count"
> *(emits the schema + view calls in the same turn)*
