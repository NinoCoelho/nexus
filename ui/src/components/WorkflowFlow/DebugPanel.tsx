import { useCallback, useEffect, useState } from "react";
import type { DebugStepEvent } from "../../types/workflow";
import {
  startDebug as apiStartDebug,
  debugContinue as apiContinue,
  debugRerunStep as apiRerunStep,
  debugCancel as apiCancel,
  debugEventsUrl,
} from "../../api/workflows";

interface DebugState {
  runId: string;
  status: "running" | "completed" | "failed" | "cancelled";
  currentStepId: string | null;
  stepEvents: Record<string, DebugStepEvent>;
}

export default function DebugPanel({
  wfPath,
  onClose,
}: {
  wfPath: string;
  onClose: () => void;
}) {
  const [debug, setDebug] = useState<DebugState | null>(null);
  const [starting, setStarting] = useState(false);
  const [samplePayload, setSamplePayload] = useState("{}");
  const [selectedStep, setSelectedStep] = useState<string | null>(null);

  const startDebug = useCallback(async () => {
    setStarting(true);
    try {
      let payload = {};
      try { payload = JSON.parse(samplePayload); } catch {}
      const run = await apiStartDebug(wfPath, payload);
      setDebug({
        runId: run.id,
        status: "running",
        currentStepId: null,
        stepEvents: {},
      });
    } catch (e: any) {
      alert(e.message || "Failed to start debug");
    } finally {
      setStarting(false);
    }
  }, [wfPath, samplePayload]);

  useEffect(() => {
    if (!debug?.runId) return;

    const url = debugEventsUrl(wfPath, debug.runId);
    const es = new EventSource(url);

    es.addEventListener("workflow.debug.step_starting", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setDebug((prev) => prev ? {
        ...prev,
        currentStepId: data.step_id,
        stepEvents: {
          ...prev.stepEvents,
          [data.step_id]: {
            ...prev.stepEvents[data.step_id],
            ...data,
            status: "running",
          },
        },
      } : prev);
    });

    es.addEventListener("workflow.debug.step_completed", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setDebug((prev) => prev ? {
        ...prev,
        stepEvents: {
          ...prev.stepEvents,
          [data.step_id]: {
            ...prev.stepEvents[data.step_id],
            ...data,
          },
        },
      } : prev);
    });

    es.addEventListener("workflow.debug.step_failed", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setDebug((prev) => prev ? {
        ...prev,
        status: "failed",
        stepEvents: {
          ...prev.stepEvents,
          [data.step_id]: {
            ...prev.stepEvents[data.step_id],
            ...data,
          },
        },
      } : prev);
    });

    es.addEventListener("workflow.debug.run_completed", (e: MessageEvent) => {
      const data = JSON.parse(e.data);
      setDebug((prev) => prev ? { ...prev, status: data.status || "completed" } : prev);
      es.close();
    });

    es.onerror = () => {
      es.close();
    };

    return () => { es.close(); };
  }, [debug?.runId, wfPath]);

  const handleContinue = useCallback(async (stepId?: string) => {
    if (!debug?.runId) return;
    await apiContinue(wfPath, debug.runId, stepId);
  }, [debug?.runId, wfPath]);

  const handleCancel = useCallback(async () => {
    if (!debug?.runId) return;
    await apiCancel(wfPath, debug.runId);
    setDebug((prev) => prev ? { ...prev, status: "cancelled" } : prev);
  }, [debug?.runId, wfPath]);

  const handleRerun = useCallback(async (stepId: string) => {
    if (!debug?.runId) return;
    const sr = await apiRerunStep(wfPath, debug.runId, stepId);
    setDebug((prev) => prev ? {
      ...prev,
      stepEvents: {
        ...prev.stepEvents,
        [stepId]: {
          ...prev.stepEvents[stepId],
          status: sr.status,
          output: sr.output,
          error: sr.error,
        },
      },
    } : prev);
  }, [debug?.runId, wfPath]);

  const currentEvent = debug?.currentStepId ? debug.stepEvents[debug.currentStepId] : null;

  return (
    <div className="wf-debug-panel">
      <div className="wf-debug-header">
        <span className="wf-debug-title">Debug</span>
        <button className="wf-debug-close" onClick={onClose}>✕</button>
      </div>

      {!debug ? (
        <div className="wf-debug-body">
          <div className="wf-field">
            <label>Sample Payload (JSON)</label>
            <textarea
              value={samplePayload}
              onChange={(e) => setSamplePayload(e.target.value)}
              placeholder='{"key": "value"}'
              style={{ fontFamily: "var(--font-mono)", fontSize: 11, minHeight: 60 }}
            />
          </div>
          <button
            className="wf-debug-start"
            disabled={starting}
            onClick={startDebug}
          >
            {starting ? "Starting…" : "▶ Start Debug"}
          </button>
        </div>
      ) : (
        <div className="wf-debug-body">
          <div className="wf-debug-status">
            <span className={`wf-debug-badge wf-debug-badge-${debug.status}`}>
              {debug.status}
            </span>
            <span className="wf-debug-run-id">{debug.runId.slice(0, 8)}…</span>
          </div>

          {debug.status === "running" && currentEvent && (
            <div className="wf-debug-controls">
              <button
                className="wf-debug-btn"
                onClick={() => handleContinue(debug.currentStepId || undefined)}
                title="Continue to next step"
              >▶| Step</button>
              <button
                className="wf-debug-btn"
                onClick={() => handleContinue()}
                title="Continue running"
              >▶ Continue</button>
              <button
                className="wf-debug-btn wf-debug-btn-danger"
                onClick={handleCancel}
              >■ Cancel</button>
            </div>
          )}

          <div className="wf-debug-steps">
            {Object.entries(debug.stepEvents).map(([stepId, evt]) => (
              <div
                key={stepId}
                className={`wf-debug-step${selectedStep === stepId ? " selected" : ""}${debug.currentStepId === stepId ? " current" : ""}`}
                onClick={() => setSelectedStep(selectedStep === stepId ? null : stepId)}
              >
                <div className="wf-debug-step-header">
                  <span className={`wf-debug-step-status wf-dss-${evt.status}`}>
                    {evt.status === "completed" ? "✓" : evt.status === "failed" ? "✗" : evt.status === "running" ? "●" : "○"}
                  </span>
                  <span className="wf-debug-step-name">{evt.step_name || stepId}</span>
                  <span className="wf-debug-step-type">{evt.step_type || ""}</span>
                </div>
                {selectedStep === stepId && (
                  <div className="wf-debug-step-detail">
                    {evt.input_resolved && (
                      <div className="wf-debug-section">
                        <label>Input</label>
                        <pre>{JSON.stringify(evt.input_resolved, null, 2)}</pre>
                      </div>
                    )}
                    {evt.output !== undefined && (
                      <div className="wf-debug-section">
                        <label>Output</label>
                        <pre>{typeof evt.output === "string" ? evt.output : JSON.stringify(evt.output, null, 2)}</pre>
                      </div>
                    )}
                    {evt.error && (
                      <div className="wf-debug-section">
                        <label>Error</label>
                        <pre className="wf-debug-error">{evt.error}</pre>
                      </div>
                    )}
                    {debug.status !== "running" && (
                      <button
                        className="wf-debug-btn"
                        onClick={(e) => { e.stopPropagation(); handleRerun(stepId); }}
                      >↻ Rerun</button>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
