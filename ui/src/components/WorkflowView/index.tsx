import { useState, useCallback, useEffect } from "react";
import type {
  WorkflowDef,
  WorkflowSummary,
  WorkflowRun,
  TriggerConfig,
  TriggerType,
  StepConfig,
  StepType,
} from "../../types/workflow";
import * as api from "../../api/workflows";
import Modal from "../Modal";
import "./WorkflowView.css";

const STEP_ICONS: Record<StepType, string> = {
  tool_call: "🔧",
  agent_session: "🤖",
  mcp_call: "🔌",
  http_request: "🌐",
  condition: "🔀",
  transform: "🔄",
  delay: "⏱️",
};

const TRIGGER_ICONS: Record<TriggerType, string> = {
  webhook: "🔗",
  fs_watch: "📁",
  schedule: "📅",
  manual: "👆",
  event: "📡",
};

function formatTime(iso: string) {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString();
  } catch {
    return iso;
  }
}

function genId() {
  return Math.random().toString(36).substring(2, 10);
}

export default function WorkflowView({
  selectedPath,
  onOpen,
}: {
  selectedPath: string | null;
  onOpen: (path: string) => void;
}) {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [wf, setWf] = useState<WorkflowDef | null>(null);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [activeStep, setActiveStep] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  const loadList = useCallback(async () => {
    try {
      const list = await api.listWorkflows();
      setWorkflows(list);
    } catch {}
  }, []);

  const loadWorkflow = useCallback(
    async (path: string) => {
      try {
        const data = await api.getWorkflow(path);
        setWf(data.definition);
        setRuns(data.runs);
        setActiveStep(null);
      } catch {}
    },
    [],
  );

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    if (selectedPath) loadWorkflow(selectedPath);
    else setWf(null);
  }, [selectedPath, loadWorkflow]);

  const save = useCallback(
    async (updated: WorkflowDef) => {
      if (!selectedPath) return;
      setSaving(true);
      try {
        await api.updateWorkflow(selectedPath, {
          title: updated.title,
          enabled: updated.enabled,
          triggers: updated.triggers.map((t) => ({
            ...t,
            type: t.type,
          })),
          variables: updated.variables,
          steps: updated.steps.map((s) => ({
            ...s,
            type: s.type,
          })),
        });
        setWf(updated);
      } catch {}
      setSaving(false);
    },
    [selectedPath],
  );

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    setCreating(false);
    try {
      const res = await api.createWorkflow(`workflows/${newTitle.trim()}`);
      await loadList();
      onOpen(res.path);
      setNewTitle("");
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

  const addTrigger = (type: TriggerType) => {
    if (!wf) return;
    const trigger: TriggerConfig = { id: genId(), type };
    save({ ...wf, triggers: [...wf.triggers, trigger] });
  };

  const removeTrigger = (id: string) => {
    if (!wf) return;
    save({ ...wf, triggers: wf.triggers.filter((t) => t.id !== id) });
  };

  const updateTrigger = (id: string, patch: Partial<TriggerConfig>) => {
    if (!wf) return;
    save({
      ...wf,
      triggers: wf.triggers.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    });
  };

  const addStep = (type: StepType) => {
    if (!wf) return;
    const step: StepConfig = {
      id: genId(),
      name: `Step ${wf.steps.length + 1}`,
      type,
    };
    save({ ...wf, steps: [...wf.steps, step] });
  };

  const removeStep = (id: string) => {
    if (!wf) return;
    save({ ...wf, steps: wf.steps.filter((s) => s.id !== id) });
    if (activeStep === id) setActiveStep(null);
  };

  const moveStep = (id: string, dir: -1 | 1) => {
    if (!wf) return;
    const idx = wf.steps.findIndex((s) => s.id === id);
    const newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= wf.steps.length) return;
    const steps = [...wf.steps];
    [steps[idx], steps[newIdx]] = [steps[newIdx], steps[idx]];
    save({ ...wf, steps });
  };

  const updateStep = (id: string, patch: Partial<StepConfig>) => {
    if (!wf) return;
    save({
      ...wf,
      steps: wf.steps.map((s) => (s.id === id ? { ...s, ...patch } : s)),
    });
  };

  const addVariable = () => {
    if (!wf) return;
    const key = `var_${Object.keys(wf.variables).length + 1}`;
    save({ ...wf, variables: { ...wf.variables, [key]: "" } });
  };

  const updateVariable = (key: string, val: string) => {
    if (!wf) return;
    save({ ...wf, variables: { ...wf.variables, [key]: val } });
  };

  const removeVariable = (key: string) => {
    if (!wf) return;
    const { [key]: _, ...rest } = wf.variables;
    save({ ...wf, variables: rest });
  };

  if (!selectedPath || !wf) {
    return (
      <div className="workflow-view">
        <div className="workflow-editor-header">
          <h2>Workflows</h2>
          <button
            className="wf-btn-primary"
            onClick={() => setCreating(true)}
          >
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
            <div
              key={w.path}
              className="wf-step-card"
              style={{ marginBottom: 8 }}
              onClick={() => onOpen(w.path)}
            >
              <div className="wf-step-card-header">
                <span className="icon">⚡</span>
                <span className="step-name">{w.title}</span>
                <span className="step-type">
                  {w.step_count} steps · {w.trigger_count} triggers
                </span>
                <button
                  className="wf-btn-danger"
                  style={{ fontSize: 11, padding: "2px 8px" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(w.path);
                  }}
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
            onSubmit={(v: string) => {
              setNewTitle(v);
              if (v.trim()) handleCreate();
            }}
            onCancel={() => setCreating(false)}
          />
        )}
      </div>
    );
  }

  return (
    <div className="workflow-view">
      <div className="workflow-editor-header">
        <button
          className="wf-btn-secondary"
          onClick={() => onOpen("")}
          style={{ padding: "4px 10px" }}
        >
          ←
        </button>
        <h2>{wf.title}</h2>
        {saving && (
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Saving...
          </span>
        )}
        <label className="workflow-enabled-toggle">
          <input
            type="checkbox"
            checked={wf.enabled}
            onChange={(e) => save({ ...wf, enabled: e.target.checked })}
          />
          Enabled
        </label>
        <button
          className="wf-btn-primary"
          onClick={handleRun}
          disabled={running}
        >
          {running ? "Running..." : "▶ Run Now"}
        </button>
      </div>

      <div className="workflow-body">
        {/* Triggers */}
        <div className="workflow-section-title">
          Triggers
          <button onClick={() => addTrigger("manual")}>+ Add</button>
        </div>
        <div className="wf-trigger-list">
          {wf.triggers.map((t) => (
            <div key={t.id} className="wf-trigger-card">
              <div className="wf-trigger-card-header">
                <span className="icon">{TRIGGER_ICONS[t.type]}</span>
                <span className="type-label">
                  {t.type === "fs_watch"
                    ? "File Watch"
                    : t.type === "schedule"
                      ? "Schedule"
                      : t.type.charAt(0).toUpperCase() + t.type.slice(1)}
                </span>
              </div>
              <TriggerDetail trigger={t} onChange={(p) => updateTrigger(t.id, p)} />
              <div className="actions">
                <button onClick={() => removeTrigger(t.id)}>Remove</button>
              </div>
            </div>
          ))}
          {wf.triggers.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
              No triggers configured. This workflow can only be run manually.
            </div>
          )}
        </div>

        {/* Steps */}
        <div className="workflow-section-title">
          Steps
          <button onClick={() => addStep("tool_call")}>+ Tool</button>
          <button onClick={() => addStep("agent_session")}>+ Agent</button>
          <button onClick={() => addStep("condition")}>+ Condition</button>
          <button onClick={() => addStep("delay")}>+ Delay</button>
        </div>
        <div className="wf-step-list">
          {wf.steps.map((step, idx) => (
            <div key={step.id}>
              {idx > 0 && (
                <div className="wf-step-connector">│</div>
              )}
              <div
                className={`wf-step-card${activeStep === step.id ? " active" : ""}`}
                onClick={() => setActiveStep(activeStep === step.id ? null : step.id)}
              >
                <div className="wf-step-card-header">
                  <span className="step-num">{idx + 1}</span>
                  <span className="icon">{STEP_ICONS[step.type]}</span>
                  <span className="step-name">{step.name}</span>
                  <span className="step-type">{step.type.replace("_", " ")}</span>
                  <span className="step-actions">
                    <button
                      onClick={(e) => { e.stopPropagation(); moveStep(step.id, -1); }}
                      disabled={idx === 0}
                    >
                      ↑
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); moveStep(step.id, 1); }}
                      disabled={idx === wf.steps.length - 1}
                    >
                      ↓
                    </button>
                    <button
                      onClick={(e) => { e.stopPropagation(); removeStep(step.id); }}
                    >
                      ✕
                    </button>
                  </span>
                </div>
                <StepSummary step={step} />
                {step.condition && (
                  <div className="step-condition">
                    if: {step.condition}
                  </div>
                )}
                {activeStep === step.id && (
                  <StepConfigForm
                    step={step}
                    steps={wf.steps}
                    onChange={(p) => updateStep(step.id, p)}
                  />
                )}
              </div>
            </div>
          ))}
          {wf.steps.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-muted)", padding: "12px 0" }}>
              No steps yet. Add a tool call, agent session, or condition to get started.
            </div>
          )}
        </div>

        {/* Variables */}
        <div className="workflow-section-title">
          Variables
          <button onClick={addVariable}>+ Add</button>
        </div>
        <div className="wf-variables-list">
          {Object.entries(wf.variables).map(([key, val]) => (
            <div key={key} className="wf-variable-row">
              <input
                value={key}
                onChange={(e) => {
                  void wf.variables;
                  updateVariable(e.target.value, val);
                  removeVariable(key);
                }}
                placeholder="key"
              />
              <input
                value={val}
                onChange={(e) => updateVariable(key, e.target.value)}
                placeholder="value"
              />
              <button onClick={() => removeVariable(key)}>✕</button>
            </div>
          ))}
          {Object.keys(wf.variables).length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-muted)" }}>
              No variables. Add key-value pairs to parameterize your workflow.
            </div>
          )}
        </div>

        {/* Run History */}
        {runs.length > 0 && (
          <div className="wf-run-history">
            <div className="workflow-section-title">Run History</div>
            {runs.map((run) => (
              <div key={run.id} className="wf-run-item">
                <span className="status-icon">
                  {run.status === "completed"
                    ? "✅"
                    : run.status === "failed"
                      ? "❌"
                      : run.status === "running"
                        ? "⏳"
                        : "⏸️"}
                </span>
                <span className="run-time">{formatTime(run.started_at)}</span>
                <span className="run-detail">
                  {run.status}
                  {run.error ? ` — ${run.error.slice(0, 80)}` : ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function TriggerDetail({
  trigger,
  onChange,
}: {
  trigger: TriggerConfig;
  onChange: (patch: Partial<TriggerConfig>) => void;
}) {
  switch (trigger.type) {
    case "webhook":
      return (
        <div className="detail">
          {trigger.token
            ? `Token: ${trigger.token.slice(0, 8)}...`
            : "Token will be generated on save"}
        </div>
      );
    case "schedule":
      return (
        <div>
          <div className="wf-config-field">
            <label>Cron Expression</label>
            <input
              value={trigger.cron || ""}
              onChange={(e) => onChange({ cron: e.target.value })}
              placeholder="0 9 * * 1-5"
            />
          </div>
        </div>
      );
    case "fs_watch":
      return (
        <div>
          <div className="wf-config-field">
            <label>Folder Path</label>
            <input
              value={trigger.path || ""}
              onChange={(e) => onChange({ path: e.target.value })}
              placeholder="~/Downloads"
            />
          </div>
          <div className="wf-config-field">
            <label>Glob Pattern</label>
            <input
              value={trigger.pattern || "*"}
              onChange={(e) => onChange({ pattern: e.target.value })}
              placeholder="*.pdf"
            />
          </div>
        </div>
      );
    case "event":
      return (
        <div>
          <div className="wf-config-field">
            <label>Event Pattern</label>
            <input
              value={trigger.event || ""}
              onChange={(e) => onChange({ event: e.target.value })}
              placeholder="vault.*"
            />
          </div>
        </div>
      );
    default:
      return <div className="detail">Manual trigger</div>;
  }
}

function StepSummary({ step }: { step: StepConfig }) {
  switch (step.type) {
    case "tool_call":
      return <div className="step-summary">tool: {step.tool || "—"}</div>;
    case "agent_session":
      return (
        <div className="step-summary">
          {(step.prompt || "—").slice(0, 60)}
        </div>
      );
    case "condition":
      return (
        <div className="step-summary">
          if: {(step.expression || "—").slice(0, 60)}
        </div>
      );
    case "delay":
      return (
        <div className="step-summary">{step.duration_seconds || 0}s delay</div>
      );
    case "transform":
      return (
        <div className="step-summary">
          {(step.template || "—").slice(0, 60)}
        </div>
      );
    case "http_request":
      return (
        <div className="step-summary">
          {step.method || "GET"} {(step.url || "—").slice(0, 40)}
        </div>
      );
    case "mcp_call":
      return (
        <div className="step-summary">
          {step.mcp_server}/{step.mcp_tool || "—"}
        </div>
      );
    default:
      return null;
  }
}

function StepConfigForm({
  step,
  steps,
  onChange,
}: {
  step: StepConfig;
  steps: StepConfig[];
  onChange: (patch: Partial<StepConfig>) => void;
}) {
  const set = (patch: Partial<StepConfig>) => onChange(patch);

  const commonFields = (
    <>
      <div className="wf-config-field">
        <label>Condition (skip if falsy)</label>
        <input
          value={step.condition || ""}
          onChange={(e) => set({ condition: e.target.value || undefined })}
          placeholder="{{steps.prev.result}} == 'ok'"
        />
      </div>
      <div className="wf-config-field">
        <label>On Error</label>
        <select
          value={step.on_error || "stop"}
          onChange={(e) => set({ on_error: e.target.value })}
        >
          <option value="stop">Stop</option>
          <option value="continue">Continue</option>
          {steps.map((s) => (
            <option key={s.id} value={`goto:${s.id}`}>
              Goto: {s.name}
            </option>
          ))}
        </select>
      </div>
      <div className="wf-config-field">
        <label>Retry Count</label>
        <input
          type="number"
          value={step.retry_count || 0}
          min={0}
          onChange={(e) => set({ retry_count: parseInt(e.target.value) || 0 })}
        />
      </div>
    </>
  );

  switch (step.type) {
    case "tool_call":
      return (
        <div className="step-config-form" onClick={(e) => e.stopPropagation()}>
          <div className="wf-config-field">
            <label>Tool Name</label>
            <input
              value={step.tool || ""}
              onChange={(e) => set({ tool: e.target.value })}
              placeholder="vault_write"
            />
          </div>
          <div className="wf-config-field">
            <label>Input (JSON)</label>
            <textarea
              value={
                step.input ? JSON.stringify(step.input, null, 2) : ""
              }
              onChange={(e) => {
                try {
                  set({ input: JSON.parse(e.target.value) });
                } catch {}
              }}
              placeholder='{"path": "{{vars.dir}}/output.md"}'
            />
          </div>
          {commonFields}
        </div>
      );
    case "agent_session":
      return (
        <div className="step-config-form" onClick={(e) => e.stopPropagation()}>
          <div className="wf-config-field">
            <label>Prompt</label>
            <textarea
              value={step.prompt || ""}
              onChange={(e) => set({ prompt: e.target.value })}
              placeholder="Analyze this data: {{steps.prev.result}}"
            />
          </div>
          <div className="wf-config-field">
            <label>Model (optional)</label>
            <input
              value={step.model || ""}
              onChange={(e) => set({ model: e.target.value || undefined })}
              placeholder="fast"
            />
          </div>
          {commonFields}
        </div>
      );
    case "condition":
      return (
        <div className="step-config-form" onClick={(e) => e.stopPropagation()}>
          <div className="wf-config-field">
            <label>Expression</label>
            <textarea
              value={step.expression || ""}
              onChange={(e) => set({ expression: e.target.value })}
              placeholder="trigger.amount > 0"
            />
          </div>
          <div className="wf-config-field">
            <label>Then (step ID)</label>
            <select
              value={step.then_step || ""}
              onChange={(e) => set({ then_step: e.target.value || undefined })}
            >
              <option value="">Next step</option>
              {steps
                .filter((s) => s.id !== step.id)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
            </select>
          </div>
          <div className="wf-config-field">
            <label>Else (step ID)</label>
            <select
              value={step.else_step || ""}
              onChange={(e) => set({ else_step: e.target.value || undefined })}
            >
              <option value="">Next step</option>
              {steps
                .filter((s) => s.id !== step.id)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
            </select>
          </div>
        </div>
      );
    case "delay":
      return (
        <div className="step-config-form" onClick={(e) => e.stopPropagation()}>
          <div className="wf-config-field">
            <label>Duration (seconds)</label>
            <input
              type="number"
              value={step.duration_seconds || 0}
              min={0}
              onChange={(e) =>
                set({ duration_seconds: parseInt(e.target.value) || 0 })
              }
            />
          </div>
        </div>
      );
    case "transform":
      return (
        <div className="step-config-form" onClick={(e) => e.stopPropagation()}>
          <div className="wf-config-field">
            <label>Template</label>
            <textarea
              value={step.template || ""}
              onChange={(e) => set({ template: e.target.value })}
              placeholder="Hello {{vars.name}}!"
            />
          </div>
          <div className="wf-config-field">
            <label>Output Format</label>
            <select
              value={step.output_format || "text"}
              onChange={(e) => set({ output_format: e.target.value })}
            >
              <option value="text">Text</option>
              <option value="json">JSON</option>
            </select>
          </div>
        </div>
      );
    case "http_request":
      return (
        <div className="step-config-form" onClick={(e) => e.stopPropagation()}>
          <div className="wf-config-field">
            <label>URL</label>
            <input
              value={step.url || ""}
              onChange={(e) => set({ url: e.target.value })}
              placeholder="https://api.example.com/data"
            />
          </div>
          <div className="wf-config-field">
            <label>Method</label>
            <select
              value={step.method || "GET"}
              onChange={(e) => set({ method: e.target.value })}
            >
              <option>GET</option>
              <option>POST</option>
              <option>PUT</option>
              <option>PATCH</option>
              <option>DELETE</option>
            </select>
          </div>
          <div className="wf-config-field">
            <label>Body (JSON, optional)</label>
            <textarea
              value={
                step.body ? JSON.stringify(step.body, null, 2) : ""
              }
              onChange={(e) => {
                try {
                  set({ body: JSON.parse(e.target.value) });
                } catch {}
              }}
              placeholder='{"key": "value"}'
            />
          </div>
          {commonFields}
        </div>
      );
    case "mcp_call":
      return (
        <div className="step-config-form" onClick={(e) => e.stopPropagation()}>
          <div className="wf-config-field">
            <label>MCP Server</label>
            <input
              value={step.mcp_server || ""}
              onChange={(e) => set({ mcp_server: e.target.value })}
              placeholder="server-name"
            />
          </div>
          <div className="wf-config-field">
            <label>MCP Tool</label>
            <input
              value={step.mcp_tool || ""}
              onChange={(e) => set({ mcp_tool: e.target.value })}
              placeholder="tool-name"
            />
          </div>
          {commonFields}
        </div>
      );
    default:
      return null;
  }
}
