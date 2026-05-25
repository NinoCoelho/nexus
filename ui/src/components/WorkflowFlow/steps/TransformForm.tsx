import type { Model } from "../../../api/models";
import TemplateInput from "../TemplateInput";
import { TRANSFORM_MODES } from "./constants";
import { ScriptGenerator, type StepFormProps } from "./shared";

interface TransformFormProps extends StepFormProps {
  availableModels: Model[];
}

export default function TransformForm({
  step,
  onChangeStep,
  stepRefs,
  stepSchemas,
  onOpenEditor,
  wfPath,
  availableModels,
}: TransformFormProps) {
  const transformMode =
    step.output_format === "llm"
      ? "llm"
      : step.output_format === "script"
        ? "script"
        : "template";

  return (
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
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
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
              onChange={(e) =>
                onChangeStep({
                  output_format:
                    e.target.value === "json" ? "json" : undefined,
                })
              }
            >
              <option value="text">Plain text</option>
              <option value="json">JSON (parses result as JSON)</option>
            </select>
          </div>
          <div className="wf-field">
            <label>Template</label>
            <button
              className="wf-open-editor-btn"
              onClick={onOpenEditor}
              title="Open template editor"
            >
              ✏️{" "}
              {step.template
                ? step.template.slice(0, 40) +
                  (step.template.length > 40 ? "…" : "")
                : "Edit template…"}
            </button>
          </div>
        </>
      )}

      {transformMode === "llm" && (
        <>
          <div className="wf-field">
            <label>Instructions</label>
            <TemplateInput
              value={step.llm_instructions || ""}
              onChange={(val) =>
                onChangeStep({ llm_instructions: val || undefined })
              }
              steps={stepRefs}
              stepSchemas={stepSchemas}
              multiline
              minLines={3}
              placeholder="Extract the name and email from the input. Return as JSON."
            />
          </div>
          <div className="wf-field">
            <label>Input</label>
            <button
              className="wf-open-editor-btn"
              onClick={onOpenEditor}
              title="Open input editor"
            >
              ✏️{" "}
              {step.template
                ? step.template.slice(0, 40) +
                  (step.template.length > 40 ? "…" : "")
                : "Edit input…"}
            </button>
          </div>
          <div className="wf-field">
            <label>Output Sample (optional)</label>
            <TemplateInput
              value={step.output_sample || ""}
              onChange={(val) =>
                onChangeStep({ output_sample: val || undefined })
              }
              steps={stepRefs}
              stepSchemas={stepSchemas}
              multiline
              minLines={2}
              placeholder='{"name": "John", "email": "john@example.com"}'
            />
          </div>
          <div className="wf-field">
            <label>Model</label>
            <select
              value={step.model || ""}
              onChange={(e) =>
                onChangeStep({ model: e.target.value || undefined })
              }
            >
              <option value="">Default</option>
              {availableModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.model_name} ({m.tier})
                </option>
              ))}
            </select>
          </div>
        </>
      )}

      {transformMode === "script" && (
        <>
          <div className="wf-field">
            <label>Script (Python)</label>
            <button
              className="wf-open-editor-btn"
              onClick={onOpenEditor}
              title="Open script editor"
            >
              ✏️{" "}
              {step.template
                ? step.template.slice(0, 40) +
                  (step.template.length > 40 ? "…" : "")
                : "Edit script…"}
            </button>
          </div>
          <ScriptGenerator
            wfPath={wfPath}
            onGenerated={(code) => onChangeStep({ template: code })}
          />
        </>
      )}
    </>
  );
}
