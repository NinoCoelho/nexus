import { useEffect, useState } from "react";
import type { StepConfig, StepType, TriggerConfig, ToolInfo, StepSchema } from "../../types/workflow";
import type { Model } from "../../api/models";
import { getWorkflowSchema, listWorkflowTools } from "../../api/workflows";
import { getModels } from "../../api/models";
import { slugify } from "./workflow-utils";
import TemplateInput from "./TemplateInput";
import { STEP_TYPES, STEP_ICONS } from "./steps/constants";
import { ToolCallFields, type StepFormProps } from "./steps/shared";
import TriggerConfigForm from "./steps/TriggerConfigForm";
import AgentSessionForm from "./steps/AgentSessionForm";
import TransformForm from "./steps/TransformForm";
import HttpRequestForm from "./steps/HttpRequestForm";
import McpCallForm from "./steps/McpCallForm";
import KanbanActionForm from "./steps/KanbanActionForm";
import TableActionForm from "./steps/TableActionForm";

export default function ConfigPanel({
  mode,
  step,
  trigger,
  allSteps,
  onChangeStep,
  onChangeTrigger,
  onDelete,
  onClose,
  onOpenEditor,
  wfPath,
}: {
  mode: "step" | "trigger";
  step?: StepConfig;
  trigger?: TriggerConfig;
  allSteps?: StepConfig[];
  onChangeStep: (patch: Partial<StepConfig>) => void;
  onChangeTrigger: (patch: Partial<TriggerConfig>) => void;
  onDelete: () => void;
  onClose: () => void;
  onOpenEditor?: () => void;
  wfPath?: string;
}) {
  const [stepSchemas, setStepSchemas] = useState<StepSchema[]>([]);
  const [availableModels, setAvailableModels] = useState<Model[]>([]);
  const [workflowTools, setWorkflowTools] = useState<ToolInfo[]>([]);

  useEffect(() => {
    if (wfPath) {
      getWorkflowSchema(wfPath)
        .then((data: any) => {
          const steps = data?.steps;
          if (steps && typeof steps === "object") {
            setStepSchemas(Object.values(steps) as StepSchema[]);
          }
        })
        .catch(() => {});
    }
  }, [wfPath]);

  useEffect(() => {
    getModels().then(setAvailableModels).catch(() => {});
  }, []);

  useEffect(() => {
    listWorkflowTools().then(setWorkflowTools).catch(() => {});
  }, []);

  const stepRefs = (allSteps || []).map((s) => ({
    slug: s.slug || slugify(s.name),
    name: s.name,
    type: s.type,
  }));

  const sharedProps: StepFormProps = {
    step: step!,
    onChangeStep,
    stepRefs,
    stepSchemas,
    onOpenEditor,
    wfPath,
  };

  if (mode === "trigger" && trigger) {
    return (
      <TriggerConfigForm
        trigger={trigger}
        onChangeTrigger={onChangeTrigger}
        onDelete={onDelete}
        onClose={onClose}
        wfPath={wfPath}
      />
    );
  }

  if (mode === "step" && step) {
    const isCondition = step.type === "condition";

    return (
      <div className="wf-config-panel">
        <div className="wf-config-panel-header">
          <span className="icon">{STEP_ICONS[step.type] || "⚙️"}</span>
          <span className="title">{step.name || "Step"}</span>
          <button className="close-btn" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="wf-config-panel-body">
          <div className="wf-field">
            <label>Name</label>
            <input
              value={step.name}
              onChange={(e) => {
                const name = e.target.value;
                onChangeStep({ name, slug: slugify(name) });
              }}
            />
          </div>

          <div className="wf-field">
            <label>Slug</label>
            <input
              value={step.slug || slugify(step.name)}
              onChange={(e) => onChangeStep({ slug: e.target.value })}
              placeholder="auto-generated from name"
              style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}
            />
          </div>

          {isCondition ? (
            <div className="wf-field">
              <label>Expression</label>
              <button
                className="wf-open-editor-btn"
                onClick={onOpenEditor}
                title="Open expression editor"
              >
                ✏️{" "}
                {step.expression
                  ? step.expression.slice(0, 40) +
                    (step.expression.length > 40 ? "…" : "")
                  : "Edit expression…"}
              </button>
            </div>
          ) : (
            <>
              <div className="wf-field">
                <label>Type</label>
                <select
                  value={step.type}
                  onChange={(e) =>
                    onChangeStep({ type: e.target.value as StepType })
                  }
                >
                  {STEP_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>

              <hr className="wf-divider" />

              {step.type === "tool_call" && (
                <ToolCallFields
                  step={step}
                  tools={workflowTools}
                  stepRefs={stepRefs}
                  stepSchemas={stepSchemas}
                  onChangeStep={onChangeStep}
                />
              )}

              {step.type === "agent_session" && (
                <AgentSessionForm
                  {...sharedProps}
                  availableModels={availableModels}
                />
              )}

              {step.type === "transform" && (
                <TransformForm
                  {...sharedProps}
                  availableModels={availableModels}
                />
              )}

              {step.type === "delay" && (
                <div className="wf-field">
                  <label>Duration (seconds)</label>
                  <input
                    type="number"
                    value={step.duration_seconds || 0}
                    min={0}
                    onChange={(e) =>
                      onChangeStep({
                        duration_seconds: parseInt(e.target.value) || 0,
                      })
                    }
                  />
                </div>
              )}

              {step.type === "http_request" && <HttpRequestForm {...sharedProps} />}

              {step.type === "mcp_call" && <McpCallForm {...sharedProps} />}

              {step.type === "kanban_action" && (
                <KanbanActionForm {...sharedProps} />
              )}

              {step.type === "table_action" && (
                <TableActionForm {...sharedProps} />
              )}

              {step.type === "return_step" && (
                <>
                  <div className="wf-section-label">Response</div>
                  <div className="wf-field">
                    <label>Response Template</label>
                    <TemplateInput
                      value={step.response_template || ""}
                      onChange={(v) =>
                        onChangeStep({ response_template: v })
                      }
                      steps={stepRefs}
                      stepSchemas={stepSchemas}
                      placeholder="{{trigger.body}}"
                    />
                  </div>
                  <div className="wf-field">
                    <label>Note</label>
                    <span
                      style={{ fontSize: 10, color: "var(--fg-dim)" }}
                    >
                      Only available for webhook triggers. The caller
                      receives the resolved template as the HTTP response.
                      Without this step, webhook callers get an instant 202
                      acknowledgement.
                    </span>
                  </div>
                </>
              )}

              <hr className="wf-divider" />

              <div className="wf-field">
                <label>On Error</label>
                <select
                  value={step.on_error || "stop"}
                  onChange={(e) =>
                    onChangeStep({ on_error: e.target.value })
                  }
                >
                  <option value="stop">Stop</option>
                  <option value="continue">Continue</option>
                </select>
              </div>

              <div className="wf-field-row">
                <div className="wf-field">
                  <label>Retries</label>
                  <input
                    type="number"
                    min={0}
                    value={step.retry_count || 0}
                    onChange={(e) =>
                      onChangeStep({
                        retry_count: parseInt(e.target.value) || 0,
                      })
                    }
                  />
                </div>
              </div>
            </>
          )}

          <button className="wf-delete-btn" onClick={onDelete}>
            {isCondition ? "Delete Condition" : "Delete Step"}
          </button>
        </div>
      </div>
    );
  }

  return null;
}
