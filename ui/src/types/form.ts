export type FieldKind =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "select"
  | "multiselect"
  | "date"
  | "vault-link"
  | "formula";

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
}

export interface FormSchema {
  title?: string;
  description?: string;
  fields: FieldSchema[];
}
