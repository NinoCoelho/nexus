// DataTableView — inline cell editor rendered in place of the cell value.

import type { FieldSchema } from "../../types/form";
import RefEditor from "./RefEditor";

interface Props {
  field: FieldSchema;
  value: unknown;
  hostPath?: string;
  onChange: (v: unknown) => void;
  onCommit: () => void;
  onCancel: () => void;
}

export default function InlineEditor({ field, value, hostPath, onChange, onCommit, onCancel }: Props) {
  const kind = field.kind ?? "text";
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); onCommit(); }
    else if (e.key === "Escape") { e.preventDefault(); onCancel(); }
  };
  if (kind === "ref") {
    return (
      <RefEditor
        field={field}
        hostPath={hostPath ?? ""}
        value={value}
        onChange={onChange}
        onCommit={onCommit}
        onCancel={onCancel}
      />
    );
  }
  if (kind === "boolean") {
    return (
      <input
        autoFocus
        type="checkbox"
        checked={!!value}
        onChange={(e) => onChange(e.target.checked)}
        onBlur={onCommit}
        onKeyDown={onKey}
      />
    );
  }
  if (kind === "select" && field.choices) {
    return (
      <select
        autoFocus
        className="dt-cell-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        onKeyDown={onKey}
      >
        <option value="">—</option>
        {field.choices.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  }
  if (kind === "number") {
    return (
      <input
        autoFocus
        type="number"
        className="dt-cell-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value === "" ? "" : parseFloat(e.target.value))}
        onBlur={onCommit}
        onKeyDown={onKey}
      />
    );
  }
  if (kind === "date") {
    return (
      <input
        autoFocus
        type="date"
        className="dt-cell-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        onKeyDown={onKey}
      />
    );
  }
  // text + vault-link
  return (
    <input
      autoFocus
      type="text"
      className="dt-cell-input"
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onCommit}
      onKeyDown={onKey}
    />
  );
}
