import { useState, useCallback, useEffect, useRef } from "react";
import type {
  WorkflowDef,
  WorkflowSummary,
} from "../../types/workflow";
import * as api from "../../api/workflows";
import Modal from "../Modal";
import WorkflowFlow from "../WorkflowFlow";
import "./WorkflowView.css";

export default function WorkflowView({
  selectedPath,
  onOpen,
}: {
  selectedPath: string | null;
  onOpen: (path: string) => void;
}) {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [wf, setWf] = useState<WorkflowDef | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [confirmEnable, setConfirmEnable] = useState(false);

  const loadList = useCallback(async () => {
    try {
      const list = await api.listWorkflows();
      setWorkflows(list);
    } catch {}
  }, []);

  const loadWorkflow = useCallback(async (path: string) => {
    try {
      const data = await api.getWorkflow(path);
      setWf(data.definition);
    } catch {}
  }, []);

  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => {
    if (selectedPath) loadWorkflow(selectedPath);
    else setWf(null);
  }, [selectedPath, loadWorkflow]);

  const saveTimerRef = useRef(0);
  const latestWfRef = useRef<WorkflowDef | null>(null);

  const save = useCallback((updated: WorkflowDef) => {
    if (!selectedPath) return;
    setWf(updated);
    latestWfRef.current = updated;
    clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(async () => {
      setSaving(true);
      try {
        const d = latestWfRef.current;
        if (d) {
          await api.updateWorkflow(selectedPath, {
            title: d.title,
            enabled: d.enabled,
            triggers: d.triggers.map((t) => ({ ...t, type: t.type })),
            variables: d.variables,
            steps: d.steps.map((s) => ({ ...s, type: s.type })),
          });
        }
      } catch {}
      setSaving(false);
    }, 500);
  }, [selectedPath]);

  const flushSave = useCallback(async () => {
    clearTimeout(saveTimerRef.current);
    const d = latestWfRef.current;
    if (d && selectedPath) {
      await api.updateWorkflow(selectedPath, {
        title: d.title,
        enabled: d.enabled,
        triggers: d.triggers.map((t) => ({ ...t, type: t.type })),
        variables: d.variables,
        steps: d.steps.map((s) => ({ ...s, type: s.type })),
      });
    }
  }, [selectedPath]);

  const handleCreate = async (title: string) => {
    if (!title.trim()) return;
    setCreating(false);
    try {
      const res = await api.createWorkflow(`workflows/${title.trim()}`, title.trim());
      await loadList();
      onOpen(res.path);
    } catch {}
  };

  const handleRun = async () => {
    if (!selectedPath) return;
    setRunning(true);
    try {
      await api.runWorkflow(selectedPath);
      await loadWorkflow(selectedPath);
    } catch {}
    setRunning(false);
  };

  const handleDelete = async (path: string) => {
    try {
      await api.deleteWorkflow(path);
      await loadList();
    } catch {}
  };

  const handleToggleEnabled = (nextEnabled: boolean) => {
    if (!wf) return;
    if (nextEnabled) {
      setConfirmEnable(true);
    } else {
      save({ ...wf, enabled: false });
    }
  };

  const handleConfirmEnable = () => {
    if (!wf) return;
    setConfirmEnable(false);
    save({ ...wf, enabled: true });
  };

  // ── List View (no path selected) ──────────────────
  if (!selectedPath || !wf) {
    return (
      <div className="workflow-view">
        <div className="workflow-editor-header">
          <h2>Workflows</h2>
          <button className="wf-btn-primary" onClick={() => setCreating(true)}>
            + New
          </button>
        </div>
        <div className="workflow-body">
          {workflows.length === 0 && (
            <div className="workflow-empty">
              <div className="workflow-empty-icon">⚡</div>
              <p>No workflows yet</p>
              <p style={{ fontSize: 13 }}>
                Create a workflow to automate tasks with triggers and actions.
              </p>
            </div>
          )}
          {workflows.map((w) => (
            <div key={w.path} className="wf-list-card" onClick={() => onOpen(w.path)}>
              <div className="wf-list-card-header">
                <span>⚡</span>
                <span className="name">{w.title}</span>
                <span className="meta">
                  {w.step_count} steps · {w.trigger_count} triggers
                </span>
                <button
                  className="wf-btn-danger"
                  onClick={(e) => { e.stopPropagation(); handleDelete(w.path); }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
        {creating && (
          <Modal
            kind="prompt"
            title="New Workflow"
            message="Enter a name for the new workflow"
            defaultValue=""
            onSubmit={(v: string) => { if (v.trim()) handleCreate(v); }}
            onCancel={() => setCreating(false)}
          />
        )}
      </div>
    );
  }

  // ── Editor View (path selected) ───────────────────
  return (
    <div className="workflow-view">
      <div className="workflow-editor-header">
        <button className="wf-btn-secondary" onClick={() => onOpen("")} style={{ padding: "3px 8px" }}>
          ←
        </button>
        <h2>{wf.title}</h2>
        {saving && <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Saving…</span>}
        <div className="wf-toggle-row">
          <button
            className={`wf-toggle-switch${wf.enabled ? " on" : ""}`}
            onClick={() => handleToggleEnabled(!wf.enabled)}
            role="switch"
            aria-checked={wf.enabled}
          >
            <span className="wf-toggle-knob" />
          </button>
          <span className={`wf-toggle-label${wf.enabled ? " on" : ""}`}>
            {wf.enabled ? "Enabled" : "Disabled"}
          </span>
        </div>
        <button className="wf-btn-primary" onClick={handleRun} disabled={running || !wf.enabled}>
          {running ? "Running…" : "▶ Run"}
        </button>
      </div>

      <div className="wf-flow-container">
        <WorkflowFlow wf={wf} onSave={save} onFlushSave={flushSave} wfPath={selectedPath} />
      </div>

      {confirmEnable && (
        <Modal
          kind="confirm"
          title="Enable Workflow"
          message="Enabling this workflow will activate its triggers. Any configured webhooks, schedules, or event listeners will start firing automatically. Make sure your triggers and steps are correctly configured before enabling."
          confirmLabel="Enable"
          onSubmit={handleConfirmEnable}
          onCancel={() => setConfirmEnable(false)}
        />
      )}
    </div>
  );
}
