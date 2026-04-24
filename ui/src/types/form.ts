export type FieldKind =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "select"
  | "multiselect"
  | "date";

export interface FieldSchema {
  name: string;
  label?: string;
  kind?: FieldKind;
  required?: boolean;
  default?: unknown;
  choices?: string[];
  placeholder?: string;
  help?: string;
}

export interface FormSchema {
  title?: string;
  description?: string;
  fields: FieldSchema[];
}
