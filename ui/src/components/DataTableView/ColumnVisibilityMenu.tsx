// DataTableView — popover to show/hide individual columns.

import { useState } from "react";
import type { FieldSchema } from "../../types/form";

interface Props {
  fields: FieldSchema[];
  hidden: Set<string>;
  onToggle: (name: string) => void;
}

export default function ColumnVisibilityMenu({ fields, hidden, onToggle }: Props) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dt-cols-menu">
      <button className="vault-pill" onClick={() => setOpen((o) => !o)} title="Show/hide columns">
        Columns ({fields.length - hidden.size}/{fields.length})
      </button>
      {open && (
        <div className="dt-cols-popover" onMouseLeave={() => setOpen(false)}>
          {fields.map((f) => (
            <label key={f.name} className="dt-cols-item">
              <input
                type="checkbox"
                checked={!hidden.has(f.name)}
                onChange={() => onToggle(f.name)}
              />
              <span>{f.label ?? f.name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
