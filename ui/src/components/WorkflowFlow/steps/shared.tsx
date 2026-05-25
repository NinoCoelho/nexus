import { useState, useMemo } from "react";
import type { StepConfig, ToolInfo, StepSchema } from "../../../types/workflow";
import { generateScript } from "../../../api/workflows";
import TemplateInput from "../TemplateInput";

export interface StepFormProps {
  step: StepConfig;
  onChangeStep: (patch: Partial<StepConfig>) => void;
  stepRefs: { slug: string; name: string; type: string }[];
  stepSchemas: StepSchema[];
  onOpenEditor?: () => void;
  wfPath?: string;
}

export function CopyBtn({ text }: { text: string }) {
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

export function ScriptGenerator({
  wfPath,
  onGenerated,
}: {
  wfPath?: string;
  onGenerated: (code: string) => void;
}) {
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
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            generate();
          }
        }}
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

export function ToolParamField({
  name,
  schema,
  required,
  value,
  stepRefs,
  stepSchemas,
  onChange,
}: {
  name: string;
  schema: {
    type?: string;
    description?: string;
    enum?: string[];
    items?: { type?: string };
    default?: unknown;
  };
  required: boolean;
  value: unknown;
  stepRefs: { slug: string; name: string; type: string }[];
  stepSchemas: StepSchema[];
  onChange: (value: unknown) => void;
}) {
  const label = (
    <>
      {name}
      {required && (
        <span style={{ color: "var(--accent, #f90)", marginLeft: 2 }}>*</span>
      )}
    </>
  );

  const enumOptions = schema.enum;
  const paramType = schema.type;

  if (enumOptions && enumOptions.length > 0) {
    return (
      <div className="wf-field">
        <label>{label}</label>
        <select
          value={typeof value === "string" ? value : ""}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">— select —</option>
          {enumOptions.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
        {schema.description && (
          <span className="wf-field-hint">{schema.description}</span>
        )}
      </div>
    );
  }

  if (paramType === "boolean") {
    return (
      <div className="wf-field">
        <label>{label}</label>
        <select
          value={
            value === true ? "true" : value === false ? "false" : ""
          }
          onChange={(e) => {
            if (e.target.value === "") onChange("");
            else onChange(e.target.value === "true");
          }}
        >
          <option value="">—</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
        {schema.description && (
          <span className="wf-field-hint">{schema.description}</span>
        )}
      </div>
    );
  }

  if (paramType === "number" || paramType === "integer") {
    return (
      <div className="wf-field">
        <label>{label}</label>
        <TemplateInput
          value={
            typeof value === "number"
              ? String(value)
              : typeof value === "string"
                ? value
                : ""
          }
          onChange={(v) => {
            if (v === "" || v.startsWith("{{")) onChange(v);
            else {
              const n = Number(v);
              onChange(isNaN(n) ? v : n);
            }
          }}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          placeholder="0"
        />
        {schema.description && (
          <span className="wf-field-hint">{schema.description}</span>
        )}
      </div>
    );
  }

  if (paramType === "array" || paramType === "object") {
    return (
      <div className="wf-field">
        <label>{label}</label>
        <TemplateInput
          value={
            value !== undefined && value !== ""
              ? typeof value === "string" &&
                !value.startsWith("{") &&
                !value.startsWith("[")
                ? value
                : JSON.stringify(value, null, 2)
              : ""
          }
          onChange={(v) => {
            if (v.startsWith("{{")) {
              onChange(v);
              return;
            }
            try {
              onChange(JSON.parse(v));
            } catch {
              onChange(v);
            }
          }}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          multiline
          minLines={2}
          placeholder={
            paramType === "array"
              ? '["item1", "item2"]'
              : '{"key": "value"}'
          }
        />
        {schema.description && (
          <span className="wf-field-hint">{schema.description}</span>
        )}
      </div>
    );
  }

  return (
    <div className="wf-field">
      <label>{label}</label>
      <TemplateInput
        value={
          typeof value === "string"
            ? value
            : value !== undefined
              ? JSON.stringify(value)
              : ""
        }
        onChange={(v) => onChange(v)}
        steps={stepRefs}
        stepSchemas={stepSchemas}
        placeholder={schema.description || name}
      />
      {schema.description && (
        <span className="wf-field-hint">{schema.description}</span>
      )}
    </div>
  );
}

export function ToolCallFields({
  step,
  tools,
  stepRefs,
  stepSchemas,
  onChangeStep,
}: {
  step: StepConfig;
  tools: ToolInfo[];
  stepRefs: { slug: string; name: string; type: string }[];
  stepSchemas: StepSchema[];
  onChangeStep: (patch: Partial<StepConfig>) => void;
}) {
  const selectedTool = useMemo(
    () => tools.find((t) => t.name === step.tool),
    [tools, step.tool],
  );

  const properties = useMemo(() => {
    if (!selectedTool?.parameters?.properties) return {};
    return selectedTool.parameters.properties;
  }, [selectedTool]);

  const requiredSet = useMemo(() => {
    if (!selectedTool?.parameters?.required) return new Set<string>();
    return new Set(selectedTool.parameters.required);
  }, [selectedTool]);

  const handleToolSelect = (name: string) => {
    const tool = tools.find((t) => t.name === name);
    if (!tool) {
      onChangeStep({ tool: name, input: undefined });
      return;
    }
    const props = tool.parameters?.properties;
    const hasStructuredSchema = props && Object.keys(props).length > 0;
    if (hasStructuredSchema) {
      const input: Record<string, unknown> = {};
      for (const [key, schema] of Object.entries(props)) {
        if (step.input && key in step.input) {
          input[key] = step.input[key];
        } else if (schema.default !== undefined) {
          input[key] = schema.default;
        } else {
          input[key] = "";
        }
      }
      onChangeStep({ tool: name, input });
    } else {
      onChangeStep({ tool: name, input: step.input || {} });
    }
  };

  const handleFieldChange = (key: string, value: unknown) => {
    const current = step.input || {};
    onChangeStep({ input: { ...current, [key]: value } });
  };

  const propEntries = useMemo(
    () =>
      Object.entries(properties) as [
        string,
        NonNullable<
          NonNullable<ToolInfo["parameters"]>["properties"]
        >[string],
      ][],
    [properties],
  );

  return (
    <>
      <div className="wf-field">
        <label>Tool</label>
        <select
          value={step.tool || ""}
          onChange={(e) => handleToolSelect(e.target.value)}
        >
          <option value="">— select tool —</option>
          {tools.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}
            </option>
          ))}
        </select>
        {selectedTool && (
          <span className="wf-field-hint">{selectedTool.description}</span>
        )}
      </div>

      {selectedTool && propEntries.length > 0 && (
        <>
          <div className="wf-section-label">Parameters</div>
          {propEntries.map(([key, schema]) => (
            <ToolParamField
              key={key}
              name={key}
              schema={schema}
              required={requiredSet.has(key)}
              value={(step.input as Record<string, unknown>)?.[key]}
              stepRefs={stepRefs}
              stepSchemas={stepSchemas}
              onChange={(v) => handleFieldChange(key, v)}
            />
          ))}
        </>
      )}

      {selectedTool && propEntries.length === 0 && (
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
            placeholder='{"key": "{{steps.prev.result}}"}'
          />
        </div>
      )}
    </>
  );
}
