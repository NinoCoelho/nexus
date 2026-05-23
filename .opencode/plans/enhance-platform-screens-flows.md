# Platform Enhancement: Screens, Flows, and Agent Knowledge

## Problem
The agent creates tables + operations + widgets but never creates screens, flows, or links.
Result: flat form-only UX. No master-detail views, no multi-step workflows (e.g. "purchase + items").

## Solution
All changes are platform-level. When the agent next designs any database (CRM, inventory, clinic, ERP),
it will automatically create screens and flows because:
1. The skill tells it to
2. The tool descriptions guide the structure
3. The UI components support the patterns

---

## Task 1: Enhance MasterDetailLayout — CRUD in detail section

**File:** `ui/src/components/ScreenRenderer/layouts/MasterDetailLayout.tsx`

Changes:
- Filter detail rows by matching `section.relation.field` against the selected master row's PK value
  - `relation.field` = the FK column name on the child table (e.g. `"purchase"`)
  - Master PK = `_id` or `schema.table.primary_key` on the master section
- Add "+ Add Item" button in the detail section header
  - Opens an inline form rendered from the detail section's schema fields (excluding the FK field)
  - Auto-populates the FK field with the selected master row's PK value
  - Calls `crud.addRow(1, row)` to insert into the detail table
- Add delete button (×) on each detail row → `crud.deleteRow(1, rowId)`
- Helper functions:
  - `getDetailRelationField(detail)` — reads `detail.relation.field` or `detail.source.relation_field`
  - `getMasterPkField(master)` — reads schema for primary_key, falls back to `_id`
  - `getMasterPkValue(row, pkField)` — extracts the value

## Task 2: Enhance FlowStepper — repeatable-form step type

**File:** `ui/src/components/FlowStepper/index.tsx`

Changes:
- New step type: `"repeatable-form"`
  - Renders the table schema fields as a form row
  - "Add another" button appends another blank row
  - Each row has a remove button
  - Shows running count: "3 items added"
  - On "Next" or "Finish", bulk-inserts all rows via `bulkAddVaultDataTableRows`
- New config on step: `"parent_ref": { "step": 0, "field": "purchase" }`
  - After step 0 creates a row and returns its ID, auto-populate `field` on every repeatable row with that ID
  - Implementation: store created IDs per step in state, resolve parent_ref before bulk insert
- Step type rendering dispatch:
  ```
  step.type === "repeatable-form" → RepeatableFormSection
  ```

New component within FlowStepper:
- `RepeatableFormSection` — manages an array of `Record<string, unknown>[]`
- Each item renders the same schema fields
- Footer: "Add another [entity name]" + count badge

## Task 3: Update database-design skill

**File:** `~/.nexus/skills/database-design/SKILL.md`

Add after step 7 (operations):

```
8. **Design screens.** Ask: "Want me to set up custom views for browsing and editing your data?"
   If yes, create screens based on the schema patterns:

   - For tables with frequent manual edits (product catalog, contacts, tasks):
     create a `search-and-edit` screen. One section with `source.table` and `display_fields`.

   - For parent-child relations (orders → items, customers → orders, suppliers → purchases):
     create a `master-detail` screen with two sections:
     - Section 0: parent table with `display_fields` and `search_fields`
     - Section 1: child table with `display_fields` and `relation: { field: "<fk_column>" }`
     The relation.field is the FK column on the child table that points to the parent.

   - For read-only overviews (stock summary, monthly stats):
     create a `dashboard` screen. Each section is a table with `display_fields`.

   Example — purchase detail screen:
   ```json
   {
     "id": "purchase-detail",
     "name": "Purchase Detail",
     "layout": "master-detail",
     "sections": [
       {
         "id": "purchases",
         "source": { "table": "./purchases.md" },
         "display_fields": ["date", "supplier", "total", "status", "payment_method"],
         "search_fields": ["supplier", "invoice_number"]
       },
       {
         "id": "items",
         "source": { "table": "./purchase_items.md" },
         "display_fields": ["product", "quantity", "unit_price", "subtotal"],
         "relation": { "field": "purchase" }
       }
     ]
   }
   ```

9. **Design flows.** For any "create parent + N children" workflow, create a flow:
   - Step 1: `type: "form"` — creates the parent record (e.g. purchase header)
   - Step 2: `type: "repeatable-form"` — adds N child rows, auto-linked via `parent_ref`
   - Step 3: `type: "confirm"` — review and finish

   Example — "Record Purchase" flow:
   ```json
   {
     "id": "record-purchase",
     "name": "Record Purchase",
     "steps": [
       { "type": "form", "table": "./purchases.md", "fields": ["date", "supplier", "payment_method"] },
       { "type": "repeatable-form", "table": "./purchase_items.md", "parent_ref": { "step": 0, "field": "purchase" } },
       { "type": "confirm", "message": "Confirm this purchase?" }
     ]
   }
   ```

10. **Link boards.** If the workflow benefits from a kanban board (e.g. order status pipeline),
    create a vault file with `kanban-plugin: basic` frontmatter and link it via
    `dashboard_manage action=add_link link_kind=boards link_path=./orders-board.md`.
```

## Task 4: Update dashboard_manage tool descriptions

**File:** `agent/src/nexus/tools/dashboard_tool.py`

Enhance the `screen` parameter description to include:
- Section structure with `relation` for master-detail child sections
- Example of a master-detail screen config

Enhance the `flow` parameter description to include:
- `repeatable-form` step type with `parent_ref` config
- Example of a parent + children flow

## Task 5: Update knowledge.md

**File:** `~/.nexus/skills/nexus/knowledge.md`

Replace the "create a business app" section with expanded guidance:
- Screen layout patterns and when to use each
- Flow patterns including repeatable-form
- The relation field on sections
- The parent_ref config on flow steps

## Task 6: Build + lint + test

- `npm run build` in `ui/`
- `uv run ruff check` in `agent/`
- Run existing tests: `uv run pytest tests/test_vault_dashboard.py -k "screen or flow or link"`
