// DataTableView — inline editor for kind: "ref" cells.
//
// cardinality: "one" → async dropdown of target rows by primary key.
// cardinality: "many" → comma-separated text input (paste IDs).

import type { FieldSchema } from "../../types/form";
import { useRefOptions } from "../datatable/refOptions";

interface Props {
  field: FieldSchema;
  hostPath: string;
  value: unknown;
  onChange: (v: unknown) => void;
  onCommit: () => void;
  onCancel: () => void;
}

export default function RefEditor({ field, hostPath, value, onChange, onCommit, onCancel }: Props) {
  const cardinality = field.cardinality ?? "one";
  const { options, error } = useRefOptions(field, hostPath);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") { e.preventDefault(); onCommit(); }
    else if (e.key === "Escape") { e.preventDefault(); onCancel(); }
  };

  if (cardinality === "many") {
    const arr = Array.isArray(value) ? value : value ? [value] : [];
    return (
      <input
        autoFocus
        type="text"
        className="dt-cell-input"
        value={arr.map(String).join(", ")}
        onChange={(e) => onChange(
          e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
        )}
        onBlur={onCommit}
        onKeyDown={onKey}
        placeholder="comma-separated IDs"
      />
    );
  }

  if (options === null) {
    return <input autoFocus className="dt-cell-input" disabled value="loading…" />;
  }
  if (error) {
    return (
      <input
        autoFocus
        type="text"
        className="dt-cell-input"
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit}
        onKeyDown={onKey}
        title={`target load failed: ${error}`}
      />
    );
  }
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
      {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
  );
}
