import { useState } from "react";
import type { StepRun, StepConfig } from "../../types/workflow";

interface Props {
  stepRun: StepRun;
  stepConfig: StepConfig | undefined;
  onClose: () => void;
  onCopyToEditor: () => void;
}

function statusBadge(status: string): { label: string; cls: string } {
  switch (status) {
    case "completed":
      return { label: "Completed", cls: "wf-exec-status wf-exec-ok" };
    case "failed":
      return { label: "Failed", cls: "wf-exec-status wf-exec-err" };
    case "running":
      return { label: "Running", cls: "wf-exec-status wf-exec-run" };
    case "skipped":
      return { label: "Skipped", cls: "wf-exec-status" };
    default:
      return { label: status, cls: "wf-exec-status" };
  }
}

function formatDuration(start?: string, end?: string): string {
  if (!start || !end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

function formatTime(iso?: string): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export default function ExecutionInspector({ stepRun, onClose, onCopyToEditor }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ output: true });

  const badge = statusBadge(stepRun.status);

  function toggle(key: string) {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <div className="wf-exec-inspector">
      <div className="wf-exec-inspector-header">
        <span className="wf-exec-inspector-name">{stepRun.step_name || "Step"}</span>
        <span className="wf-exec-inspector-type">{(stepRun.step_type || "").replace(/_/g, " ")}</span>
        <button className="wf-exec-inspector-close" onClick={onClose}>✕</button>
      </div>

      <div className="wf-exec-inspector-body">
        <div className="wf-exec-inspector-meta">
          <span className={badge.cls}>{badge.label}</span>
          {stepRun.started_at && (
            <span>
              {formatTime(stepRun.started_at)}
              {stepRun.finished_at ? ` → ${formatTime(stepRun.finished_at)}` : ""}
            </span>
          )}
          <span>{formatDuration(stepRun.started_at, stepRun.finished_at)}</span>
        </div>

        <div className="wf-exec-inspector-section">
          <button className="wf-exec-inspector-section-header" onClick={() => toggle("input")}>
            <span className={`arrow${expanded.input ? " expanded" : ""}`}>▸</span>
            Input
          </button>
          {expanded.input && (
            <pre className="wf-exec-inspector-pre">
              {stepRun.input_resolved
                ? JSON.stringify(stepRun.input_resolved, null, 2)
                : "No input data"}
            </pre>
          )}
        </div>

        <div className="wf-exec-inspector-section">
          <button className="wf-exec-inspector-section-header" onClick={() => toggle("output")}>
            <span className={`arrow${expanded.output ? " expanded" : ""}`}>▸</span>
            Output
          </button>
          {expanded.output && (
            <pre className="wf-exec-inspector-pre">
              {stepRun.output !== undefined && stepRun.output !== null
                ? JSON.stringify(stepRun.output, null, 2)
                : "No output data"}
            </pre>
          )}
        </div>

        {stepRun.error && (
          <div className="wf-exec-inspector-section">
            <button className="wf-exec-inspector-section-header" onClick={() => toggle("error")}>
              <span className={`arrow${expanded.error ? " expanded" : ""}`}>▸</span>
              Error
            </button>
            {expanded.error && (
              <pre className="wf-exec-inspector-pre error">{stepRun.error}</pre>
            )}
          </div>
        )}
      </div>

      <div className="wf-exec-inspector-actions">
        <button className="wf-exec-inspector-debug-btn" onClick={onCopyToEditor}>
          Copy to Editor &amp; Debug
        </button>
      </div>
    </div>
  );
}
