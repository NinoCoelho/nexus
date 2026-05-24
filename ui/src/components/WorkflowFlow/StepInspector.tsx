import { useCallback, useEffect, useMemo, useState } from "react";
import type { StepConfig, StepRun } from "../../types/workflow";
import { resolveTemplate, getWorkflowSamples, generateScript } from "../../api/workflows";
import { getSession } from "../../api/sessions";
import TemplateInput from "./TemplateInput";
import { slugify } from "./index";
import "./WorkflowFlow.css";

interface StepSample {
  name: string;
  slug: string;
  input_resolved?: unknown;
  output?: unknown;
}

interface StepInspectorProps {
  step: StepConfig;
  stepRun: StepRun | null;
  allStepRuns: StepRun[];
  allSteps: StepConfig[];
  triggerPayload: Record<string, unknown>;
  variables: Record<string, string>;
  wfPath?: string;
  onStepPatch: (patch: Partial<StepConfig>) => void;
  onExecute: () => void;
  onClose: () => void;
  executing?: boolean;
}

function JsonTreeNode({
  label,
  value,
  path,
  depth = 0,
  draggable = true,
}: {
  label: string;
  value: unknown;
  path: string;
  depth?: number;
  draggable?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(depth > 2);

  if (value === null || value === undefined) {
    return (
      <div className="wf-tree-node" style={{ paddingLeft: depth * 12 }}>
        <span className="wf-tree-key">{label}</span>
        <span className="wf-tree-colon">: </span>
        <span className="wf-tree-null">{value === null ? "null" : "undefined"}</span>
      </div>
    );
  }

  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return (
      <div
        className={`wf-tree-node${draggable ? " wf-tree-draggable" : ""}`}
        style={{ paddingLeft: depth * 12 }}
        draggable={draggable}
        onDragStart={draggable ? (e) => {
          e.dataTransfer.setData("text/plain", `{{${path}}}`);
          e.dataTransfer.effectAllowed = "copy";
        } : undefined}
      >
        <span className="wf-tree-key">{label}</span>
        <span className="wf-tree-colon">: </span>
        <span className={`wf-tree-${typeof value}`}>
          {typeof value === "string"
            ? `"${value.length > 80 ? value.slice(0, 80) + "…" : value}"`
            : String(value)}
        </span>
      </div>
    );
  }

  if (Array.isArray(value)) {
    return (
      <div className="wf-tree-node" style={{ paddingLeft: depth * 12 }}>
        <button className="wf-tree-toggle" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? "▸" : "▾"}
        </button>
        <span className="wf-tree-key">{label}</span>
        <span className="wf-tree-colon">: </span>
        <span className="wf-tree-bracket">[{value.length}]</span>
        {!collapsed &&
          value.slice(0, 20).map((item, i) => (
            <JsonTreeNode
              key={i}
              label={`[${i}]`}
              value={item}
              path={`${path}.${i}`}
              depth={depth + 1}
              draggable={draggable}
            />
          ))}
      </div>
    );
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <div className="wf-tree-node" style={{ paddingLeft: depth * 12 }}>
        <button className="wf-tree-toggle" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? "▸" : "▾"}
        </button>
        <span
          className={`wf-tree-key${draggable ? " wf-tree-draggable" : ""}`}
          draggable={draggable}
          onDragStart={draggable ? (e) => {
            e.dataTransfer.setData("text/plain", `{{${path}}}`);
            e.dataTransfer.effectAllowed = "copy";
          } : undefined}
        >
          {label}
        </span>
        <span className="wf-tree-colon">: </span>
        <span className="wf-tree-bracket">{"{}"}</span>
        {!collapsed &&
          entries.map(([k, v]) => (
            <JsonTreeNode
              key={k}
              label={k}
              value={v}
              path={`${path}.${k}`}
              depth={depth + 1}
              draggable={draggable}
            />
          ))}
      </div>
    );
  }

  return null;
}

function DataTree({
  data,
  rootLabel,
  rootPath,
  draggable = true,
}: {
  data: unknown;
  rootLabel: string;
  rootPath: string;
  draggable?: boolean;
}) {
  if (data === null || data === undefined) {
    return <div className="wf-tree-empty">No data</div>;
  }
  return (
    <div className="wf-data-tree">
      <JsonTreeNode label={rootLabel} value={data} path={rootPath} draggable={draggable} depth={0} />
    </div>
  );
}

function PreviewPanel({
  template,
  triggerPayload,
  stepOutputs,
  variables,
}: {
  template: string;
  triggerPayload: Record<string, unknown>;
  stepOutputs: Record<string, unknown>;
  variables: Record<string, string>;
}) {
  const [resolved, setResolved] = useState<string>("");

  useEffect(() => {
    resolveTemplate(template, triggerPayload, stepOutputs, variables).then(setResolved);
  }, [template, triggerPayload, stepOutputs, variables]);

  if (!template) {
    return <div className="wf-preview-empty">No template to preview</div>;
  }

  return (
    <pre className="wf-preview-content">{resolved}</pre>
  );
}

function cleanOutput(output: unknown): unknown {
  if (output && typeof output === "object" && "_simulated" in (output as Record<string, unknown>)) {
    const o = { ...(output as Record<string, unknown>) };
    delete o._simulated;
    return o;
  }
  return output;
}

function extractSessionId(output: unknown): string | null {
  if (!output || typeof output !== "object") return null;
  const o = output as Record<string, unknown>;
  if (typeof o.session_id === "string" && o.session_id) return o.session_id;
  return null;
}

function AgentLogPanel({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<Array<{
    role: string;
    content: string;
    tool_calls?: unknown;
  }> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSession(sessionId)
      .then((session) => {
        if (!cancelled) setMessages(session.messages || []);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Failed to load log");
      });
    return () => { cancelled = true; };
  }, [sessionId]);

  if (error) {
    return <div className="wf-agent-log-error">{error}</div>;
  }

  if (!messages) {
    return <div className="wf-agent-log-loading">Loading log…</div>;
  }

  return (
    <div className="wf-agent-log">
      {messages.map((msg, i) => (
        <div key={i} className={`wf-agent-log-msg wf-alm-${msg.role}`}>
          <span className="wf-alm-role">{msg.role}</span>
          <span className="wf-alm-content">
            {typeof msg.content === "string"
              ? msg.content.length > 500
                ? msg.content.slice(0, 500) + "…"
                : msg.content
              : JSON.stringify(msg.content)}
          </span>
          {Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0 && (
            <div className="wf-alm-tools">
              {(msg.tool_calls as Array<{ function?: { name?: string } }>).map((tc, j) => (
                <span key={j} className="wf-alm-tool-badge">
                  {tc.function?.name || `tool_${j}`}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function StepInspector({
  step,
  stepRun,
  allStepRuns,
  allSteps,
  triggerPayload,
  variables,
  wfPath,
  onStepPatch,
  onExecute,
  onClose,
  executing,
}: StepInspectorProps) {
  const [samples, setSamples] = useState<Record<string, StepSample>>({});
  const [showLog, setShowLog] = useState(false);
  const [genDesc, setGenDesc] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    if (!wfPath) return;
    getWorkflowSamples(wfPath).then((s) => setSamples(s.steps || {})).catch(() => {});
  }, [wfPath]);

  const previousOutputs = useMemo(() => {
    const map: Record<string, unknown> = {};
    const usedOutputs: Record<string, unknown> = {};
    for (const sr of allStepRuns) {
      if (sr.step_id === step.id) continue;
      if (sr.status === "completed" && sr.output !== undefined) {
        const cfg = allSteps.find((s) => s.id === sr.step_id);
        const key = cfg?.slug || sr.step_id;
        map[key] = sr.output;
        usedOutputs[sr.step_id] = true;
      }
    }
    for (const [sid, sample] of Object.entries(samples)) {
      if (sid === step.id || usedOutputs[sid]) continue;
      if (sample.output !== undefined) {
        const cfg = allSteps.find((s) => s.id === sid);
        const key = cfg?.slug || sample.slug || sid;
        map[key] = sample.output;
      }
    }
    return map;
  }, [allStepRuns, allSteps, step.id, samples]);

  const ownOutput = useMemo(() => {
    if (stepRun?.output !== undefined) return cleanOutput(stepRun.output);
    const sample = samples[step.id];
    if (sample?.output !== undefined) return cleanOutput(sample.output);
    return null;
  }, [stepRun, samples, step.id]);

  const ownInputResolved = useMemo(() => {
    if (stepRun?.input_resolved !== undefined && stepRun.input_resolved !== null) return stepRun.input_resolved;
    const sample = samples[step.id];
    return sample?.input_resolved || null;
  }, [stepRun, samples, step.id]);

  const agentSessionId = useMemo(() => {
    if (step.type !== "agent_session") return null;
    return extractSessionId(ownOutput);
  }, [step.type, ownOutput]);

  const stepRefs = useMemo(() =>
    allSteps.map((s) => ({
      slug: s.slug || slugify(s.name),
      name: s.name,
      type: s.type,
    })),
    [allSteps],
  );

  const getTemplateField = useCallback((): { value: string; onChange: (v: string) => void; label: string } => {
    switch (step.type) {
      case "tool_call":
        return {
          value: step.input ? JSON.stringify(step.input, null, 2) : "",
          onChange: (v) => { try { onStepPatch({ input: JSON.parse(v) }); } catch {} },
          label: "Input (JSON)",
        };
      case "agent_session":
        return {
          value: step.prompt || "",
          onChange: (v) => onStepPatch({ prompt: v }),
          label: "Prompt",
        };
      case "transform":
        return {
          value: step.template || "",
          onChange: (v) => onStepPatch({ template: v }),
          label: step.output_format === "script" ? "Script (Python)" : step.output_format === "llm" ? "Input (send to LLM)" : "Template",
        };
      case "http_request":
        return {
          value: step.body ? JSON.stringify(step.body, null, 2) : "",
          onChange: (v) => { try { onStepPatch({ body: JSON.parse(v) }); } catch {} },
          label: "Body (JSON)",
        };
      case "mcp_call":
        return {
          value: step.input ? JSON.stringify(step.input, null, 2) : "",
          onChange: (v) => { try { onStepPatch({ input: JSON.parse(v) }); } catch {} },
          label: "Input (JSON)",
        };
      case "condition":
        return {
          value: step.expression || "",
          onChange: (v) => onStepPatch({ expression: v }),
          label: "Expression",
        };
      case "return_step":
        return {
          value: step.response_template || "",
          onChange: (v) => onStepPatch({ response_template: v }),
          label: "Response Template",
        };
      default:
        return {
          value: step.input ? JSON.stringify(step.input, null, 2) : "",
          onChange: (v) => { try { onStepPatch({ input: JSON.parse(v) }); } catch {} },
          label: "Input (JSON)",
        };
    }
  }, [step, onStepPatch]);

  const templateField = getTemplateField();

  const handleGenerateScript = useCallback(async () => {
    if (!wfPath || !genDesc.trim()) return;
    setGenerating(true);
    try {
      const inputSchema: Record<string, unknown> = {};
      for (const [sid, sample] of Object.entries(samples)) {
        const cfg = allSteps.find((s) => s.id === sid);
        inputSchema[cfg?.slug || sid] = sample.output || {};
      }
      for (const sr of allStepRuns) {
        if (sr.status === "completed" && sr.output !== undefined) {
          const cfg = allSteps.find((s) => s.id === sr.step_id);
          inputSchema[cfg?.slug || sr.step_id] = sr.output;
        }
      }
      const { code } = await generateScript(wfPath, genDesc, inputSchema, Object.keys(triggerPayload));
      templateField.onChange(code);
      setGenDesc("");
    } catch (e: any) {
      console.error("Script generation failed:", e);
    } finally {
      setGenerating(false);
    }
  }, [wfPath, genDesc, samples, allSteps, allStepRuns, triggerPayload, templateField]);

  const allOutputsForPreview = useMemo(() => {
    const map: Record<string, unknown> = {};
    const usedOutputs: Record<string, unknown> = {};
    for (const sr of allStepRuns) {
      if (sr.status === "completed" && sr.output !== undefined) {
        const cfg = allSteps.find((s) => s.id === sr.step_id);
        const key = cfg?.slug || sr.step_id;
        map[key] = sr.output;
        usedOutputs[sr.step_id] = true;
      }
    }
    for (const [sid, sample] of Object.entries(samples)) {
      if (usedOutputs[sid]) continue;
      if (sample.output !== undefined) {
        const cfg = allSteps.find((s) => s.id === sid);
        const key = cfg?.slug || sample.slug || sid;
        map[key] = sample.output;
      }
    }
    return map;
  }, [allStepRuns, allSteps, samples]);

  const isFromSample = !stepRun && !!samples[step.id];

  return (
    <div className="wf-inspector-overlay" onClick={onClose}>
      <div className="wf-inspector-modal wf-inspector-3col" onClick={(e) => e.stopPropagation()}>
        <div className="wf-inspector-header">
          <span className="wf-inspector-title">{step.name || "Step"}</span>
          <span className="wf-inspector-type">{step.type.replace(/_/g, " ")}</span>
          {stepRun?.status && (
            <span className={`wf-inspector-status-badge wf-isb-${stepRun.status}`}>
              {stepRun.status}
            </span>
          )}
          {isFromSample && (
            <span className="wf-inspector-sample-badge">cached</span>
          )}
          <div className="wf-inspector-spacer" />
          <button
            className="wf-inspector-run-btn"
            onClick={onExecute}
            disabled={executing}
          >
            {executing ? "⏳ Running…" : "▶ Run Step"}
          </button>
          <button className="wf-inspector-close" onClick={onClose}>✕</button>
        </div>

        <div className="wf-inspector-body">
          {/* LEFT: Previous data (draggable) */}
          <div className="wf-inspector-col-left">
            <div className="wf-inspector-data-section">
              <h4>Trigger Payload</h4>
              <DataTree
                data={triggerPayload}
                rootLabel="trigger"
                rootPath="trigger"
              />
            </div>

            {Object.keys(previousOutputs).length > 0 && (
              <div className="wf-inspector-data-section">
                <h4>Step Outputs</h4>
                <DataTree
                  data={previousOutputs}
                  rootLabel="steps"
                  rootPath="steps"
                />
              </div>
            )}
          </div>

          {/* MIDDLE: Editor (top) + Preview (bottom) */}
          <div className="wf-inspector-col-mid">
            <div className="wf-inspector-editor-panel">
              <label className="wf-inspector-editor-label">{templateField.label}</label>
              <TemplateInput
                value={templateField.value}
                onChange={templateField.onChange}
                steps={stepRefs}
                triggerKeys={Object.keys(triggerPayload)}
                varNames={Object.keys(variables)}
                multiline
                className="wf-inspector-editor"
                style={{ flex: 1, minHeight: 0 }}
                placeholder="Drag data from the left panel or type {{steps.myStep.result}}"
              />
            </div>
            {step.type === "agent_session" && (
              <div className="wf-inspector-format-row">
                <select
                  value={step.output_format || "text"}
                  onChange={(e) => onStepPatch({ output_format: e.target.value })}
                  className="wf-inspector-format-select"
                >
                  <option value="text">Text</option>
                  <option value="json">JSON</option>
                </select>
                {step.output_format === "json" && (
                  <TemplateInput
                    className="wf-inspector-schema-input"
                    value={step.output_schema || ""}
                    onChange={(val) => onStepPatch({ output_schema: val || undefined })}
                    steps={stepRefs}
                    multiline
                    minLines={2}
                    placeholder='Schema: {"key": "value"}'
                  />
                )}
              </div>
            )}
            {step.type === "transform" && step.output_format === "script" && (
              <div className="wf-inspector-gen-bar">
                <input
                  className="wf-inspector-gen-input"
                  value={genDesc}
                  onChange={(e) => setGenDesc(e.target.value)}
                  placeholder="Describe what the script should do..."
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleGenerateScript(); } }}
                />
                <button
                  className="wf-inspector-gen-btn"
                  onClick={handleGenerateScript}
                  disabled={generating || !genDesc.trim()}
                >
                  {generating ? "⏳" : "✨ Generate"}
                </button>
              </div>
            )}
            <div className="wf-inspector-preview-section">
              <label className="wf-inspector-editor-label">Preview</label>
              <PreviewPanel
                template={templateField.value}
                triggerPayload={triggerPayload}
                stepOutputs={allOutputsForPreview}
                variables={variables}
              />
            </div>
          </div>

          {/* RIGHT: Own output (read-only) */}
          <div className="wf-inspector-col-right">
            <div className="wf-inspector-col-right-header">
              <h4>Output</h4>
              {ownOutput !== null && <span className="wf-inspector-output-note">(read-only)</span>}
            </div>

            {ownInputResolved && (
              <div className="wf-inspector-data-section">
                <h4>Resolved Input</h4>
                <DataTree
                  data={ownInputResolved}
                  rootLabel="input"
                  rootPath="input"
                  draggable={false}
                />
              </div>
            )}

            {ownOutput ? (
              <div className="wf-inspector-data-section">
                <DataTree
                  data={ownOutput}
                  rootLabel={step.name || step.slug || step.id}
                  rootPath={`steps.${step.slug || step.id}`}
                  draggable={false}
                />
              </div>
            ) : (
              <div className="wf-inspector-no-output">
                Run this step to see output
              </div>
            )}

            {agentSessionId && (
              <div className="wf-inspector-data-section">
                <button
                  className="wf-agent-log-toggle"
                  onClick={() => setShowLog(!showLog)}
                >
                  {showLog ? "▾ Hide Log" : "▸ View Log"}
                </button>
                {showLog && <AgentLogPanel sessionId={agentSessionId} />}
              </div>
            )}

            {stepRun?.error && (
              <div className="wf-inspector-data-section">
                <h4>Error</h4>
                <pre className="wf-inspector-pre wf-debug-error">{stepRun.error}</pre>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
