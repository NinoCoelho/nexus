"""Vault-native data table — markdown file type parallel to kanban.

Format
------
A data-table file has YAML frontmatter with ``data-table-plugin: basic`` and
a markdown body with two fenced YAML blocks:

    ---
    data-table-plugin: basic
    ---

    ## Schema
    ```yaml
    title: Bug triage
    fields:
      - { name: id, kind: text, required: true }
      - { name: severity, kind: select, choices: [low, med, high] }
    ```

    ## Rows
    ```yaml
    - { id: BUG-1, severity: high }
    ```

Human-readable, diff-friendly, survives hand edits.
"""

from __future__ import annotations

from ._vault_datatable_core import (
    DATATABLE_PLUGIN_KEY,
    _FENCE_RE,
    _ROWS_SECTION,
    _SCHEMA_SECTION,
    _VIEWS_SECTION,
    _extract_frontmatter,
    _extract_section_yaml,
    _load_state,
    _ref_fields,
    _rollup_fields,
    _serialize,
    is_datatable_file,
    read_table,
)
from .vault_datatable_materialize import (
    _aggregate,
    _collect_unmatched_sample,
    _expected_ref_ids,
    _formula_fields,
    _norm_ref_value,
    _pk_name,
    _row_matches_ref,
    _to_num_materialize,
    _truthy_materialize,
    _validate_refs,
    create_junction,
    create_relation,
    find_inbound_refs,
    is_junction,
    materialize,
    related_rows,
    resolve_ref,
    validate_refs,
)
from .vault_datatable_rows import (
    add_row,
    add_rows,
    add_rows_with_report,
    delete_row,
    find_rows,
    update_row,
)
from .vault_datatable_schema import (
    add_field,
    create_table,
    remove_field,
    rename_field,
    set_schema,
    set_views,
    update_field,
    validate_schema,
)

__all__ = [
    "DATATABLE_PLUGIN_KEY",
    "is_datatable_file",
    "read_table",
    "create_table",
    "set_schema",
    "set_views",
    "add_field",
    "remove_field",
    "rename_field",
    "update_field",
    "validate_schema",
    "add_row",
    "add_rows",
    "add_rows_with_report",
    "update_row",
    "delete_row",
    "find_rows",
    "resolve_ref",
    "is_junction",
    "materialize",
    "validate_refs",
    "create_relation",
    "create_junction",
    "find_inbound_refs",
    "related_rows",
    "_serialize",
    "_load_state",
    "_ref_fields",
    "_rollup_fields",
    "_extract_frontmatter",
    "_extract_section_yaml",
    "_FENCE_RE",
    "_SCHEMA_SECTION",
    "_ROWS_SECTION",
    "_VIEWS_SECTION",
    "_formula_fields",
    "_pk_name",
    "_aggregate",
    "_to_num_materialize",
    "_truthy_materialize",
    "_validate_refs",
    "_norm_ref_value",
    "_row_matches_ref",
    "_expected_ref_ids",
    "_collect_unmatched_sample",
]
