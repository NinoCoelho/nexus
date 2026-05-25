import type { Model } from "../../../api/models";
import TemplateInput from "../TemplateInput";
import type { StepFormProps } from "./shared";

interface AgentSessionFormProps extends StepFormProps {
  availableModels: Model[];
}

export default function AgentSessionForm({
  step,
  onChangeStep,
  stepRefs,
  stepSchemas,
  onOpenEditor,
  availableModels,
}: AgentSessionFormProps) {
  return (
    <>
      <div className="wf-field">
        <label>Prompt</label>
        <button
          className="wf-open-editor-btn"
          onClick={onOpenEditor}
          title="Open prompt editor"
        >
          ✏️{" "}
          {step.prompt
            ? step.prompt.slice(0, 40) + (step.prompt.length > 40 ? "…" : "")
            : "Edit prompt…"}
        </button>
      </div>
      <div className="wf-field">
        <label>Model</label>
        <select
          value={step.model || ""}
          onChange={(e) => onChangeStep({ model: e.target.value || undefined })}
        >
          <option value="">Default</option>
          {availableModels.map((m) => (
            <option key={m.id} value={m.id}>
              {m.model_name} ({m.tier})
            </option>
          ))}
        </select>
      </div>
      <div className="wf-field">
        <label>Response Format</label>
        <select
          value={step.output_format || "text"}
          onChange={(e) => onChangeStep({ output_format: e.target.value })}
        >
          <option value="text">Text (free-form)</option>
          <option value="json">JSON (structured)</option>
        </select>
      </div>
      {step.output_format === "json" && (
        <div className="wf-field">
          <label>Output Schema</label>
          <TemplateInput
            value={step.output_schema || ""}
            onChange={(val) =>
              onChangeStep({ output_schema: val || undefined })
            }
            steps={stepRefs}
            stepSchemas={stepSchemas}
            multiline
            minLines={4}
            placeholder='{"key": "value", "count": 0}'
          />
          <span className="wf-field-hint">
            JSON example showing expected structure. LLM will match this shape.
          </span>
        </div>
      )}
    </>
  );
}
