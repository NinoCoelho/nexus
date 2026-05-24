import type { FieldSchema } from "../../types/form";
import RefCombobox from "../Combobox";

interface Props {
  field: FieldSchema;
  hostPath: string;
  value: unknown;
  onChange: (v: unknown) => void;
  onCommit: () => void;
  onCancel: () => void;
}

export default function RefEditor({ field, hostPath, value, onChange, onCommit, onCancel }: Props) {
  return (
    <RefCombobox
      field={field}
      hostPath={hostPath}
      value={value}
      onChange={onChange}
      className="dt-cell-input"
      autoFocus
      onBlur={onCommit}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.preventDefault(); onCancel(); }
      }}
    />
  );
}
