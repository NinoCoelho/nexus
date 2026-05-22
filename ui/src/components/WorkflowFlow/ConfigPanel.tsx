import { useEffect, useState } from "react";
import type { StepConfig, StepType, TriggerConfig, TriggerType } from "../../types/workflow";
import { listMcpServers, type McpServerStatus } from "../../api/mcp";
import { listCredentials, type Credential } from "../../api/credentials";
import TemplateInput from "./TemplateInput";
import { slugify } from "./index";

const STEP_TYPES: { value: StepType; label: string }[] = [
  { value: "tool_call", label: "Tool Call" },
  { value: "agent_session", label: "Agent Session" },
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
  http_request: "🌐", transform: "🔄", delay: "⏱️", condition: "◇",
};

const TRIGGER_ICONS: Record<string, string> = {
  webhook: "🔗", fs_watch: "📁", schedule: "📅", manual: "👆", event: "📡",
};

const AUTH_TYPES = [
  { value: "none", label: "None" },
  { value: "apikey", label: "API Key" },
  { value: "basic", label: "Basic Auth" },
  { value: "oauth", label: "OAuth 2.0 Bearer" },
];

const API_KEY_LOCATIONS = [
  { value: "header", label: "Header" },
  { value: "query", label: "Query String" },
];

const TRANSFORM_MODES = [
  { value: "template", label: "Template" },
  { value: "llm", label: "LLM Transform" },
  { value: "script", label: "Script" },
];

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="wf-copy-btn"
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

export default function ConfigPanel({
  mode,
  step,
  trigger,
  allSteps,
  onChangeStep,
  onChangeTrigger,
  onDelete,
  onClose,
}: {
  mode: "step" | "trigger";
  step?: StepConfig;
  trigger?: TriggerConfig;
  allSteps?: StepConfig[];
  onChangeStep: (patch: Partial<StepConfig>) => void;
  onChangeTrigger: (patch: Partial<TriggerConfig>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const [mcpServers, setMcpServers] = useState<McpServerStatus[]>([]);
  const [mcpTools, setMcpTools] = useState<string[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);

  const stepRefs = (allSteps || []).map((s) => ({
    slug: s.slug || slugify(s.name),
    name: s.name,
    type: s.type,
  }));

  useEffect(() => {
    if (mode === "step" && step?.type === "mcp_call") {
      listMcpServers().then((servers) => {
        setMcpServers(servers);
        const selected = servers.find((s) => s.name === step.mcp_server);
        if (selected?.tools) setMcpTools(selected.tools);
      }).catch(() => {});
    }
  }, [mode, step?.type, step?.mcp_server]);

  useEffect(() => {
    if (mode === "step" && step?.type === "http_request") {
      listCredentials().then(setCredentials).catch(() => {});
    }
  }, [mode, step?.type]);

  const handleMcpServerChange = (name: string) => {
    const server = mcpServers.find((s) => s.name === name);
    setMcpTools(server?.tools || []);
    onChangeStep({ mcp_server: name, mcp_tool: "" });
  };

  if (mode === "trigger" && trigger) {
    const webhookUrl = trigger.token
      ? `${window.location.origin}/api/workflow/trigger/${trigger.token}`
      : null;

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
            <>
              <div className="wf-field">
                <label>Webhook Token</label>
                <input
                  value={trigger.token || ""}
                  readOnly
                  placeholder="Generated on save"
                />
              </div>
              {webhookUrl && (
                <div className="wf-field">
                  <label>Webhook URL</label>
                  <div className="wf-webhook-url-row">
                    <code className="wf-webhook-url">{webhookUrl}</code>
                    <CopyBtn text={webhookUrl} />
                  </div>
                </div>
              )}
            </>
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
    const isCondition = step.type === "condition";
    const transformMode = step.output_format === "llm" ? "llm"
      : step.output_format === "script" ? "script"
      : "template";

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
              <TemplateInput
                value={step.expression || ""}
                onChange={(val) => onChangeStep({ expression: val })}
                steps={stepRefs}
                multiline
                minLines={3}
                placeholder="trigger.amount > 0"
              />
            </div>
          ) : (
            <>
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
                    <TemplateInput
                      value={step.input ? JSON.stringify(step.input, null, 2) : ""}
                      onChange={(val) => {
                        try { onChangeStep({ input: JSON.parse(val) }); } catch {}
                      }}
                      steps={stepRefs}
                      multiline
                      minLines={3}
                      placeholder='{"path": "{{vars.dir}}/out.md"}'
                    />
                  </div>
                </>
              )}

              {step.type === "agent_session" && (
                <>
                  <div className="wf-field">
                    <label>Prompt</label>
                    <TemplateInput
                      value={step.prompt || ""}
                      onChange={(val) => onChangeStep({ prompt: val })}
                      steps={stepRefs}
                      multiline
                      minLines={4}
                      placeholder="Analyze: {{steps.prev.result}}"
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

              {step.type === "transform" && (
                <>
                  <div className="wf-field">
                    <label>Mode</label>
                    <select
                      value={transformMode}
                      onChange={(e) => {
                        const v = e.target.value;
                        onChangeStep({ output_format: v });
                      }}
                    >
                      {TRANSFORM_MODES.map((m) => (
                        <option key={m.value} value={m.value}>{m.label}</option>
                      ))}
                    </select>
                  </div>

                  {transformMode === "template" && (
                    <div className="wf-field">
                      <label>Template</label>
                      <TemplateInput
                        value={step.template || ""}
                        onChange={(val) => onChangeStep({ template: val })}
                        steps={stepRefs}
                        multiline
                        minLines={3}
                        placeholder="Hello {{vars.name}}! Result: {{steps.getData.result}}"
                      />
                    </div>
                  )}

                  {transformMode === "llm" && (
                    <>
                      <div className="wf-field">
                        <label>System Prompt</label>
                        <input
                          value={step.tool || ""}
                          onChange={(e) => onChangeStep({ tool: e.target.value })}
                          placeholder="Summarize the input in 3 bullet points"
                        />
                      </div>
                      <div className="wf-field">
                        <label>Input</label>
                        <TemplateInput
                          value={step.template || ""}
                          onChange={(val) => onChangeStep({ template: val })}
                          steps={stepRefs}
                          multiline
                          minLines={3}
                          placeholder="{{steps.prev.result}}"
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

                  {transformMode === "script" && (
                    <div className="wf-field">
                      <label>Script (Python)</label>
                      <textarea
                        value={step.template || ""}
                        onChange={(e) => onChangeStep({ template: e.target.value })}
                        placeholder={"# input is in `data` variable\nresult = data.upper()"}
                        style={{ fontFamily: "var(--font-mono)", fontSize: 11, minHeight: 80 }}
                      />
                    </div>
                  )}
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
                    <TemplateInput
                      value={step.url || ""}
                      onChange={(val) => onChangeStep({ url: val })}
                      steps={stepRefs}
                      placeholder="https://api.example.com/data"
                    />
                  </div>

                  <div className="wf-section-label">Authentication</div>

                  <div className="wf-field">
                    <label>Type</label>
                    <select
                      value={step.auth_type || "none"}
                      onChange={(e) => onChangeStep({ auth_type: e.target.value })}
                    >
                      {AUTH_TYPES.map((a) => (
                        <option key={a.value} value={a.value}>{a.label}</option>
                      ))}
                    </select>
                  </div>

                  {step.auth_type === "apikey" && (
                    <>
                      <div className="wf-field">
                        <label>Credential</label>
                        <select
                          value={step.auth_credential || ""}
                          onChange={(e) => onChangeStep({ auth_credential: e.target.value })}
                        >
                          <option value="">— select credential —</option>
                          {credentials.map((c) => (
                            <option key={c.name} value={c.name}>{c.name}</option>
                          ))}
                        </select>
                      </div>
                      <div className="wf-field">
                        <label>Location</label>
                        <select
                          value={step.auth_location || "header"}
                          onChange={(e) => onChangeStep({ auth_location: e.target.value })}
                        >
                          {API_KEY_LOCATIONS.map((l) => (
                            <option key={l.value} value={l.value}>{l.label}</option>
                          ))}
                        </select>
                      </div>
                      {(step.auth_location || "header") === "header" && (
                        <>
                          <div className="wf-field">
                            <label>Header Name</label>
                            <input
                              value={step.auth_header_name || ""}
                              onChange={(e) => onChangeStep({ auth_header_name: e.target.value })}
                              placeholder="Authorization"
                            />
                          </div>
                          <div className="wf-field">
                            <label>Prefix</label>
                            <input
                              value={step.auth_prefix || ""}
                              onChange={(e) => onChangeStep({ auth_prefix: e.target.value })}
                              placeholder="Bearer"
                            />
                          </div>
                        </>
                      )}
                      {(step.auth_location || "header") === "query" && (
                        <div className="wf-field">
                          <label>Query Param</label>
                          <input
                            value={step.auth_query_name || ""}
                            onChange={(e) => onChangeStep({ auth_query_name: e.target.value })}
                            placeholder="api_key"
                          />
                        </div>
                      )}
                    </>
                  )}

                  {step.auth_type === "basic" && (
                    <>
                      <div className="wf-field">
                        <label>Username</label>
                        <input
                          value={step.auth_username || ""}
                          onChange={(e) => onChangeStep({ auth_username: e.target.value })}
                          placeholder="user"
                        />
                      </div>
                      <div className="wf-field">
                        <label>Password Credential</label>
                        <select
                          value={step.auth_password_credential || ""}
                          onChange={(e) => onChangeStep({ auth_password_credential: e.target.value })}
                        >
                          <option value="">— select credential —</option>
                          {credentials.map((c) => (
                            <option key={c.name} value={c.name}>{c.name}</option>
                          ))}
                        </select>
                      </div>
                    </>
                  )}

                  {step.auth_type === "oauth" && (
                    <div className="wf-field">
                      <label>Token Credential</label>
                      <select
                        value={step.auth_credential || ""}
                        onChange={(e) => onChangeStep({ auth_credential: e.target.value })}
                      >
                        <option value="">— select credential —</option>
                        {credentials.map((c) => (
                          <option key={c.name} value={c.name}>{c.name}</option>
                        ))}
                      </select>
                    </div>
                  )}

                  <div className="wf-section-label">Custom Headers</div>
                  <div className="wf-field">
                    <label>Headers (JSON)</label>
                    <textarea
                      value={step.custom_headers ? JSON.stringify(step.custom_headers, null, 2) : ""}
                      onChange={(e) => {
                        try { onChangeStep({ custom_headers: JSON.parse(e.target.value) }); } catch {}
                      }}
                      placeholder='{"X-Custom": "value"}'
                      style={{ minHeight: 48 }}
                    />
                  </div>

                  <div className="wf-section-label">Body</div>
                  <div className="wf-field">
                    <label>Body (JSON)</label>
                    <TemplateInput
                      value={step.body ? JSON.stringify(step.body, null, 2) : ""}
                      onChange={(val) => {
                        try { onChangeStep({ body: JSON.parse(val) }); } catch {}
                      }}
                      steps={stepRefs}
                      multiline
                      minLines={3}
                      placeholder='{"key": "value"}'
                    />
                  </div>
                </>
              )}

              {step.type === "mcp_call" && (
                <>
                  <div className="wf-field">
                    <label>MCP Server</label>
                    <select
                      value={step.mcp_server || ""}
                      onChange={(e) => handleMcpServerChange(e.target.value)}
                    >
                      <option value="">— select server —</option>
                      {mcpServers.map((s) => (
                        <option key={s.name} value={s.name}>
                          {s.name} {s.connected ? "●" : "○"}
                        </option>
                      ))}
                    </select>
                  </div>
                  {step.mcp_server && (
                    <div className="wf-field">
                      <label>MCP Tool</label>
                      {mcpTools.length > 0 ? (
                        <select
                          value={step.mcp_tool || ""}
                          onChange={(e) => onChangeStep({ mcp_tool: e.target.value })}
                        >
                          <option value="">— select tool —</option>
                          {mcpTools.map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          value={step.mcp_tool || ""}
                          onChange={(e) => onChangeStep({ mcp_tool: e.target.value })}
                          placeholder="tool_name"
                        />
                      )}
                    </div>
                  )}
                  {step.mcp_server && step.mcp_tool && (
                    <div className="wf-field">
                      <label>Input (JSON)</label>
                      <TemplateInput
                        value={step.input ? JSON.stringify(step.input, null, 2) : ""}
                        onChange={(val) => {
                          try { onChangeStep({ input: JSON.parse(val) }); } catch {}
                        }}
                        steps={stepRefs}
                        multiline
                        minLines={3}
                        placeholder='{"arg": "value"}'
                      />
                    </div>
                  )}
                </>
              )}

              <hr className="wf-divider" />

              <div className="wf-field">
                <label>On Error</label>
                <select
                  value={step.on_error || "stop"}
                  onChange={(e) => onChangeStep({ on_error: e.target.value })}
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
                    onChange={(e) => onChangeStep({ retry_count: parseInt(e.target.value) || 0 })}
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
