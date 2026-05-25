import { useCallback, useState } from "react";
import type {
  WorkflowDef,
  StepConfig,
  StepType,
  TriggerConfig,
} from "../../types/workflow";
import { uid, slugify, insertStepAfter } from "./workflow-utils";

const MAX_UNDO = 5;

interface UseWorkflowCRUDParams {
  wfRef: React.MutableRefObject<WorkflowDef>;
  latestStepsRef: React.MutableRefObject<StepConfig[]>;
  onSave: (updated: WorkflowDef) => void;
}

export function useWorkflowCRUD({
  wfRef,
  latestStepsRef,
  onSave,
}: UseWorkflowCRUDParams) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [undoStack, setUndoStack] = useState<WorkflowDef[]>([]);
  const [deleteConfirm, setDeleteConfirm] = useState<{
    type: "step" | "trigger";
    id: string;
  } | null>(null);
  const [handlePicker, setHandlePicker] = useState<{
    sourceNodeId: string;
    sourceHandle: string;
    x: number;
    y: number;
  } | null>(null);

  const saveWithUndo = useCallback((next: WorkflowDef) => {
    setUndoStack((prev) => [...prev.slice(-(MAX_UNDO - 1)), wfRef.current]);
    latestStepsRef.current = next.steps;
    onSave(next);
  }, [onSave]);

  const handleUndo = useCallback(() => {
    setUndoStack((prev) => {
      if (prev.length === 0) return prev;
      const restored = prev[prev.length - 1];
      onSave(restored);
      return prev.slice(0, -1);
    });
  }, [onSave]);

  const addStep = useCallback((type: StepType | "trigger", insertAfter?: { stepId: string; branch?: "then" | "else" }) => {
    if (type === "trigger" || (typeof type === "string" && type.startsWith("trigger-"))) {
      const trigType = type === "trigger" ? "manual" as TriggerConfig["type"] : (type.replace("trigger-", "") as TriggerConfig["type"]);
      if (wfRef.current.triggers.length > 0) return;
      saveWithUndo({ ...wfRef.current, triggers: [{ id: uid(), type: trigType }] });
      return;
    }

    const stepType = type as StepType;
    const newId = uid();
    const name = `${stepType.replace(/_/g, " ")} ${wfRef.current.steps.length + 1}`;
    const step: StepConfig = { id: newId, name, slug: slugify(name), type: stepType };

    if (insertAfter?.branch) {
      const condId = insertAfter.stepId;
      const branch = insertAfter.branch;
      const branchField = branch === "then" ? "then_step" : "else_step";

      let steps = wfRef.current.steps.map((s) => ({ ...s }));
      const condIdx = steps.findIndex((s) => s.id === condId);
      if (condIdx === -1) return;

      const cond = steps[condIdx];
      const existingTarget = cond[branchField];
      if (existingTarget) {
        let chainEnd = existingTarget;
        while (true) {
          const chainStep = steps.find((s) => s.id === chainEnd);
          if (!chainStep || !chainStep.next_step) break;
          chainEnd = chainStep.next_step;
        }
        const chainEndStep = steps.find((s) => s.id === chainEnd);
        if (chainEndStep) {
          chainEndStep.next_step = newId;
        }
      } else {
        (cond as Record<string, unknown>)[branchField] = newId;
      }

      let insertIdx = condIdx + 1;
      const otherBranch = branch === "then" ? "else_step" : "then_step";
      const otherTarget = cond[otherBranch as keyof StepConfig] as string | undefined;
      if (otherTarget) {
        const otherTargetIdx = steps.findIndex((s) => s.id === otherTarget);
        if (otherTargetIdx !== -1 && otherTargetIdx > condIdx) {
          insertIdx = condIdx + 1;
        }
      }

      steps.splice(insertIdx, 0, step);
      steps = steps.map((s) =>
        s.id === condId ? { ...s, [branchField]: existingTarget || newId } : s
      );

      if (!existingTarget) {
        steps = steps.map((s) =>
          s.id === condId ? { ...s, [branchField]: newId } : s
        );
      }

      saveWithUndo({ ...wfRef.current, steps });
    } else if (insertAfter?.stepId) {
      const afterId = insertAfter.stepId;
      let steps = wfRef.current.steps.map((s) => ({ ...s }));
      const afterStep = steps.find((s) => s.id === afterId);
      if (!afterStep) return;

      step.next_step = afterStep.next_step;
      afterStep.next_step = newId;

      steps = insertStepAfter(steps, afterId, step);
      saveWithUndo({ ...wfRef.current, steps });
    } else {
      saveWithUndo({ ...wfRef.current, steps: [...wfRef.current.steps, step] });
    }
  }, [saveWithUndo]);

  const onAddFromHandle = useCallback(
    (nodeId: string, handleId: string, rect: DOMRect) => {
      const containerRect = document.querySelector(".wf-flow-canvas")?.getBoundingClientRect();
      const x = containerRect ? rect.left + rect.width / 2 - containerRect.left : rect.left;
      const y = containerRect ? rect.bottom + 4 - containerRect.top : rect.bottom + 4;
      setHandlePicker({ sourceNodeId: nodeId, sourceHandle: handleId, x, y });
    },
    [],
  );

  const handlePickerSelect = useCallback((type: StepType) => {
    if (!handlePicker) return;
    const { sourceNodeId, sourceHandle } = handlePicker;

    const newId = uid();
    const name = `${type.replace(/_/g, " ")} ${wfRef.current.steps.length + 1}`;
    const step: StepConfig = { id: newId, name, slug: slugify(name), type };

    if (sourceHandle === "then" || sourceHandle === "else") {
      const condId = sourceNodeId.replace("step-", "");
      const branchField = sourceHandle === "then" ? "then_step" : "else_step";

      let steps = wfRef.current.steps.map((s) => ({ ...s }));
      const condIdx = steps.findIndex((s) => s.id === condId);
      if (condIdx === -1) return;

      const cond = steps[condIdx];
      const existingTarget = cond[branchField] as string | undefined;

      if (existingTarget) {
        let chainEnd = existingTarget;
        while (true) {
          const chainStep = steps.find((s) => s.id === chainEnd);
          if (!chainStep || !chainStep.next_step) break;
          chainEnd = chainStep.next_step;
        }
        const chainEndStep = steps.find((s) => s.id === chainEnd);
        if (chainEndStep) {
          chainEndStep.next_step = newId;
        }
      }

      const branchPatch = existingTarget
        ? {}
        : { [branchField]: newId };

      let insertIdx = condIdx + 1;
      const otherField = sourceHandle === "then" ? "else_step" : "then_step";
      const otherTarget = cond[otherField as keyof StepConfig] as string | undefined;
      if (otherTarget) {
        const otherTargetIdx = steps.findIndex((s) => s.id === otherTarget);
        if (otherTargetIdx !== -1 && otherTargetIdx > condIdx) {
          insertIdx = condIdx + 1;
        }
      }

      steps.splice(insertIdx, 0, step);
      steps = steps.map((s) =>
        s.id === condId ? { ...s, ...branchPatch } : s
      );

      saveWithUndo({ ...wfRef.current, steps });
    } else if (sourceNodeId.startsWith("trigger-")) {
      if (wfRef.current.steps.length > 0) {
        step.next_step = wfRef.current.steps[0].id;
      }
      saveWithUndo({ ...wfRef.current, steps: [step, ...wfRef.current.steps] });
    } else {
      const afterId = sourceNodeId.replace("step-", "");
      let steps = wfRef.current.steps.map((s) => ({ ...s }));
      const afterStep = steps.find((s) => s.id === afterId);
      if (!afterStep) return;

      step.next_step = afterStep.next_step;
      afterStep.next_step = newId;
      steps = insertStepAfter(steps, afterId, step);
      saveWithUndo({ ...wfRef.current, steps });
    }
    setHandlePicker(null);
  }, [handlePicker, saveWithUndo]);

  const confirmDelete = useCallback(() => {
    if (!deleteConfirm) return;
    if (deleteConfirm.type === "step") {
      const stepId = deleteConfirm.id;
      saveWithUndo({
        ...wfRef.current,
        steps: wfRef.current.steps
          .filter((s) => s.id !== stepId)
          .map((s) => ({
            ...s,
            next_step: s.next_step === stepId ? undefined : s.next_step,
            then_step: s.then_step === stepId ? undefined : s.then_step,
            else_step: s.else_step === stepId ? undefined : s.else_step,
          })),
      });
    } else {
      saveWithUndo({ ...wfRef.current, triggers: [] });
    }
    setSelectedId(null);
    setDeleteConfirm(null);
  }, [deleteConfirm, saveWithUndo]);

  const handleDeleteStep = useCallback(() => {
    if (!selectedId?.startsWith("step-")) return;
    setDeleteConfirm({ type: "step", id: selectedId.replace("step-", "") });
  }, [selectedId]);

  const handleDeleteTrigger = useCallback(() => {
    if (!selectedId?.startsWith("trigger-")) return;
    setDeleteConfirm({ type: "trigger", id: selectedId.replace("trigger-", "") });
  }, [selectedId]);

  const handleChangeStep = useCallback(
    (patch: Partial<StepConfig>) => {
      if (!selectedId?.startsWith("step-")) return;
      const stepId = selectedId.replace("step-", "");
      saveWithUndo({
        ...wfRef.current,
        steps: wfRef.current.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)),
      });
    },
    [selectedId, saveWithUndo],
  );

  const handleChangeTrigger = useCallback(
    (patch: Partial<TriggerConfig>) => {
      if (!selectedId?.startsWith("trigger-")) return;
      const triggerId = selectedId.replace("trigger-", "");
      saveWithUndo({
        ...wfRef.current,
        triggers: wfRef.current.triggers.map((t) => (t.id === triggerId ? { ...t, ...patch } : t)),
      });
    },
    [selectedId, saveWithUndo],
  );

  return {
    selectedId,
    setSelectedId,
    undoStack,
    deleteConfirm,
    setDeleteConfirm,
    handlePicker,
    setHandlePicker,
    saveWithUndo,
    handleUndo,
    addStep,
    onAddFromHandle,
    handlePickerSelect,
    confirmDelete,
    handleDeleteStep,
    handleDeleteTrigger,
    handleChangeStep,
    handleChangeTrigger,
  };
}
