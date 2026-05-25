import { useState, useEffect } from "react";
import {
  listKanbanBoards,
  getVaultKanban,
  type KanbanBoardSummary,
  type KanbanLane,
} from "../../../api/kanban";
import TemplateInput from "../TemplateInput";
import { KANBAN_ACTIONS } from "./constants";
import type { StepFormProps } from "./shared";

export default function KanbanActionForm({
  step,
  onChangeStep,
  stepRefs,
  stepSchemas,
}: StepFormProps) {
  const [kanbanBoards, setKanbanBoards] = useState<KanbanBoardSummary[]>([]);
  const [kanbanLanes, setKanbanLanes] = useState<KanbanLane[]>([]);

  useEffect(() => {
    listKanbanBoards()
      .then((res) => setKanbanBoards(res.boards))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (step.board_path) {
      getVaultKanban(step.board_path)
        .then((board) => setKanbanLanes(board.lanes))
        .catch(() => setKanbanLanes([]));
    }
  }, [step.board_path]);

  return (
    <>
      <div className="wf-field">
        <label>Action</label>
        <select
          value={step.action || ""}
          onChange={(e) => onChangeStep({ action: e.target.value })}
        >
          <option value="">— select action —</option>
          {KANBAN_ACTIONS.map((a) => (
            <option key={a.value} value={a.value}>
              {a.label}
            </option>
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
            <option key={b.path} value={b.path}>
              {b.title || b.path}
            </option>
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

      {step.board_path &&
        (step.action === "add_card" || step.action === "move_card") && (
          <div className="wf-field">
            <label>Column</label>
            <select
              value={step.lane_id || ""}
              onChange={(e) => onChangeStep({ lane_id: e.target.value })}
            >
              <option value="">— select column —</option>
              {kanbanLanes.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.title}
                </option>
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
              value={
                step.input ? JSON.stringify(step.input, null, 2) : ""
              }
              onChange={(val) => {
                try {
                  onChangeStep({ input: JSON.parse(val) });
                } catch {}
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

      {step.board_path &&
        step.action === "update_card" &&
        step.row_data === undefined && (
          <div className="wf-field">
            <label>Updates (JSON)</label>
            <TemplateInput
              value="{}"
              onChange={(val) => {
                try {
                  onChangeStep({ row_data: JSON.parse(val) });
                } catch {}
              }}
              steps={stepRefs}
              stepSchemas={stepSchemas}
              multiline
              minLines={3}
              placeholder='{"priority": "high", "labels": ["urgent"]}'
            />
          </div>
        )}
      {step.board_path &&
        step.action === "update_card" &&
        step.row_data !== undefined && (
          <div className="wf-field">
            <label>Updates (JSON)</label>
            <TemplateInput
              value={JSON.stringify(step.row_data, null, 2)}
              onChange={(val) => {
                try {
                  onChangeStep({ row_data: JSON.parse(val) });
                } catch {}
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
  );
}
