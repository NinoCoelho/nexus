import { useEffect, useState } from "react";
import type { StepConfig, StepType, TriggerConfig, TriggerType } from "../../types/workflow";
import type { StepSchema } from "../../types/workflow";
import { listMcpServers, type McpServerStatus } from "../../api/mcp";
import { listCredentials, type Credential } from "../../api/credentials";
import { listKanbanBoards, getVaultKanban, type KanbanBoardSummary, type KanbanLane } from "../../api/kanban";
import { listDatabases, listDatabaseTables, getVaultDataTable, type DatabaseSummary, type DatabaseTableSummary } from "../../api/datatable";
import { getModels, type Model } from "../../api/models";
import { getWorkflowSchema, generateScript } from "../../api/workflows";
import TemplateInput from "./TemplateInput";

function ScriptGenerator({ wfPath, onGenerated }: { wfPath?: string; onGenerated: (code: string) => void }) {
  const [desc, setDesc] = useState("");
  const [loading, setLoading] = useState(false);

  const generate = async () => {
    if (!wfPath || !desc.trim()) return;
    setLoading(true);
    try {
      const { code } = await generateScript(wfPath, desc);
      onGenerated(code);
      setDesc("");
    } catch (e) {
      console.error("Script generation failed:", e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="wf-inspector-gen-bar" style={{ padding: 0 }}>
      <input
        className="wf-inspector-gen-input"
        value={desc}
        onChange={(e) => setDesc(e.target.value)}
        placeholder="Describe what the script should do..."
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); generate(); } }}
      />
      <button
        className="wf-inspector-gen-btn"
        onClick={generate}
        disabled={loading || !desc.trim()}
      >
        {loading ? "⏳" : "✨ Generate"}
      </button>
    </div>
  );
}
import { slugify } from "./index";

const STEP_TYPES: { value: StepType; label: string }[] = [
  { value: "tool_call", label: "Tool Call" },
  { value: "agent_session", label: "Agent Session" },
  { value: "transform", label: "Transform" },
  { value: "delay", label: "Delay" },
  { value: "http_request", label: "HTTP Request" },
  { value: "mcp_call", label: "MCP Call" },
  { value: "kanban_action", label: "Kanban Action" },
  { value: "table_action", label: "App Table Action" },
  { value: "return_step", label: "Return" },
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
  kanban_action: "📋", table_action: "📊",
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
  { value: "template", label: "Template", desc: "Resolve {{...}} expressions into a string or JSON object" },
  { value: "llm", label: "LLM Transform", desc: "Send resolved input to an LLM for extraction, summarization, or reformatting" },
  { value: "script", label: "Script (Python)", desc: "Run a Python script with access to `data` (step outputs). Set `result` to return." },
];

const KANBAN_ACTIONS = [
  { value: "add_card", label: "Add Card" },
  { value: "move_card", label: "Move Card" },
  { value: "update_card", label: "Update Card" },
];

const TABLE_ACTIONS = [
  { value: "add_row", label: "Add Row" },
  { value: "update_row", label: "Update Row" },
  { value: "find_rows", label: "Find Rows" },
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
  wfPath?: string;
}) {
  const [mcpServers, setMcpServers] = useState<McpServerStatus[]>([]);
  const [mcpTools, setMcpTools] = useState<string[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [kanbanBoards, setKanbanBoards] = useState<KanbanBoardSummary[]>([]);
  const [kanbanLanes, setKanbanLanes] = useState<KanbanLane[]>([]);
  const [appDatabases, setAppDatabases] = useState<DatabaseSummary[]>([]);
  const [appTables, setAppTables] = useState<DatabaseTableSummary[]>([]);
  const [tableFields, setTableFields] = useState<{ name: string; kind: string }[]>([]);
  const [stepSchemas, setStepSchemas] = useState<StepSchema[]>([]);
  const [availableModels, setAvailableModels] = useState<Model[]>([]);

  useEffect(() => {
    if (wfPath) {
      getWorkflowSchema(wfPath).then((data: any) => {
        const steps = data?.steps;
        if (steps && typeof steps === "object") {
          setStepSchemas(Object.values(steps) as StepSchema[]);
        }
      }).catch(() => {});
    }
  }, [wfPath]);

  useEffect(() => {
    getModels().then(setAvailableModels).catch(() => {});
  }, []);

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

  useEffect(() => {
    if (mode === "step" && step?.type === "kanban_action") {
      listKanbanBoards().then((res) => setKanbanBoards(res.boards)).catch(() => {});
    }
  }, [mode, step?.type]);

  useEffect(() => {
    if (mode === "step" && step?.type === "kanban_action" && step.board_path) {
      getVaultKanban(step.board_path).then((board) => setKanbanLanes(board.lanes)).catch(() => setKanbanLanes([]));
    }
  }, [mode, step?.type, step?.board_path]);

  useEffect(() => {
    if (mode === "step" && step?.type === "table_action") {
      listDatabases().then((res) => setAppDatabases(res.databases)).catch(() => {});
    }
  }, [mode, step?.type]);

  useEffect(() => {
    if (mode === "step" && step?.type === "table_action" && step.table_path) {
      getVaultDataTable(step.table_path).then((t) => {
        setTableFields((t.schema?.fields || []).map((f: any) => ({ name: f.name, kind: f.kind })));
      }).catch(() => setTableFields([]));
    }
  }, [mode, step?.type, step?.table_path]);

  const handleMcpServerChange = (name: string) => {
    const server = mcpServers.find((s) => s.name === name);
    setMcpTools(server?.tools || []);
    onChangeStep({ mcp_server: name, mcp_tool: "" });
  };

  const handleDatabaseSelect = (folder: string) => {
    listDatabaseTables(folder).then((res) => setAppTables(res.tables)).catch(() => setAppTables([]));
  };

  if (mode === "trigger" && trigger) {
    const webhookUrl = trigger.token
      ? `${window.location.origin}/workflow/trigger/${trigger.token}`
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
                  stepSchemas={stepSchemas}
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
                  stepSchemas={stepSchemas}
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
                  stepSchemas={stepSchemas}
                      multiline
                      minLines={4}
                      placeholder="Analyze: {{steps.prev.result}}"
                    />
                  </div>
                  <div className="wf-field">
                    <label>Model</label>
                    <select
                      value={step.model || ""}
                      onChange={(e) => onChangeStep({ model: e.target.value || undefined })}
                    >
                      <option value="">Default</option>
                      {availableModels.map((m) => (
                        <option key={m.id} value={m.id}>{m.model_name} ({m.tier})</option>
                      ))}
                    </select>
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
                     <span className="wf-field-hint">
                       {TRANSFORM_MODES.find((m) => m.value === transformMode)?.desc}
                     </span>
                   </div>

                  {transformMode === "template" && (
                    <>
                      <div className="wf-field">
                        <label>Output Format</label>
                        <select
                          value={step.output_format === "json" ? "json" : "text"}
                          onChange={(e) => onChangeStep({ output_format: e.target.value === "json" ? "json" : undefined })}
                        >
                          <option value="text">Plain text</option>
                          <option value="json">JSON (parses result as JSON)</option>
                        </select>
                      </div>
                      <div className="wf-field">
                        <label>Template</label>
                        <TemplateInput
                          value={step.template || ""}
                          onChange={(val) => onChangeStep({ template: val })}
                          steps={stepRefs}
                  stepSchemas={stepSchemas}
                          multiline
                          minLines={3}
                          placeholder={"{{steps.prev.result}} processed by {{vars.name}}"}
                        />
                      </div>
                    </>
                  )}

                  {transformMode === "llm" && (
                    <>
                      <div className="wf-field">
                        <label>Instructions</label>
                        <textarea
                          value={step.llm_instructions || ""}
                          onChange={(e) => onChangeStep({ llm_instructions: e.target.value || undefined })}
                          placeholder="Extract the name and email from the input. Return as JSON."
                          style={{ fontFamily: "var(--font-mono)", fontSize: 11, minHeight: 60 }}
                        />
                      </div>
                      <div className="wf-field">
                        <label>Input</label>
                        <TemplateInput
                          value={step.template || ""}
                          onChange={(val) => onChangeStep({ template: val })}
                        steps={stepRefs}
                        stepSchemas={stepSchemas}
                        multiline
                        minLines={3}
                        placeholder="{{steps.prev.result}}"
                        />
                      </div>
                      <div className="wf-field">
                        <label>Output Sample (optional)</label>
                        <textarea
                          value={step.output_sample || ""}
                          onChange={(e) => onChangeStep({ output_sample: e.target.value || undefined })}
                          placeholder='{"name": "John", "email": "john@example.com"}'
                          style={{ fontFamily: "var(--font-mono)", fontSize: 11, minHeight: 40 }}
                        />
                      </div>
                      <div className="wf-field">
                        <label>Model</label>
                        <select
                          value={step.model || ""}
                          onChange={(e) => onChangeStep({ model: e.target.value || undefined })}
                        >
                          <option value="">Default</option>
                          {availableModels.map((m) => (
                            <option key={m.id} value={m.id}>{m.model_name} ({m.tier})</option>
                          ))}
                        </select>
                      </div>
                    </>
                  )}

                  {transformMode === "script" && (
                    <>
                      <div className="wf-field">
                        <label>Script (Python)</label>
                        <textarea
                          value={step.template || ""}
                          onChange={(e) => onChangeStep({ template: e.target.value })}
                          placeholder={"# input is in `data` variable\nresult = data.upper()"}
                          style={{ fontFamily: "var(--font-mono)", fontSize: 11, minHeight: 80 }}
                        />
                      </div>
                      <ScriptGenerator
                        wfPath={wfPath}
                        onGenerated={(code) => onChangeStep({ template: code })}
                      />
                    </>
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
                  stepSchemas={stepSchemas}
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
                  stepSchemas={stepSchemas}
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
                  stepSchemas={stepSchemas}
                        multiline
                        minLines={3}
                        placeholder='{"arg": "value"}'
                      />
                    </div>
                  )}
                </>
              )}

              {step.type === "kanban_action" && (
                <>
                  <div className="wf-field">
                    <label>Action</label>
                    <select
                      value={step.action || ""}
                      onChange={(e) => onChangeStep({ action: e.target.value })}
                    >
                      <option value="">— select action —</option>
                      {KANBAN_ACTIONS.map((a) => (
                        <option key={a.value} value={a.value}>{a.label}</option>
                      ))}
                    </select>
                  </div>

                  <div className="wf-field">
                    <label>Board</label>
                    <select
                      value={step.board_path || ""}
                      onChange={(e) => onChangeStep({ board_path: e.target.value })}
                    >
                      <option value="">— select board —</option>
                      {kanbanBoards.map((b) => (
                        <option key={b.path} value={b.path}>{b.title || b.path}</option>
                      ))}
                    </select>
                  </div>

                  {step.board_path && step.action !== "add_card" && (
                    <div className="wf-field">
                      <label>Card ID</label>
                      <TemplateInput
                        value={step.card_id || ""}
                        onChange={(val) => onChangeStep({ card_id: val })}
                        steps={stepRefs}
                  stepSchemas={stepSchemas}
                        placeholder="{{steps.prev.output.card_id}}"
                      />
                    </div>
                  )}

                  {step.board_path && (step.action === "add_card" || step.action === "move_card") && (
                    <div className="wf-field">
                      <label>Column</label>
                      <select
                        value={step.lane_id || ""}
                        onChange={(e) => onChangeStep({ lane_id: e.target.value })}
                      >
                        <option value="">— select column —</option>
                        {kanbanLanes.map((l) => (
                          <option key={l.id} value={l.id}>{l.title}</option>
                        ))}
                      </select>
                    </div>
                  )}

                  {step.board_path && step.action === "add_card" && (
                    <>
                      <div className="wf-field">
                        <label>Title</label>
                        <TemplateInput
                          value={step.template || ""}
                          onChange={(val) => onChangeStep({ template: val })}
                          steps={stepRefs}
                  stepSchemas={stepSchemas}
                          placeholder="{{trigger.body.title}}"
                        />
                      </div>
                      <div className="wf-field">
                        <label>Body (optional)</label>
                        <TemplateInput
                          value={step.input ? JSON.stringify(step.input, null, 2) : ""}
                          onChange={(val) => {
                            try { onChangeStep({ input: JSON.parse(val) }); } catch {}
                          }}
                          steps={stepRefs}
                  stepSchemas={stepSchemas}
                          multiline
                          minLines={2}
                          placeholder="{{trigger.body.description}}"
                        />
                      </div>
                    </>
                  )}

                  {step.board_path && step.action === "update_card" && step.row_data === undefined && (
                    <div className="wf-field">
                      <label>Updates (JSON)</label>
                      <TemplateInput
                        value="{}"
                        onChange={(val) => {
                          try { onChangeStep({ row_data: JSON.parse(val) }); } catch {}
                        }}
                        steps={stepRefs}
                  stepSchemas={stepSchemas}
                        multiline
                        minLines={3}
                        placeholder='{"priority": "high", "labels": ["urgent"]}'
                      />
                    </div>
                  )}
                  {step.board_path && step.action === "update_card" && step.row_data !== undefined && (
                    <div className="wf-field">
                      <label>Updates (JSON)</label>
                      <TemplateInput
                        value={JSON.stringify(step.row_data, null, 2)}
                        onChange={(val) => {
                          try { onChangeStep({ row_data: JSON.parse(val) }); } catch {}
                        }}
                        steps={stepRefs}
                  stepSchemas={stepSchemas}
                        multiline
                        minLines={3}
                        placeholder='{"priority": "high", "labels": ["urgent"]}'
                      />
                    </div>
                  )}
                </>
              )}

              {step.type === "table_action" && (
                <>
                  <div className="wf-field">
                    <label>Action</label>
                    <select
                      value={step.action || ""}
                      onChange={(e) => onChangeStep({ action: e.target.value })}
                    >
                      <option value="">— select action —</option>
                      {TABLE_ACTIONS.map((a) => (
                        <option key={a.value} value={a.value}>{a.label}</option>
                      ))}
                    </select>
                  </div>

                  <div className="wf-field">
                    <label>App</label>
                    <select
                      value=""
                      onChange={(e) => {
                        if (e.target.value) handleDatabaseSelect(e.target.value);
                      }}
                    >
                      <option value="">— select app —</option>
                      {appDatabases.map((d) => (
                        <option key={d.folder} value={d.folder}>{d.title || d.folder}</option>
                      ))}
                    </select>
                  </div>

                  <div className="wf-field">
                    <label>Table</label>
                    <select
                      value={step.table_path || ""}
                      onChange={(e) => onChangeStep({ table_path: e.target.value })}
                    >
                      <option value="">— select table —</option>
                      {appTables.map((t) => (
                        <option key={t.path} value={t.path}>{t.title || t.path}</option>
                      ))}
                    </select>
                  </div>

                  {step.table_path && (step.action === "add_row" || step.action === "update_row") && tableFields.length > 0 && (
                    <div className="wf-field">
                      <label>Row Data</label>
                      <TemplateInput
                        value={step.row_data ? JSON.stringify(step.row_data, null, 2) : JSON.stringify(Object.fromEntries(tableFields.map((f) => [f.name, ""])), null, 2)}
                        onChange={(val) => {
                          try { onChangeStep({ row_data: JSON.parse(val) }); } catch {}
                        }}
                        steps={stepRefs}
                  stepSchemas={stepSchemas}
                        multiline
                        minLines={4}
                        placeholder='{"field": "{{steps.prev.result}}"}'
                      />
                    </div>
                  )}

                  {step.table_path && step.action === "update_row" && (
                    <div className="wf-field">
                      <label>Row ID</label>
                      <TemplateInput
                        value={step.row_id || ""}
                        onChange={(val) => onChangeStep({ row_id: val })}
                        steps={stepRefs}
                  stepSchemas={stepSchemas}
                        placeholder="{{steps.prev.output._id}}"
                      />
                    </div>
                  )}

                  {step.table_path && step.action === "find_rows" && (
                    <div className="wf-field">
                      <label>Where (JSON)</label>
                      <TemplateInput
                        value={step.where ? JSON.stringify(step.where, null, 2) : "{}"}
                        onChange={(val) => {
                          try { onChangeStep({ where: JSON.parse(val) }); } catch {}
                        }}
                        steps={stepRefs}
                  stepSchemas={stepSchemas}
                        multiline
                        minLines={3}
                        placeholder='{"status": "open"}'
                      />
                    </div>
                  )}
                </>
              )}

              {step.type === "return_step" && (
                <>
                  <div className="wf-section-label">Response</div>
                  <div className="wf-field">
                    <label>Response Template</label>
                    <TemplateInput
                      value={step.response_template || ""}
                      onChange={(v) => onChangeStep({ response_template: v })}
                      steps={stepRefs}
                      stepSchemas={stepSchemas}
                      placeholder='{{trigger.body}}'
                    />
                  </div>
                  <div className="wf-field">
                    <label>Note</label>
                    <span style={{ fontSize: 10, color: "var(--fg-dim)" }}>
                      Only available for webhook triggers. The caller receives the resolved
                      template as the HTTP response. Without this step, webhook callers
                      get an instant 202 acknowledgement.
                    </span>
                  </div>
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
