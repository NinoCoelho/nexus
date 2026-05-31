import type { StepFormProps } from "./shared";
import TemplateInput from "../TemplateInput";

export default function FileSaveForm({ step, onChangeStep, stepRefs, stepSchemas }: StepFormProps) {
  return (
    <>
      <div className="wf-field">
        <label>File Path</label>
        <TemplateInput
          value={step.file_save_path || ""}
          onChange={(v) => onChangeStep({ file_save_path: v })}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          placeholder={"/output/{{date}}-{{uuid}}.txt"}
        />
        <span className="wf-field-hint">
          {"Type {{ to insert: now, date, time, uuid, timestamp, steps.*, trigger.*, vars.*"}
        </span>
      </div>

      <div className="wf-field">
        <label>Content</label>
        <TemplateInput
          value={step.file_save_content || ""}
          onChange={(v) => onChangeStep({ file_save_content: v })}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          multiline
          minLines={4}
          placeholder={"{{steps.myStep.result}}"}
        />
      </div>

      <div className="wf-field">
        <label>Mode</label>
        <select
          value={step.file_save_mode || "overwrite"}
          onChange={(e) => onChangeStep({ file_save_mode: e.target.value })}
        >
          <option value="overwrite">Overwrite</option>
          <option value="append">Append</option>
        </select>
      </div>
    </>
  );
}
