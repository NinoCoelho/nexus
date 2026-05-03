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

These apply for every turn during a data task. Treat them as invariants, not suggestions.

1. **Every "I will / vou / let me / vamos" turn carries a tool call in the same turn.** Prose-only "next I'll do X" turns are forbidden. If you can't pick a tool, you don't have a plan — call `vault_csv action=schema` on the input as a default first move and then decide.

2. **No inert code blocks.** A fenced ```python``` / ```bash``` / ```sql``` block in chat does *not* execute. Show code only when (a) it just executed via a tool call you also emitted this turn (paste the result, not the script), (b) the user explicitly asked to *see* code, or (c) you've labelled it `# manual fallback — run yourself` because a tool error proves execution is impossible. The user has flagged this as the single most disliked behaviour — do not paste pandas/duckdb snippets as if they ran.

3. **No record values without a tool result behind them.** Don't produce dates, IDs, names, amounts, counts, summaries of clinical / financial / legal records, or any specific field value unless a tool call in *this conversation* returned it. If you've only called `vault_list`, the only allowed answer is "I see N files; reading them now" plus the next tool call.

4. **The plan is three lines max.** No nested numbering, no preamble. Format:
   ```
   Plan: (1) inspect <file> with vault_csv schema  (2) <verify step>  (3) <target action>
   ```
   Then immediately make the first tool call. If the plan needs a fourth line, it's actually two tasks — split them and finish the first.

5. **Step 2 starts with another tool call, not prose.** When a tool result comes back, the next assistant turn either calls another tool or returns a final answer. "Now I'll just analyze this..." with no tool call burns a turn.

## Trigger phrases

Any of these in the user's message means this skill is in scope:

- *import / load / ingest / bring in* (CSV, TSV, JSON, parquet, dump, export, etc.)
- *analyze / summarise / count / aggregate / group / rank / top N / bottom N*
- *join / merge / match / correlate / relate / link*
- *clean / dedupe / normalize / fix / transform / convert*
- *script / pipeline / batch / for each*
- mentions of `.csv` / `.tsv` / `.json` / `.parquet` / `.xlsx` / `.sql` / a data-table folder
- "Reconstruct / show / list / find / pull" rows / records / patients / orders / events from data the user owns

## Default first move (when in doubt)

If the user named a CSV path: `vault_csv action=schema path=<...>` — gets columns + row count cheaply, no data into context.
If the user named a vault folder of data-tables: `datatable_manage action=list_tables folder=<...>`.
If the user named a JSON file: `vault_read path=<...>` (small) or `terminal command='wc -c <path> && head -c 2000 <path>'` (large).
If the user gave a free-form ask without a path: one `ask_user` for the source — but only if vault search wouldn't find it. Try `vault_search query=<keywords>` first.

## Anti-patterns (the audit's worst hits)

- **Code theater.** Writing pandas / SQL as text "for clarity" without calling a tool. Forbidden.
- **"You'll need to run this yourself"** when `terminal` is available. The user has explicitly approved tunnel/YOLO sessions where it works. Try the tool first; only suggest manual run if the tool returned a hard error twice.
- **"Please share the file"** for a path the user already gave you or one that lives in their vault. Use `vault_csv` / `vault_read` / `vault_list`.
- **Re-reading the same CSV across turns.** Once `vault_csv schema` has run, cache the column list in your reasoning — don't re-fetch.
- **Generating record-shape output (a clinical table, a list of orders, a summary by patient) after only listing files.** That's hallucination. Read the data first.

## What good looks like

> User: "Reconstruct the clinical history for patient 289624 from the CSVs in `Clinica Junior/289624/`."
>
> Assistant (one turn):
> "Plan: (1) `vault_csv schema` on the three CSVs to confirm shape, (2) `vault_csv query` to filter to the patient and join, (3) format the timeline."
> *(emits `vault_csv action=schema path=Clinica Junior/289624/25-04-2026-patient.csv` in the same turn)*

> User: "Import these 1k rows into the data-table."
>
> Assistant (one turn):
> "Plan: (1) `vault_csv schema` on the source, (2) `datatable_manage list_tables` on the target folder, (3) `datatable_manage import_csv` mapping the columns."
> *(emits the schema call in the same turn — does not paste a Python script)*
