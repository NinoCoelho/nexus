import { useCallback, useEffect, useState } from "react";
import type {
  WorkflowDef,
  StepConfig,
  StepRun,
  StepRunStatus,
  TriggerConfig,
  RunDetail,
} from "../../types/workflow";
import * as wfApi from "../../api/workflows";

interface UseInteractiveRunParams {
  wfPath?: string;
  onFlushSave?: () => Promise<void>;
  wfRef: React.MutableRefObject<WorkflowDef>;
  latestStepsRef: React.MutableRefObject<StepConfig[]>;
  triggers: TriggerConfig[];
}

export function useInteractiveRun({
  wfPath,
  onFlushSave,
  wfRef,
  latestStepsRef,
  triggers,
}: UseInteractiveRunParams) {
  const [interactiveRunId, setInteractiveRunId] = useState<string | null>(null);
  const [stepRunMap, setStepRunMap] = useState<Record<string, StepRun>>({});
  const [triggerPayload, setTriggerPayload] = useState<Record<string, unknown>>({});
  const [condBranches, setCondBranches] = useState<Record<string, string>>({});
  const [executingStep, setExecutingStep] = useState<string | null>(null);
  const [inspectorStepId, setInspectorStepId] = useState<string | null>(null);
  const [showPayloadInput, setShowPayloadInput] = useState(false);
  const [payloadInputMode, setPayloadInputMode] = useState<"trigger" | "all">("trigger");
  const [payloadText, setPayloadText] = useState("{}");
  const [payloadFormat, setPayloadFormat] = useState<"json" | "plain" | "xml">("json");
  const [execError, setExecError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"design" | "monitor">("design");
  const [monitorDetail, setMonitorDetail] = useState<RunDetail | null>(null);
  const [monitorInspectStepId, setMonitorInspectStepId] = useState<string | null>(null);
  const [showTriggerTest, setShowTriggerTest] = useState(false);

  useEffect(() => {
    if (!wfPath) return;
    wfApi.getWorkflowSamples(wfPath).then((s) => {
      if (s.trigger_payload && Object.keys(s.trigger_payload).length > 0) {
        setPayloadText(JSON.stringify(s.trigger_payload, null, 2));
        setTriggerPayload(s.trigger_payload);
      }
      const stepSampleMap: Record<string, StepRun> = {};
      for (const [stepId, sample] of Object.entries(s.steps || {})) {
        if (sample.output !== undefined) {
          stepSampleMap[stepId] = {
            run_id: "__sample__",
            step_id: stepId,
            step_name: sample.slug || stepId,
            step_slug: sample.slug || "",
            step_type: "",
            status: "completed",
            input_resolved: sample.input_resolved as Record<string, unknown> | undefined,
            output: sample.output,
            started_at: "",
            finished_at: "",
          };
        }
      }
      if (Object.keys(stepSampleMap).length > 0) {
        setStepRunMap(stepSampleMap);
      }
    }).catch(() => {});
  }, [wfPath]);

  const startRun = useCallback(
    async (mode: "trigger" | "all") => {
      if (!wfPath) return;
      setExecError(null);
      if (onFlushSave) await onFlushSave();
      try {
        let payload = {};
        if (payloadFormat === "json") {
          try { payload = JSON.parse(payloadText); } catch {}
        }
        const result = await wfApi.startInteractiveRun(
          wfPath, payload, mode, false,
          payloadFormat, payloadFormat !== "json" ? payloadText : "",
        );
        const resolvedPayload = payloadFormat !== "json" ? result.run?.trigger_payload || payload : payload;
        setInteractiveRunId(result.run.id);
        setTriggerPayload(resolvedPayload);
        setStepRunMap({});
        setCondBranches({});
        if (mode === "all") {
          const state = await wfApi.getInteractiveState(wfPath, result.run.id);
          const newMap: Record<string, StepRun> = {};
          for (const sr of state.steps) {
            newMap[sr.step_id] = sr;
          }
          setStepRunMap(newMap);
          setCondBranches(state.condition_branches || {});
        }
      } catch (e: any) {
        const msg = e?.message || String(e);
        setExecError(msg);
        console.error("Failed to start interactive run:", e);
      }
    },
    [wfPath, payloadText, payloadFormat, onFlushSave],
  );

  const handleTriggerRun = useCallback(() => {
    const trigger = triggers[0];
    if (trigger && trigger.type !== "manual") {
      setShowTriggerTest(true);
    } else {
      setShowPayloadInput(true);
      setPayloadInputMode("trigger");
    }
  }, [triggers]);

  const handleRunAll = useCallback(() => {
    setShowPayloadInput(true);
    setPayloadInputMode("all");
  }, []);

  const handleTriggerTestPayload = useCallback((payload: Record<string, unknown>) => {
    setShowTriggerTest(false);
    setPayloadText(JSON.stringify(payload, null, 2));
    setTriggerPayload(payload);
    if (!wfPath) return;
    (async () => {
      try {
        if (onFlushSave) await onFlushSave();
        const result = await wfApi.startInteractiveRun(wfPath, payload, "all", false, "json", "");
        setInteractiveRunId(result.run.id);
        setTriggerPayload(payload);
        setStepRunMap({});
        setCondBranches({});
        const state = await wfApi.getInteractiveState(wfPath, result.run.id);
        const newMap: Record<string, StepRun> = {};
        for (const sr of state.steps) {
          newMap[sr.step_id] = sr;
        }
        setStepRunMap(newMap);
        setCondBranches(state.condition_branches || {});
      } catch (e: any) {
        setExecError(e?.message || String(e));
      }
    })();
  }, [wfPath, onFlushSave]);

  const handlePayloadSubmit = useCallback(() => {
    setShowPayloadInput(false);
    startRun(payloadInputMode);
  }, [startRun, payloadInputMode]);

  const startSeededRun = useCallback(async (): Promise<string | null> => {
    if (!wfPath) return null;
    let payload = {};
    try { payload = JSON.parse(payloadText); } catch {}
    const result = await wfApi.startInteractiveRun(wfPath, payload, "trigger", true);
    const runId = result.run.id;
    setInteractiveRunId(runId);
    setTriggerPayload(payload);
    const state = await wfApi.getInteractiveState(wfPath, runId);
    const seededMap: Record<string, StepRun> = {};
    for (const sr of state.steps) {
      seededMap[sr.step_id] = sr;
    }
    setStepRunMap(seededMap);
    setCondBranches(state.condition_branches || {});
    return runId;
  }, [wfPath, payloadText]);

  const executeStep = useCallback(
    async (stepId: string) => {
      if (!wfPath) return;
      setExecError(null);
      setExecutingStep(stepId);
      try {
        if (onFlushSave) await onFlushSave();
        const currentStep = latestStepsRef.current.find((s) => s.id === stepId);
        const stepCfg = currentStep ? { ...currentStep } : undefined;
        let runId = interactiveRunId;
        if (!runId) {
          runId = (await startSeededRun()) || "";
        }
        let sr: StepRun;
        try {
          sr = await wfApi.interactiveExecuteStep(wfPath, runId, stepId, stepCfg);
        } catch (execErr: any) {
          const detail = execErr?.message || "";
          if (runId && (detail.includes("not found") || detail.includes("not reachable"))) {
            runId = (await startSeededRun()) || "";
            if (!runId) throw execErr;
            sr = await wfApi.interactiveExecuteStep(wfPath, runId, stepId, stepCfg);
          } else {
            throw execErr;
          }
        }
        setStepRunMap((prev) => ({ ...prev, [stepId]: sr }));
        const step = wfRef.current.steps.find((s) => s.id === stepId);
        if (sr.condition_branches && Object.keys(sr.condition_branches).length > 0) {
          setCondBranches((prev) => ({ ...prev, ...sr.condition_branches }));
        } else if (step?.type === "condition" && sr.status === "completed") {
          try {
            const state = await wfApi.getInteractiveState(wfPath, runId);
            setCondBranches(state.condition_branches || {});
          } catch {}
        }
      } catch (e: any) {
        const msg = e?.message || String(e);
        setExecError(msg);
        console.error("Failed to execute step:", e);
      } finally {
        setExecutingStep(null);
      }
    },
    [wfPath, interactiveRunId, startSeededRun, onFlushSave],
  );

  const handleOpenInspector = useCallback((stepId: string) => {
    setInspectorStepId(stepId);
  }, []);

  const handleSeedFromRun = useCallback(async (runId: string) => {
    if (!wfPath) return;
    try {
      const state = await wfApi.seedFromRun(wfPath, runId);
      setInteractiveRunId(state.run.id);
      setTriggerPayload(state.run.trigger_payload || {});
      const newMap: Record<string, StepRun> = {};
      for (const sr of state.steps) {
        newMap[sr.step_id] = sr;
      }
      setStepRunMap(newMap);
      setCondBranches(state.condition_branches || {});
      setActiveTab("design");
    } catch (e: any) {
      setExecError(e?.message || String(e));
    }
  }, [wfPath]);

  const getStepExecStatus = useCallback(
    (stepId: string): StepRunStatus | null => {
      if (executingStep === stepId) return "running";
      const sr = stepRunMap[stepId];
      return sr?.status || null;
    },
    [executingStep, stepRunMap],
  );

  const hasCompletedPrerequisites = useCallback(
    (stepId: string): boolean => {
      if (!interactiveRunId) return false;
      const steps = wfRef.current.steps;
      if (steps.length > 0 && steps[0].id === stepId) return true;
      for (const s of steps) {
        if (s.next_step === stepId || s.then_step === stepId || s.else_step === stepId) {
          if (s.type === "condition") {
            const branch = condBranches[s.id];
            if (branch === "then" && s.then_step === stepId) {
              return !!stepRunMap[s.id] && stepRunMap[s.id].status === "completed";
            }
            if (branch === "else" && s.else_step === stepId) {
              return !!stepRunMap[s.id] && stepRunMap[s.id].status === "completed";
            }
            return false;
          }
          return !!stepRunMap[s.id] && stepRunMap[s.id].status === "completed";
        }
      }
      return false;
    },
    [interactiveRunId, stepRunMap, condBranches],
  );

  return {
    interactiveRunId,
    setInteractiveRunId,
    stepRunMap,
    triggerPayload,
    condBranches,
    executingStep,
    execError,
    setExecError,
    inspectorStepId,
    setInspectorStepId,
    showPayloadInput,
    setShowPayloadInput,
    payloadInputMode,
    payloadText,
    setPayloadText,
    payloadFormat,
    setPayloadFormat,
    activeTab,
    setActiveTab,
    monitorDetail,
    setMonitorDetail,
    monitorInspectStepId,
    setMonitorInspectStepId,
    showTriggerTest,
    setShowTriggerTest,
    startRun,
    startSeededRun,
    executeStep,
    handleSeedFromRun,
    handleOpenInspector,
    handleTriggerRun,
    handleRunAll,
    handleTriggerTestPayload,
    handlePayloadSubmit,
    getStepExecStatus,
    hasCompletedPrerequisites,
  };
}
