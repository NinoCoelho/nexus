import { useState, useEffect } from "react";
import { listMcpServers, type McpServerStatus } from "../../../api/mcp";
import TemplateInput from "../TemplateInput";
import type { StepFormProps } from "./shared";

export default function McpCallForm({
  step,
  onChangeStep,
  stepRefs,
  stepSchemas,
}: StepFormProps) {
  const [mcpServers, setMcpServers] = useState<McpServerStatus[]>([]);
  const [mcpTools, setMcpTools] = useState<string[]>([]);

  useEffect(() => {
    listMcpServers()
      .then((servers) => {
        setMcpServers(servers);
        const selected = servers.find((s) => s.name === step.mcp_server);
        if (selected?.tools) setMcpTools(selected.tools);
      })
      .catch(() => {});
  }, [step.mcp_server]);

  const handleMcpServerChange = (name: string) => {
    const server = mcpServers.find((s) => s.name === name);
    setMcpTools(server?.tools || []);
    onChangeStep({ mcp_server: name, mcp_tool: "" });
  };

  return (
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
                <option key={t} value={t}>
                  {t}
                </option>
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
              try {
                onChangeStep({ input: JSON.parse(val) });
              } catch {}
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
  );
}
