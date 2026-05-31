import type { StepFormProps } from "./shared";
import TemplateInput from "../TemplateInput";

export default function FileReadForm({ step, onChangeStep, stepRefs, stepSchemas }: StepFormProps) {
  return (
    <>
      <div className="wf-field">
        <label>File Path</label>
        <TemplateInput
          value={step.file_read_path || ""}
          onChange={(v) => onChangeStep({ file_read_path: v })}
          steps={stepRefs}
          stepSchemas={stepSchemas}
          placeholder="/path/to/file.txt or ~/path"
        />
        <span className="wf-field-hint">
          {"Type {{ to insert: now, date, time, uuid, timestamp, steps.*, trigger.*, vars.*"}
        </span>
      </div>
    </>
  );
}
