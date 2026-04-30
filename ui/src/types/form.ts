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
  | "ref";

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
