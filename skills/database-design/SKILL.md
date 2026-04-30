---
name: database-design
description: Translate a user's described business need into vault data-tables and typed relations. Use when the user wants to "track X", "model Y", "build a database for Z", or asks for help designing tables, schemas, or relationships between data tables.
type: procedure
role: planning
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# database-design

Use when the user describes data they want to capture and you need to turn it into vault data-tables. Skip when they already have tables and just want CRUD work — in that case call `datatable_manage` directly.

Vault data-tables are markdown files with `data-table-plugin: basic` frontmatter. Tables in the same folder are treated as one **database**. Fields can declare typed relations to other tables via `kind: ref` + `target_table` + `cardinality`. The UI surfaces related rows automatically and renders an ER diagram of the database.

## Procedure

1. **Listen.** Restate the user's goal in one sentence. Identify the **entities** they care about (the nouns: "customers", "invoices", "tasks"). Ask one clarifying question only if a major entity is ambiguous — never more than one.

2. **Propose.** Sketch the schema *without writing files yet*. For each entity:
   - One table per entity, with a sensible primary-key field (`id`, `email`, `sku` — pick what feels human).
   - List columns with their `kind` (text, number, date, boolean, select, ref).
   - Identify relationships:
     - One-to-many (e.g. customer → orders): a `kind: ref` field on the *many* side, `cardinality: one`, pointing at the *one* side.
     - Many-to-many (e.g. orders ↔ products): a junction table with two `kind: ref` fields, no other content fields. The UI auto-detects junctions and shows the *other* side's rows directly when drilling down.
   - Pick a folder name (`shop/`, `crm/`, `pm/` — keep it short and lowercase).

3. **Confirm.** Show the user the proposed table list with relations summarised in plain English ("each order belongs to one customer; each order can contain many products via order_items"). Wait for their go-ahead. Iterate if they push back; do not skip ahead.

4. **Build.** Once approved, call `datatable_manage` with these actions, in order:
   - `create_table` for each entity table (omit ref fields here — add them in the next pass so the targets exist first).
   - `create_relation` for each one-to-many edge (`from_table` = many side, `target_table` = one side, `cardinality: one`).
   - `create_junction` for each many-to-many edge (creates the junction file with two pre-wired refs).
   - `add_field` for any extra columns added after the initial scaffold.

5. **Show.** Call `datatable_manage` with `action: er_diagram` for the folder and render the returned mermaid string in your reply (inside a ```mermaid code fence). Confirm the picture matches what the user described.

6. **Seed.** Offer to add 1–3 example rows per table so the database isn't empty when the user opens it. Use `add_row`. Stop after seeding once the user has a feel for it.

7. **Suggest operations.** After tables exist and the user has confirmed the model, ask: *"Want me to set up a few quick actions for this dashboard?"* If yes, propose 3–5 operations from the schema and persist them via `dashboard_manage.set_operations` (or one `add_operation` per item). Operations are chips on the database dashboard; they fall into two kinds:

   - **`kind: "chat"`** — runs a prompt in the floating chat bubble (the database's persistent assistant). Good for multi-step workflows ("Place an order", "Find customer by email").
   - **`kind: "form"`** — opens a pre-filled add-row modal for a target table. Good for fast capture ("Add customer", "Quick add bug").

   Tag each op with `id` (slug), `label` (short button text), `kind`, `prompt` (for chat kind) or `table` + optional `prefill` (for form kind). Examples:

   ```json
   {
     "id": "op_add_customer",
     "label": "Add customer",
     "kind": "form",
     "table": "shop/customers.md"
   }
   {
     "id": "op_place_order",
     "label": "Place order",
     "kind": "chat",
     "prompt": "Help me place a new order: which customer is buying, and which products?"
   }
   ```

   Don't pre-build operations the user didn't ask about. Three to five well-chosen ones is the target — the user can add more later via the dashboard's "+ Operation" button.

## Conventions

- **Sibling refs use `./name.md`**, cross-folder refs use `../folder/name.md`. Never use bare filenames — those are interpreted as vault-absolute paths.
- **Primary key**: declare it via `schema.table.primary_key` only when the user has a meaningful natural key (`id`, `email`, `sku`). Otherwise let the auto-`_id` (8-char hex) do the work.
- **Junction tables**: keep them pure (just two refs). If the user wants quantity, price, or notes on the relationship, set `schema.table.is_junction: false` so the UI keeps the junction visible as its own entity.
- **Don't pre-build schemas the user didn't ask for.** A "track invoices" prompt yields *invoices*, not invoices+contacts+payments+ledger. Ship the smallest model that matches the request and let the user grow it.
- When the user describes a one-shot list ("a list of books I want to read"), one table is the right answer. Don't invent relations.

## Examples

> User: "I want to track customers and their orders, and each order can have multiple products."

→ Three tables in `shop/`: `customers.md`, `orders.md`, `order_items.md`. `orders.md` has `customer_id` ref → customers (one). `order_items.md` is a junction with `order_id` and `sku` (refs to orders + products). You'd also create `products.md` for the SKU lookup. ER diagram confirms the M:N collapses cleanly.

> User: "Just a list of bugs I'm finding in this codebase."

→ One table, `bugs.md`, no relations. Don't over-engineer.

> User: "Help me track tasks and which project each belongs to."

→ Two tables: `projects.md` and `tasks.md`. `tasks.md` has `project` ref → projects (one). No junction needed.
