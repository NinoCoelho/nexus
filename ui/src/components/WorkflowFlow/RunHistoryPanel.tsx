import { useCallback, useEffect, useState } from "react";
import type { WorkflowRun, RunDetail, StepRun } from "../../types/workflow";
import * as wfApi from "../../api/workflows";
import "./WorkflowFlow.css";

interface Props {
  wfPath: string;
  onClose: () => void;
  onLoadRun: (detail: RunDetail | null) => void;
}

function statusIcon(status: string) {
  if (status === "completed") return "\u2713";
  if (status === "failed") return "\u2717";
  if (status === "running") return "\u25CF";
  if (status === "cancelled") return "\u2298";
  return "\u25CB";
}

function statusClass(status: string) {
  if (status === "completed") return "wf-rh-ok";
  if (status === "failed") return "wf-rh-err";
  return "wf-rh-dim";
}

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function StepOutput({ sr }: { sr: StepRun }) {
  const [expanded, setExpanded] = useState(false);
  const out = sr.output as Record<string, unknown> | undefined;

  return (
    <div className="wf-rh-step" onClick={() => setExpanded(!expanded)}>
      <div className="wf-rh-step-row">
        <span className={`wf-rh-step-status ${statusClass(sr.status)}`}>
          {statusIcon(sr.status)}
        </span>
        <span className="wf-rh-step-name">
          {sr.step_name || sr.step_slug || sr.step_id}
        </span>
        <span className="wf-rh-step-type">{sr.step_type}</span>
      </div>
      {expanded && (
        <div className="wf-rh-step-detail">
          {sr.error && (
            <div className="wf-rh-section">
              <label>Error</label>
              <pre className="wf-rh-pre wf-rh-pre-err">{sr.error}</pre>
            </div>
          )}
          {sr.input_resolved && (
            <div className="wf-rh-section">
              <label>Input</label>
              <pre className="wf-rh-pre">{JSON.stringify(sr.input_resolved, null, 2)}</pre>
            </div>
          )}
          {out && (
            <div className="wf-rh-section">
              <label>Output</label>
              <pre className="wf-rh-pre">
                {typeof out === "string" ? out : JSON.stringify(out, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function RunHistoryPanel({ wfPath, onClose, onLoadRun }: Props) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    wfApi.listRuns(wfPath, 20).then(setRuns).catch(() => {}).finally(() => setLoading(false));
  }, [wfPath]);

  useEffect(() => {
    return () => onLoadRun(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadDetail = useCallback(async (runId: string) => {
    if (selectedRunId === runId) {
      setSelectedRunId(null);
      setDetail(null);
      onLoadRun(null);
      return;
    }
    setSelectedRunId(runId);
    setDetail(null);
    try {
      const d = await wfApi.getRun(wfPath, runId);
      setDetail(d);
      onLoadRun(d);
    } catch {
      onLoadRun(null);
    }
  }, [wfPath, selectedRunId, onLoadRun]);

  return (
    <div className="wf-debug-panel">
      <div className="wf-debug-header">
        <span className="wf-debug-title">Run History</span>
        <button className="wf-debug-close" onClick={onClose}>\u2715</button>
      </div>
      <div className="wf-debug-body">
        {loading && <div className="wf-rh-dim">Loading\u2026</div>}
        {!loading && runs.length === 0 && <div className="wf-rh-dim">No runs yet</div>}
        {runs.map((run) => (
          <div key={run.id}>
            <div
              className={`wf-rh-run-row${selectedRunId === run.id ? " selected" : ""}`}
              onClick={() => loadDetail(run.id)}
            >
              <span className={`wf-rh-step-status ${statusClass(run.status)}`}>
                {statusIcon(run.status)}
              </span>
              <span className="wf-rh-run-time">{formatTime(run.started_at)}</span>
              <span className="wf-rh-run-trigger">{run.trigger_type}</span>
            </div>
            {selectedRunId === run.id && (
              <div className="wf-rh-detail">
                {detail ? (
                  detail.steps.length === 0 ? (
                    <div className="wf-rh-dim">No step data</div>
                  ) : (
                    detail.steps.map((sr) => (
                      <StepOutput key={sr.step_id} sr={sr} />
                    ))
                  )
                ) : (
                  <div className="wf-rh-dim">Loading\u2026</div>
                )}
                {run.error && (
                  <div className="wf-rh-section">
                    <label>Run Error</label>
                    <pre className="wf-rh-pre wf-rh-pre-err">{run.error}</pre>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
