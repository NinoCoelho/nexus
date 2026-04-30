/**
 * OperationChips — clickable shortcuts on the database dashboard.
 *
 * Each chip represents an operation defined in `_data.md`. Clicking a `chat`
 * chip fires the operation's prompt into the per-database chat bubble (or
 * the main chat as a Phase 2 fallback). `form` chips open an inline add-row
 * modal for the operation's target table.
 *
 * Long-press / right-click could trigger a remove flow later — kept simple
 * for now: the AddOperationModal handles authoring; a small × button on each
 * chip handles removal.
 */

import type { DashboardOperation } from "../../api/dashboard";

interface Props {
  operations: DashboardOperation[];
  onRunOperation: (op: DashboardOperation) => void;
  onAddOperation: () => void;
  onRemoveOperation?: (opId: string) => void;
}

export default function OperationChips({
  operations,
  onRunOperation,
  onAddOperation,
  onRemoveOperation,
}: Props) {
  return (
    <div className="data-dash-chips">
      {operations.map((op) => (
        <div key={op.id} className={`data-dash-chip data-dash-chip--${op.kind}`}>
          <button
            type="button"
            className="data-dash-chip-btn"
            onClick={() => onRunOperation(op)}
            title={op.prompt || op.label}
          >
            <span className="data-dash-chip-kind">{op.kind === "form" ? "📝" : "💬"}</span>
            <span className="data-dash-chip-label">{op.label}</span>
          </button>
          {onRemoveOperation && (
            <button
              type="button"
              className="data-dash-chip-remove"
              onClick={(e) => {
                e.stopPropagation();
                onRemoveOperation(op.id);
              }}
              title="Remove operation"
              aria-label="Remove operation"
            >
              ×
            </button>
          )}
        </div>
      ))}
      <button
        type="button"
        className="data-dash-chip data-dash-chip--add"
        onClick={onAddOperation}
        title="Add a new operation"
      >
        + Operation
      </button>
    </div>
  );
}
