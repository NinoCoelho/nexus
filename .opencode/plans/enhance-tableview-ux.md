# Plan: Replace ScreenRenderer with Enhanced DataTableView

## Problem

ScreenRenderer layouts (master-detail, search-edit) provide a poor UX:
- Side-by-side split is cramped and confusing
- Separate code path from DataTableView means duplicated features (no ref combobox, no inline editing, no CSV, no saved views)
- Screens are buried below raw tables on the dashboard
- Two systems to maintain

## Solution

**Screens become smart shortcuts into DataTableView.** The dashboard screen cards open a table view with a pre-applied saved view (columns, sort, filter). Master-detail is handled by a new **row detail drawer** in DataTableView that shows the full record + related rows with full CRUD.

This eliminates ScreenRenderer entirely and reuses all existing DataTableView power (inline editing, ref comboboxes, CSV, schema editor, saved views, related rows).

---

## Task 1: Add Row Detail Drawer to DataTableView

**New file:** `ui/src/components/DataTableView/RowDetailDrawer.tsx`

A side drawer (or bottom sheet) that opens when the user clicks a row in the grid. Shows:

1. **Record fields** — all fields of the selected row, editable inline or via a mini form
2. **Related rows section** — reuses the existing `RelatedRowsPanel` (already has full CRUD for 1:N groups)
3. **Action bar** — Edit, Delete, Close

The drawer slides in from the right, taking ~50% width on desktop, full width on mobile. The grid remains visible and interactive on the left.

**Changes to `DataTableView/index.tsx`:**
- Add `selectedRowId` state
- When `selectedRowId` is set, render `RowDetailDrawer` alongside the grid
- Pass `path`, `rowId`, `onOpenTable`, and `onClose` to the drawer
- Clicking a row in `DataTableGrid` sets `selectedRowId` (instead of or in addition to inline edit)

**Changes to `DataTableView/DataTableGrid.tsx`:**
- Add click handler on `<tr>` rows to open the detail drawer (single click, not double-click which is inline edit)
- Visually highlight the selected row

**Design approach:**
- The drawer uses the existing `RelatedRowsPanel` component directly — it already fetches related rows, renders mini tables, and provides add/edit/delete for 1:N groups
- Above the related rows, show the record's fields in a clean read/edit layout
- The drawer auto-refreshes when the row changes (via vault events or after CRUD actions)

---

## Task 2: Convert DashboardScreen Cards to Table View Shortcuts

**Changes to `AppDashboardView/index.tsx`:**

Instead of `setSelectedScreen(screen)` → `ScreenRenderer`, screen cards now call `onOpenTable` with the screen's primary table path + a pre-configured view.

Each `DashboardScreen` gains a resolved table path (from `sections[0].source.table`) and optional view config (columns from `display_fields`, search from `search_fields`).

**Screen card click behavior:**
- Extract the table path from `screen.sections[0].source.table`
- Resolve relative path: `./purchases.md` → `restaurant/purchases.md`
- Call `onOpenTable(resolvedPath)` — this opens the table in DataTableView
- The table can have a pre-saved view matching the screen config

**Changes to the dashboard layout:**
- Move **Screens** section above **Tables** — screens are the primary way to interact with the app
- Tables section becomes a collapsible "Advanced: Tables" section (collapsed by default in normal mode)
- Flows section stays where it is

---

## Task 3: Auto-Create Saved Views from Screen Definitions

When a screen has `display_fields` or `search_fields`, auto-create a corresponding saved view in the table's views array.

**Backend change in `agent/src/nexus/vault_dashboard.py`:**
- When `write_dashboard` saves screens, extract any `display_fields` from screen sections
- For each screen, if the target table exists, upsert a saved view named after the screen (e.g. "Purchase Detail") with:
  - `hidden`: all fields NOT in `display_fields`
  - `sort`: from `search_fields` or sensible default

This is optional/enhancement — screens work without it, the table just shows all columns.

---

## Task 4: Enhance Dashboard Layout for App-Centric UX

**Changes to `AppDashboardView/index.tsx` and `AppDashboardView.css`:**

Reorder sections top-to-bottom:
1. **Title + stats** (existing header)
2. **Screens** — the primary interaction surface. Rendered as prominent cards in a grid. Each card shows: icon, screen name, description, table name, row count
3. **Flows** — multi-step workflow buttons (existing)
4. **Quick actions** — operation chips (moved down, less prominent)
5. **Widgets** — analytics widgets (existing)
6. **Tables** — collapsible "Raw Tables" section. Collapsed by default. Shows table cards with Open + Quick Add.
7. **Processes** — linked boards/calendars (existing)
8. **Delete database** — danger zone (existing)

**Visual changes:**
- Screen cards are larger and more prominent (existing `.data-dash-screen-card` styles are good, just move them up)
- Tables section has a disclosure triangle / collapse toggle
- Remove the "Screens" and "Flows" separate sections — merge them into a unified "App" section at the top

---

## Task 5: Remove ScreenRenderer (cleanup)

Once tasks 1-4 are done:
- Delete `ui/src/components/ScreenRenderer/` directory and all layout files
- Remove `ScreenRenderer` import from `AppDashboardView`
- Remove `selectedScreen` state and the `selectedScreen` render block
- Update any remaining references

---

## Task 6: Update Agent Knowledge

- Update `database-design` skill: screens are now shortcuts to enhanced table views. The screen definition stays the same (sections with source.table, display_fields, etc.) but the agent should know the user gets the full table UX with detail drawer
- Update `knowledge.md`: same
- The `dashboard_manage` tool descriptions don't need changes — the screen data shape is the same

---

## What NOT to change

- `ScreenRenderer` data shape / `DashboardScreen` type — stays the same. Screens still exist in `_data.md`
- `FlowStepper` — flows are separate from screens and stay as-is
- `RelatedRowsPanel` — already has full CRUD, reused as-is in the detail drawer
- Backend screen/flow/link storage — no changes needed
- `dashboard_manage` tool — no changes needed

---

## Implementation Order

1. **Task 1** (Row Detail Drawer) — the core UX improvement, can be tested standalone
2. **Task 4** (Dashboard reorder) — quick win, improves navigation immediately
3. **Task 2** (Screen → table shortcut) — connects screens to enhanced tables
4. **Task 3** (Auto views) — polish, can defer
5. **Task 5** (Remove ScreenRenderer) — cleanup
6. **Task 6** (Agent knowledge) — update docs

---

## Key Design Decisions

1. **Drawer, not modal** — the grid stays visible so the user maintains context. Can click another row to switch.
2. **Single-click to open drawer** — double-click stays as inline cell edit. The row click target is the row number area or an explicit expand icon, not the data cells.
3. **Reuse RelatedRowsPanel** — it already does exactly what we need for the detail section (fetches related rows, renders mini tables, provides add/edit/delete).
4. **Screens as metadata, not UI** — the screen definition drives which table to open and what view to apply, but the rendering is 100% DataTableView.
5. **Tables section collapsible** — most users should interact via screens. Raw table access is for power users / schema editing.
