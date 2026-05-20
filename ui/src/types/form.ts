export type FieldKind =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "select"
  | "multiselect"
  | "date"
  | "vault-link"
  | "formula"
  | "rollup"
  | "ref";

export type RollupAggregate = "sum" | "count" | "avg" | "min" | "max";

export type FieldCardinality = "one" | "many";

export interface FieldSchema {
  name: string;
  label?: string;
  kind?: FieldKind;
  required?: boolean;
  default?: unknown;
  choices?: string[];
  placeholder?: string;
  help?: string;
  /** For kind="formula": expression evaluated against other row fields (e.g. "price * qty"). */
  formula?: string;
  /** For kind="ref": vault-relative path to another data-table file. */
  target_table?: string;
  /** For kind="ref": "one" (FK to a single row) or "many" (multi-select / N:N). */
  cardinality?: FieldCardinality;
  /** For kind="rollup": vault-relative path to the detail (target) table. */
  rollup_target_table?: string;
  /** For kind="rollup": FK field on the detail table that points back to this table. */
  rollup_relation_field?: string;
  /** For kind="rollup": aggregation function to apply. */
  rollup_aggregate?: RollupAggregate;
  /** For kind="rollup": field on the detail table to aggregate (not needed for count). */
  rollup_source_field?: string;
  /** For kind="rollup": optional formula evaluated against each detail row; only truthy rows are aggregated. */
  rollup_filter?: string;
  /** Optional URL rendered next to `help` (e.g. "Get your token here →"). */
  help_url?: string;
  /** When true, render as a masked password input; the value is redacted from the chat transcript. */
  secret?: boolean;
}

export interface FormSchema {
  title?: string;
  description?: string;
  fields: FieldSchema[];
}
