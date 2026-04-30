/**
 * OperationChips — clickable shortcuts on the database dashboard.
 *
 * Each chip represents an operation defined in `_data.md`. Clicking a `chat`
 * chip kicks an ephemeral hidden agent session (it never appears in the chat
 * sidebar); the chip itself surfaces the run status — spinner while running,
 * a brief check on success, a persistent warning on failure that the user
 * can click to open the run in `CardActivityModal`. `form` chips open an
 * inline add-row modal for the operation's target table.
 */

import type { ReactNode } from "react";
import type { DashboardOperation } from "../../api/dashboard";

export type OpRunStatus = "running" | "done" | "failed";

export interface OpRunState {
  sessionId: string;
  status: OpRunStatus;
  error?: string;
}

interface Props {
  operations: DashboardOperation[];
  /** Per-op last-run state for inline status icons. */
  runState?: Record<string, OpRunState>;
  onRunOperation: (op: DashboardOperation) => void;
  /** Click handler for the inline status icon — opens the run preview. */
  onOpenRun?: (op: DashboardOperation) => void;
  onAddOperation: () => void;
  onRemoveOperation?: (opId: string) => void;
}

export default function OperationChips({
  operations,
  runState,
  onRunOperation,
  onOpenRun,
  onAddOperation,
  onRemoveOperation,
}: Props) {
  return (
    <div className="data-dash-chips">
      {operations.map((op) => {
        const state = runState?.[op.id];
        const indicator = state ? renderIndicator(state.status) : null;
        const indicatorTitle =
          state?.status === "running"
            ? "Action running — click to view live progress"
            : state?.status === "failed"
            ? state.error
              ? `Last run failed: ${state.error}`
              : "Last run failed — click to see what happened"
            : state?.status === "done"
            ? "Last run finished — click to view"
            : "";
        return (
          <div key={op.id} className={`data-dash-chip data-dash-chip--${op.kind}`}>
            <button
              type="button"
              className="data-dash-chip-btn"
              onClick={() => onRunOperation(op)}
              title={op.prompt || op.label}
              disabled={state?.status === "running"}
            >
              <span className="data-dash-chip-kind">{op.kind === "form" ? "📝" : "💬"}</span>
              <span className="data-dash-chip-label">{op.label}</span>
            </button>
            {indicator && onOpenRun && (
              <button
                type="button"
                className={`data-dash-chip-status data-dash-chip-status--${state!.status}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenRun(op);
                }}
                title={indicatorTitle}
                aria-label={indicatorTitle}
              >
                {indicator}
              </button>
            )}
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
        );
      })}
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

function renderIndicator(status: OpRunStatus): ReactNode {
  if (status === "running") {
    return <span className="kanban-card-spin" aria-hidden />;
  }
  if (status === "failed") {
    return <span className="data-dash-chip-status-icon" aria-hidden>⚠</span>;
  }
  return <span className="data-dash-chip-status-icon" aria-hidden>✓</span>;
}
