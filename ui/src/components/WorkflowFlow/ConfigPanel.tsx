import type { StepConfig, StepType, TriggerConfig, TriggerType } from "../../types/workflow";

const STEP_TYPES: { value: StepType; label: string }[] = [
  { value: "tool_call", label: "Tool Call" },
  { value: "agent_session", label: "Agent Session" },
  { value: "condition", label: "Condition" },
  { value: "transform", label: "Transform" },
  { value: "delay", label: "Delay" },
  { value: "http_request", label: "HTTP Request" },
  { value: "mcp_call", label: "MCP Call" },
];

const TRIGGER_TYPES: { value: TriggerType; label: string }[] = [
  { value: "manual", label: "Manual" },
  { value: "webhook", label: "Webhook" },
  { value: "schedule", label: "Schedule" },
  { value: "fs_watch", label: "File Watch" },
  { value: "event", label: "Event" },
];

const STEP_ICONS: Record<string, string> = {
  tool_call: "🔧", agent_session: "🤖", mcp_call: "🔌",
  http_request: "🌐", condition: "🔀", transform: "🔄", delay: "⏱️",
};

const TRIGGER_ICONS: Record<string, string> = {
  webhook: "🔗", fs_watch: "📁", schedule: "📅", manual: "👆", event: "📡",
};

export default function ConfigPanel({
  mode,
  step,
  trigger,
  steps,
  onChangeStep,
  onChangeTrigger,
  onDelete,
  onClose,
}: {
  mode: "step" | "trigger";
  step?: StepConfig;
  trigger?: TriggerConfig;
  steps: StepConfig[];
  onChangeStep: (patch: Partial<StepConfig>) => void;
  onChangeTrigger: (patch: Partial<TriggerConfig>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  if (mode === "trigger" && trigger) {
    return (
      <div className="wf-config-panel">
        <div className="wf-config-panel-header">
          <span className="icon">{TRIGGER_ICONS[trigger.type] || "⚡"}</span>
          <span className="title">Trigger Config</span>
          <button className="close-btn" onClick={onClose}>✕</button>
        </div>
        <div className="wf-config-panel-body">
          <div className="wf-field">
            <label>Type</label>
            <select
              value={trigger.type}
              onChange={(e) => onChangeTrigger({ type: e.target.value as TriggerType })}
            >
              {TRIGGER_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          {trigger.type === "webhook" && (
            <div className="wf-field">
              <label>Webhook Token</label>
              <input
                value={trigger.token || ""}
                readOnly
                placeholder="Generated on save"
              />
            </div>
          )}

          {trigger.type === "schedule" && (
            <div className="wf-field">
              <label>Cron Expression</label>
              <input
                value={trigger.cron || ""}
                onChange={(e) => onChangeTrigger({ cron: e.target.value })}
                placeholder="0 9 * * 1-5"
              />
            </div>
          )}

          {trigger.type === "fs_watch" && (
            <>
              <div className="wf-field">
                <label>Folder Path</label>
                <input
                  value={trigger.path || ""}
                  onChange={(e) => onChangeTrigger({ path: e.target.value })}
                  placeholder="~/Downloads"
                />
              </div>
              <div className="wf-field">
                <label>Glob Pattern</label>
                <input
                  value={trigger.pattern || "*"}
                  onChange={(e) => onChangeTrigger({ pattern: e.target.value })}
                  placeholder="*.pdf"
                />
              </div>
            </>
          )}

          {trigger.type === "event" && (
            <div className="wf-field">
              <label>Event Pattern</label>
              <input
                value={trigger.event || ""}
                onChange={(e) => onChangeTrigger({ event: e.target.value })}
                placeholder="vault.*"
              />
            </div>
          )}

          <button className="wf-delete-btn" onClick={onDelete}>Remove Trigger</button>
        </div>
      </div>
    );
  }

  if (mode === "step" && step) {
    return (
      <div className="wf-config-panel">
        <div className="wf-config-panel-header">
          <span className="icon">{STEP_ICONS[step.type] || "⚙️"}</span>
          <span className="title">{step.name || "Step"}</span>
          <button className="close-btn" onClick={onClose}>✕</button>
        </div>
        <div className="wf-config-panel-body">
          <div className="wf-field">
            <label>Name</label>
            <input
              value={step.name}
              onChange={(e) => onChangeStep({ name: e.target.value })}
            />
          </div>

          <div className="wf-field">
            <label>Type</label>
            <select
              value={step.type}
              onChange={(e) => onChangeStep({ type: e.target.value as StepType })}
            >
              {STEP_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          <hr className="wf-divider" />

          {step.type === "tool_call" && (
            <>
              <div className="wf-field">
                <label>Tool Name</label>
                <input
                  value={step.tool || ""}
                  onChange={(e) => onChangeStep({ tool: e.target.value })}
                  placeholder="vault_write"
                />
              </div>
              <div className="wf-field">
                <label>Input (JSON)</label>
                <textarea
                  value={step.input ? JSON.stringify(step.input, null, 2) : ""}
                  onChange={(e) => {
                    try { onChangeStep({ input: JSON.parse(e.target.value) }); } catch {}
                  }}
                  placeholder='{"path": "{{vars.dir}}/out.md"}'
                />
              </div>
            </>
          )}

          {step.type === "agent_session" && (
            <>
              <div className="wf-field">
                <label>Prompt</label>
                <textarea
                  value={step.prompt || ""}
                  onChange={(e) => onChangeStep({ prompt: e.target.value })}
                  placeholder="Analyze: {{steps.prev.result}}"
                  style={{ minHeight: 80 }}
                />
              </div>
              <div className="wf-field">
                <label>Model</label>
                <input
                  value={step.model || ""}
                  onChange={(e) => onChangeStep({ model: e.target.value || undefined })}
                  placeholder="fast"
                />
              </div>
            </>
          )}

          {step.type === "condition" && (
            <>
              <div className="wf-field">
                <label>Expression</label>
                <textarea
                  value={step.expression || ""}
                  onChange={(e) => onChangeStep({ expression: e.target.value })}
                  placeholder="trigger.amount > 0"
                />
              </div>
              <div className="wf-field">
                <label>Then → Step</label>
                <select
                  value={step.then_step || ""}
                  onChange={(e) => onChangeStep({ then_step: e.target.value || undefined })}
                >
                  <option value="">Next step</option>
                  {steps.filter((s) => s.id !== step.id).map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <div className="wf-field">
                <label>Else → Step</label>
                <select
                  value={step.else_step || ""}
                  onChange={(e) => onChangeStep({ else_step: e.target.value || undefined })}
                >
                  <option value="">Next step</option>
                  {steps.filter((s) => s.id !== step.id).map((s) => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>
            </>
          )}

          {step.type === "transform" && (
            <>
              <div className="wf-field">
                <label>Template</label>
                <textarea
                  value={step.template || ""}
                  onChange={(e) => onChangeStep({ template: e.target.value })}
                  placeholder="Hello {{vars.name}}!"
                />
              </div>
              <div className="wf-field">
                <label>Output Format</label>
                <select
                  value={step.output_format || "text"}
                  onChange={(e) => onChangeStep({ output_format: e.target.value })}
                >
                  <option value="text">Text</option>
                  <option value="json">JSON</option>
                </select>
              </div>
            </>
          )}

          {step.type === "delay" && (
            <div className="wf-field">
              <label>Duration (seconds)</label>
              <input
                type="number"
                value={step.duration_seconds || 0}
                min={0}
                onChange={(e) => onChangeStep({ duration_seconds: parseInt(e.target.value) || 0 })}
              />
            </div>
          )}

          {step.type === "http_request" && (
            <>
              <div className="wf-field-row">
                <div className="wf-field">
                  <label>Method</label>
                  <select value={step.method || "GET"} onChange={(e) => onChangeStep({ method: e.target.value })}>
                    {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
                      <option key={m}>{m}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="wf-field">
                <label>URL</label>
                <input
                  value={step.url || ""}
                  onChange={(e) => onChangeStep({ url: e.target.value })}
                  placeholder="https://api.example.com/data"
                />
              </div>
              <div className="wf-field">
                <label>Body (JSON)</label>
                <textarea
                  value={step.body ? JSON.stringify(step.body, null, 2) : ""}
                  onChange={(e) => {
                    try { onChangeStep({ body: JSON.parse(e.target.value) }); } catch {}
                  }}
                  placeholder='{"key": "value"}'
                />
              </div>
            </>
          )}

          {step.type === "mcp_call" && (
            <>
              <div className="wf-field">
                <label>MCP Server</label>
                <input
                  value={step.mcp_server || ""}
                  onChange={(e) => onChangeStep({ mcp_server: e.target.value })}
                />
              </div>
              <div className="wf-field">
                <label>MCP Tool</label>
                <input
                  value={step.mcp_tool || ""}
                  onChange={(e) => onChangeStep({ mcp_tool: e.target.value })}
                />
              </div>
            </>
          )}

          <hr className="wf-divider" />
          <div className="wf-section-label">Flow Control</div>

          <div className="wf-field">
            <label>Condition (skip if falsy)</label>
            <input
              value={step.condition || ""}
              onChange={(e) => onChangeStep({ condition: e.target.value || undefined })}
              placeholder="{{steps.prev.result}} == 'ok'"
            />
          </div>

          <div className="wf-field">
            <label>On Error</label>
            <select
              value={step.on_error || "stop"}
              onChange={(e) => onChangeStep({ on_error: e.target.value })}
            >
              <option value="stop">Stop</option>
              <option value="continue">Continue</option>
              {steps.filter((s) => s.id !== step.id).map((s) => (
                <option key={s.id} value={`goto:${s.id}`}>Goto: {s.name}</option>
              ))}
            </select>
          </div>

          <div className="wf-field-row">
            <div className="wf-field">
              <label>Retries</label>
              <input
                type="number"
                min={0}
                value={step.retry_count || 0}
                onChange={(e) => onChangeStep({ retry_count: parseInt(e.target.value) || 0 })}
              />
            </div>
          </div>

          <button className="wf-delete-btn" onClick={onDelete}>Delete Step</button>
        </div>
      </div>
    );
  }

  return null;
}
