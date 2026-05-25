import { useCallback, useEffect, useMemo, useState } from "react";
import type { WorkflowDef, WorkflowRun, RunDetail, StepRun, StepConfig } from "../../types/workflow";
import * as wfApi from "../../api/workflows";
import ExecutionInspector from "./ExecutionInspector";

interface Props {
  wfPath: string;
  wf: WorkflowDef;
  children: React.ReactNode;
  onExecutionLoad: (detail: RunDetail | null) => void;
  onSeedFromRun: (runId: string) => void;
  monitorInspectStepId: string | null;
  onMonitorInspectClose: () => void;
}

function statusIcon(status: string): { icon: string; cls: string } {
  switch (status) {
    case "completed": return { icon: "✓", cls: "wf-exec-status wf-exec-ok" };
    case "failed": return { icon: "✗", cls: "wf-exec-status wf-exec-err" };
    case "running": return { icon: "●", cls: "wf-exec-status wf-exec-run" };
    default: return { icon: "○", cls: "wf-exec-status" };
  }
}

function duration(start: string, end?: string): string {
  if (!end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

function formatRunTime(iso: string, groupLabel: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (groupLabel === "Today") return time;
  if (groupLabel === "Yesterday") return `Yesterday ${time}`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + ` ${time}`;
}

function groupByDate(runs: WorkflowRun[]): { label: string; runs: WorkflowRun[] }[] {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);

  const groups: Record<string, WorkflowRun[]> = {};
  const order: string[] = [];

  for (const run of runs) {
    const d = new Date(run.started_at);
    let label: string;
    if (d >= today) label = "Today";
    else if (d >= yesterday) label = "Yesterday";
    else label = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    if (!groups[label]) {
      groups[label] = [];
      order.push(label);
    }
    groups[label].push(run);
  }

  return order.map((label) => ({ label, runs: groups[label] }));
}

export default function MonitorTab({
  wfPath,
  wf,
  children,
  onExecutionLoad,
  onSeedFromRun,
  monitorInspectStepId,
  onMonitorInspectClose,
}: Props) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<RunDetail | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await wfApi.listRuns(wfPath, 50);
      setRuns(list);
    } catch {
      console.error("Failed to load execution runs");
    } finally {
      setLoading(false);
    }
  }, [wfPath]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  const selectRun = useCallback(
    async (runId: string) => {
      if (selectedRunId === runId) return;
      setSelectedRunId(runId);
      try {
        const detail = await wfApi.getRun(wfPath, runId);
        setSelectedDetail(detail);
        onExecutionLoad(detail);
      } catch {
        console.error("Failed to load run detail");
        setSelectedDetail(null);
        onExecutionLoad(null);
      }
    },
    [wfPath, selectedRunId, onExecutionLoad],
  );

  const groups = useMemo(() => groupByDate(runs), [runs]);

  const inspectorStepRun = useMemo<StepRun | null>(() => {
    if (!monitorInspectStepId || !selectedDetail) return null;
    return selectedDetail.steps.find((s) => s.step_id === monitorInspectStepId) ?? null;
  }, [monitorInspectStepId, selectedDetail]);

  const inspectorStepConfig = useMemo<StepConfig | undefined>(() => {
    if (!monitorInspectStepId) return undefined;
    return wf.steps.find((s) => s.id === monitorInspectStepId);
  }, [monitorInspectStepId, wf.steps]);

  return (
    <div className="wf-monitor">
      <div className="wf-monitor-sidebar">
        <div className="wf-monitor-sidebar-header">
          <span>Executions</span>
          <button onClick={refresh} title="Refresh">
            ↻
          </button>
        </div>
        <div className="wf-monitor-list">
          {loading && (
            <div className="wf-exec-item" style={{ justifyContent: "center", color: "var(--text-muted)" }}>
              Loading...
            </div>
          )}
          {!loading && runs.length === 0 && (
            <div className="wf-exec-item" style={{ justifyContent: "center", color: "var(--text-muted)" }}>
              No executions yet
            </div>
          )}
          {groups.map((group) => (
            <div key={group.label}>
              <div className="wf-exec-group">{group.label}</div>
              {group.runs.map((run) => {
                const si = statusIcon(run.status);
                return (
                  <div
                    key={run.id}
                    className={`wf-exec-item${selectedRunId === run.id ? " selected" : ""}`}
                    onClick={() => selectRun(run.id)}
                  >
                    <span className={si.cls}>{si.icon}</span>
                    <div className="wf-exec-info">
                      <span className="wf-exec-time">
                        {formatRunTime(run.started_at, group.label)}
                      </span>
                      <span className="wf-exec-trigger">{run.trigger_type}</span>
                    </div>
                    <span className="wf-exec-duration">
                      {duration(run.started_at, run.finished_at)}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="wf-monitor-canvas">{children}</div>
      {inspectorStepRun && (
        <ExecutionInspector
          stepRun={inspectorStepRun}
          stepConfig={inspectorStepConfig}
          onClose={onMonitorInspectClose}
          onCopyToEditor={() => {
            if (selectedRunId) onSeedFromRun(selectedRunId);
          }}
        />
      )}
    </div>
  );
}
