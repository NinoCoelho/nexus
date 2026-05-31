import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  MiniMap,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  BaseEdge,
  getBezierPath,
  ConnectionMode,
  type Node,
  type Edge,
  type EdgeProps,
  type Connection,
  type NodeChange,
} from "@xyflow/react";

import type {
  WorkflowDef,
  StepConfig,
  StepRun,
  StepType,
  TriggerConfig,
} from "../../types/workflow";
import { TriggerNode } from "./TriggerNode";
import { StepNode } from "./StepNode";
import { ConditionNode } from "./ConditionNode";
import ConfigPanel from "./ConfigPanel";
import StepInspector from "./StepInspector";
import MonitorTab from "./MonitorTab";
import ExecutionInspector from "./ExecutionInspector";
import TriggerTestModal from "./TriggerTestModal";
import Modal from "../Modal";
import {
  migrateWorkflow,
  computeLayout,
  buildEdges,
} from "./workflow-layout";
import type { PosMap } from "./workflow-layout";
import { slugify, clearPredecessorTo, moveAfter } from "./workflow-utils";
import { useInteractiveRun } from "./useInteractiveRun";
import { useWorkflowCRUD } from "./useWorkflowCRUD";
import "./WorkflowFlow.css";

export { slugify } from "./workflow-utils";

const NODE_TYPES = {
  trigger: TriggerNode,
  step: StepNode,
  condition: ConditionNode,
};

function DeletableEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  label,
  labelStyle,
  labelBgStyle,
  labelBgPadding,
}: EdgeProps) {
  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  return (
    <BaseEdge
      path={edgePath}
      style={style}
      label={label}
      labelStyle={labelStyle}
      labelBgStyle={labelBgStyle}
      labelBgPadding={labelBgPadding}
    />
  );
}

const EDGE_TYPES = { deletable: DeletableEdge };

export const STEP_PALETTE: { type: StepType; icon: string; tip: string; desc: string }[] = [
  { type: "tool_call", icon: "🔧", tip: "Tool Call", desc: "Run a tool" },
  { type: "agent_session", icon: "🤖", tip: "Agent", desc: "LLM session" },
  { type: "condition", icon: "◇", tip: "Condition", desc: "Branch" },
  { type: "transform", icon: "🔄", tip: "Transform", desc: "Map data" },
  { type: "http_request", icon: "🌐", tip: "HTTP", desc: "Call API" },
  { type: "file_read", icon: "📖", tip: "File Read", desc: "Read file" },
  { type: "file_save", icon: "💾", tip: "File Save", desc: "Write file" },
  { type: "kanban_action", icon: "📋", tip: "Kanban", desc: "Board action" },
  { type: "table_action", icon: "📊", tip: "App Table", desc: "Table action" },
  { type: "mcp_call", icon: "🔌", tip: "MCP", desc: "MCP server" },
  { type: "delay", icon: "⏱", tip: "Delay", desc: "Wait" },
  { type: "return_step", icon: "↩", tip: "Return", desc: "Send response" },
];

export const TRIGGER_PALETTE: { type: TriggerConfig["type"]; icon: string; tip: string }[] = [
  { type: "webhook", icon: "🔗", tip: "Webhook" },
  { type: "schedule", icon: "📅", tip: "Schedule" },
  { type: "fs_watch", icon: "📁", tip: "File Watch" },
  { type: "rss", icon: "📰", tip: "RSS Feed" },
  { type: "event", icon: "📡", tip: "Event" },
  { type: "manual", icon: "👆", tip: "Manual" },
];

function triggerSummary(t: TriggerConfig): string {
  switch (t.type) {
    case "webhook": return t.token ? `${t.token.slice(0, 8)}…` : "Pending";
    case "schedule": return t.cron || "—";
    case "fs_watch": return `${t.path || "?"}/${t.pattern || "*"}`;
    case "event": return t.event || "—";
    case "rss": return t.rss_url ? new URL(t.rss_url).hostname : "—";
    default: return "Manual";
  }
}

function stepSummary(s: StepConfig): string {
  switch (s.type) {
    case "tool_call": return s.tool || "—";
    case "agent_session": return (s.prompt || "—").slice(0, 40);
    case "condition": return (s.expression || "—").slice(0, 40);
    case "delay": return `${s.duration_seconds || 0}s`;
    case "transform": return (s.template || "—").slice(0, 40);
    case "http_request": return `${s.method || "GET"} ${(s.url || "—").slice(0, 25)}`;
    case "mcp_call": return `${s.mcp_server || "?"}/${s.mcp_tool || "?"}`;
    case "kanban_action": return `${s.action || "—"} ${s.board_path || ""}`;
    case "table_action": return `${s.action || "—"} ${s.table_path || ""}`;
    case "return_step": return (s.response_template || "response").slice(0, 40);
    case "file_read": return (s.file_read_path || "—").slice(0, 40);
    case "file_save": return (s.file_save_path || "—").slice(0, 40);
    default: return "";
  }
}

function Canvas({
  wf,
  onSave,
  onFlushSave,
  wfPath,
}: {
  wf: WorkflowDef;
  onSave: (updated: WorkflowDef) => void;
  onFlushSave?: () => Promise<void>;
  wfPath?: string;
}) {
  const rfInstance = useReactFlow();

  const migratedWf = useMemo(() => migrateWorkflow(wf), [wf]);
  const wfRef = useRef(migratedWf);
  wfRef.current = migratedWf;
  const latestStepsRef = useRef(migratedWf.steps);
  latestStepsRef.current = migratedWf.steps;

  const nodePosMap = useRef<PosMap>({});
  const prevFingerprint = useRef("");

  const ir = useInteractiveRun({
    wfPath,
    onFlushSave,
    wfRef,
    latestStepsRef,
    triggers: migratedWf.triggers,
  });

  const crud = useWorkflowCRUD({
    wfRef,
    latestStepsRef,
    onSave,
  });

  const fingerprint = `${migratedWf.triggers.map((t) => t.id).join(",")}|${migratedWf.steps.map((s) => `${s.id}:${s.next_step || ""}:${s.then_step || ""}:${s.else_step || ""}`).join(",")}`;

  if (fingerprint !== prevFingerprint.current) {
    ir.setInteractiveRunId(null);
    nodePosMap.current = computeLayout(migratedWf);
    prevFingerprint.current = fingerprint;
  }

  const desiredNodes = useMemo(() => {
    const nodes: Node[] = [];
    const pos = nodePosMap.current;

    for (const t of migratedWf.triggers) {
      const id = `trigger-${t.id}`;
      const triggerExecuted = ir.activeTab === "monitor" && ir.monitorDetail
        ? ir.monitorDetail.steps.length > 0
        : false;
      nodes.push({
        id,
        type: "trigger",
        position: pos[id] || { x: 300, y: 40 },
        data: {
          triggerType: t.type,
          triggerId: t.id,
          label: t.type,
          detail: triggerSummary(t),
          selected: crud.selectedId === id,
          hasActiveRun: !!ir.interactiveRunId,
          monitorDimmed: ir.activeTab === "monitor" && !triggerExecuted,
          onRunTrigger: ir.handleTriggerRun,
          onRunAll: ir.handleRunAll,
          onAddFromHandle: crud.onAddFromHandle,
        },
      });
    }

    for (const s of migratedWf.steps) {
      const id = `step-${s.id}`;
      const base = {
        id,
        position: pos[id] || { x: 300, y: 200 },
        selectable: true,
        draggable: true,
      };
      const execStatus = ir.getStepExecStatus(s.id);
      const canRun = !!ir.interactiveRunId && !ir.executingStep && ir.hasCompletedPrerequisites(s.id);
      const branch = ir.condBranches[s.id];
      const histSr = (ir.activeTab === "monitor" ? ir.monitorDetail : null)?.steps.find((h) => h.step_id === s.id);
      const historyStatus = histSr?.status || null;
      const monitorExecuted = ir.activeTab === "monitor" && ir.monitorDetail
        ? ir.monitorDetail.steps.some((h) => h.step_id === s.id && (h.status === "completed" || h.status === "failed" || h.status === "running"))
        : false;
      const monitorDimmed = ir.activeTab === "monitor" && !monitorExecuted;

      if (s.type === "condition") {
        nodes.push({
          ...base,
          type: "condition",
          data: {
            stepId: s.id,
            stepName: s.name,
            slug: s.slug || slugify(s.name),
            expression: s.expression || "",
            selected: crud.selectedId === id,
            execStatus,
            conditionBranch: branch || null,
            canRun,
            historyStatus,
            historyOutput: histSr?.output,
            monitorExecuted,
            monitorDimmed,
            onRun: () => ir.executeStep(s.id),
            onOpenInspector: () => ir.handleOpenInspector(s.id),
            onAddFromHandle: crud.onAddFromHandle,
          },
        });
      } else {
        nodes.push({
          ...base,
          type: "step",
          data: {
            stepId: s.id,
            stepName: s.name,
            slug: s.slug || slugify(s.name),
            stepType: s.type,
            summary: stepSummary(s),
            selected: crud.selectedId === id,
            execStatus,
            canRun,
            executing: ir.executingStep === s.id,
            historyStatus,
            historyOutput: histSr?.output,
            monitorExecuted,
            monitorDimmed,
            onRun: () => ir.executeStep(s.id),
            onOpenInspector: () => ir.handleOpenInspector(s.id),
            onAddFromHandle: crud.onAddFromHandle,
          },
        });
      }
    }

    return nodes;
  }, [migratedWf, crud.selectedId, fingerprint, ir.interactiveRunId, ir.stepRunMap, ir.condBranches, ir.executingStep, ir.getStepExecStatus, ir.hasCompletedPrerequisites, ir.executeStep, ir.handleTriggerRun, ir.handleRunAll, ir.handleOpenInspector, crud.onAddFromHandle, ir.activeTab, ir.monitorDetail]);

  const [controlledNodes, setControlledNodes] = useState<Node[]>([]);

  useEffect(() => {
    setControlledNodes(desiredNodes);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desiredNodes]);

  const rawEdges = useMemo(() => buildEdges(migratedWf), [migratedWf]);

  const edges = useMemo(() => rawEdges, [rawEdges]);

  const onEdgeClick = useCallback(
    (_: React.MouseEvent, edge: Edge) => {
      if (edge.sourceHandle === "then" || edge.sourceHandle === "else") {
        const stepId = edge.source.replace("step-", "");
        const field = edge.sourceHandle === "then" ? "then_step" : "else_step";
        crud.saveWithUndo({
          ...wfRef.current,
          steps: wfRef.current.steps.map((s) =>
            s.id === stepId ? { ...s, [field]: undefined } : s,
          ),
        });
      } else {
        const srcStepId = edge.source.replace("step-", "");
        crud.saveWithUndo({
          ...wfRef.current,
          steps: wfRef.current.steps.map((s) =>
            s.id === srcStepId ? { ...s, next_step: undefined } : s,
          ),
        });
      }
    },
    [crud.saveWithUndo],
  );

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setControlledNodes((prev) => {
      const childMap = new Map<string, string[]>();
      for (const e of rawEdges) {
        const list = childMap.get(e.source) || [];
        list.push(e.target);
        childMap.set(e.source, list);
      }
      function getDescendants(nodeId: string): string[] {
        const result: string[] = [];
        const stack = childMap.get(nodeId) || [];
        for (const child of stack) {
          result.push(child);
          result.push(...getDescendants(child));
        }
        return result;
      }

      const dragDeltas = new Map<string, { dx: number; dy: number }>();
      for (const change of changes) {
        if (change.type !== "position" && change.type !== "dimensions") continue;
        if (change.type !== "position") continue;
        const node = prev.find((n) => n.id === change.id);
        if (!node || !change.position) continue;
        const dx = change.position.x - node.position.x;
        const dy = change.position.y - node.position.y;
        if (dx === 0 && dy === 0) continue;
        dragDeltas.set(change.id, { dx, dy });
      }

      const next = applyNodeChanges(changes, prev);

      const moved = new Set<string>();
      for (const [parentId, delta] of dragDeltas) {
        moved.add(parentId);
        const descendants = getDescendants(parentId);
        for (const descId of descendants) {
          if (moved.has(descId)) continue;
          moved.add(descId);
          const idx = next.findIndex((n) => n.id === descId);
          if (idx === -1) continue;
          next[idx] = {
            ...next[idx],
            position: {
              x: next[idx].position.x + delta.dx,
              y: next[idx].position.y + delta.dy,
            },
          };
        }
      }

      const pos = nodePosMap.current;
      for (const n of next) {
        pos[n.id] = { ...n.position };
      }
      return next;
    });
  }, [rawEdges]);

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const srcType = connection.source.startsWith("trigger-") ? "trigger" : "step";
      const tgtType = connection.target.startsWith("step-") ? "step" : null;
      if (tgtType !== "step") return;

      const targetId = connection.target.replace("step-", "");
      const handle = connection.sourceHandle;

      if (handle === "then" || handle === "else") {
        const stepId = connection.source.replace("step-", "");
        const patch = handle === "then" ? { then_step: targetId } : { else_step: targetId };
        crud.saveWithUndo({
          ...wfRef.current,
          steps: wfRef.current.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)),
        });
        return;
      }

      if (srcType === "trigger") {
        const targetStep = wfRef.current.steps.find((s) => s.id === targetId);
        if (!targetStep) return;
        const steps = wfRef.current.steps.filter((s) => s.id !== targetId);
        steps.unshift(targetStep);
        crud.saveWithUndo({ ...wfRef.current, steps });
        return;
      }

      const srcStepId = connection.source.replace("step-", "");

      let steps = clearPredecessorTo(wfRef.current.steps, targetId);
      steps = steps.map((s) =>
        s.id === srcStepId ? { ...s, next_step: targetId } : s,
      );
      steps = moveAfter(steps, srcStepId, targetId);

      crud.saveWithUndo({ ...wfRef.current, steps });
    },
    [crud.saveWithUndo],
  );

  const handleRearrange = useCallback(() => {
    const fresh = computeLayout(migratedWf);
    nodePosMap.current = fresh;
    setControlledNodes((prev) =>
      prev.map((n) => {
        const p = fresh[n.id];
        return p ? { ...n, position: p } : n;
      }),
    );
  }, [migratedWf]);

  const isMonitor = ir.activeTab === "monitor";

  useEffect(() => {
    if (!ir.monitorDetail || ir.monitorDetail.steps.length === 0) {
      ir.setMonitorInspectStepId(null);
      return;
    }
    const failed = ir.monitorDetail.steps.find((s) => s.status === "failed");
    ir.setMonitorInspectStepId((failed ?? ir.monitorDetail.steps[0]).step_id);
  }, [ir.monitorDetail]);

  const monitorInspectorStepRun = useMemo<StepRun | null>(() => {
    if (!ir.monitorInspectStepId || !ir.monitorDetail) return null;
    return ir.monitorDetail.steps.find((s) => s.step_id === ir.monitorInspectStepId) ?? null;
  }, [ir.monitorInspectStepId, ir.monitorDetail]);

  const monitorInspectorStepConfig = useMemo<StepConfig | undefined>(() => {
    if (!ir.monitorInspectStepId) return undefined;
    return migratedWf.steps.find((s) => s.id === ir.monitorInspectStepId);
  }, [ir.monitorInspectStepId, migratedWf.steps]);

  const monitorInspectorSlot = useMemo(() => {
    if (!monitorInspectorStepRun) return null;
    return (
      <ExecutionInspector
        stepRun={monitorInspectorStepRun}
        stepConfig={monitorInspectorStepConfig}
        onClose={() => ir.setMonitorInspectStepId(null)}
        onCopyToEditor={() => {
          if (ir.monitorDetail) ir.handleSeedFromRun(ir.monitorDetail.run.id);
        }}
      />
    );
  }, [monitorInspectorStepRun, monitorInspectorStepConfig, ir.setMonitorInspectStepId, ir.monitorDetail]);

  const handleMonitorNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    if (!isMonitor || !ir.monitorDetail) return;
    const stepId = node.id.startsWith("step-") ? node.id.replace("step-", "") : null;
    ir.setMonitorInspectStepId(stepId);
  }, [isMonitor, ir.monitorDetail, ir.setMonitorInspectStepId]);

  const onNodeClickHandler = useCallback((e: React.MouseEvent, node: Node) => {
    if (isMonitor) {
      handleMonitorNodeClick(e, node);
    } else {
      crud.setSelectedId((prev) => (prev === node.id ? null : node.id));
    }
  }, [isMonitor, handleMonitorNodeClick, crud.setSelectedId]);

  const monitorEdges = useMemo(() => {
    if (!isMonitor) return edges;
    if (!ir.monitorDetail || ir.monitorDetail.steps.length === 0) {
      return edges.map((e) => ({
        ...e,
        className: "wf-edge-exec-dimmed",
        style: { stroke: "var(--text-muted, #333)", opacity: 0.2, strokeDasharray: "5 5" },
      }));
    }
    const executedIds = new Set(
      ir.monitorDetail.steps
        .filter((s) => s.status === "completed" || s.status === "failed" || s.status === "running")
        .map((s) => `step-${s.step_id}`),
    );
    const executedSources = new Set(
      ir.monitorDetail.steps
        .filter((s) => s.status === "completed" || s.status === "running")
        .map((s) => s.step_id),
    );
    const triggerId = migratedWf.triggers[0]?.id;
    if (triggerId) {
      executedIds.add(`trigger-${triggerId}`);
    }
    return edges.map((e) => {
      const sourceExec = executedSources.has(e.source.replace("step-", "")) ||
        (e.source.startsWith("trigger-") && executedIds.has(e.source));
      const targetExec = executedIds.has(e.target);
      if (sourceExec && targetExec) {
        return { ...e, className: "wf-edge-exec-path", style: { stroke: "var(--success, #22c55e)", strokeWidth: 2 } };
      }
      return { ...e, className: "wf-edge-exec-dimmed", style: { stroke: "var(--text-muted, #333)", opacity: 0.2, strokeDasharray: "5 5" } };
    });
  }, [isMonitor, ir.monitorDetail, edges, migratedWf.triggers]);

  const activeEdges = isMonitor ? monitorEdges : edges;

  const selectedNode = crud.selectedId ? controlledNodes.find((n) => n.id === crud.selectedId) : null;

  const canvasEl = (
    <div className="wf-flow-canvas" style={{ position: "relative" }}>
      {migratedWf.triggers.length === 0 && !isMonitor && (
        <button
          className="wf-empty-add"
          onClick={() => crud.addStep("trigger")}
        >
          + Add Trigger
        </button>
      )}

      {ir.execError && (
        <div className="wf-exec-error-toast" onClick={() => ir.setExecError(null)}>
          <span className="wf-exec-error-msg">{ir.execError}</span>
          <button className="wf-exec-error-close">✕</button>
        </div>
      )}
      <ReactFlow
        nodes={controlledNodes}
        edges={activeEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodeClick={onNodeClickHandler}
        onEdgeClick={isMonitor ? () => {} : onEdgeClick}
        onPaneClick={() => {
          if (isMonitor) {
            ir.setMonitorInspectStepId(null);
          } else {
            crud.setSelectedId(null);
            crud.setHandlePicker(null);
          }
        }}
        onNodesChange={isMonitor ? () => {} : onNodesChange}
        onConnect={isMonitor ? () => {} : onConnect}
        connectionMode={ConnectionMode.Loose}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        maxZoom={1.5}
        defaultEdgeOptions={{ type: "deletable" }}
        deleteKeyCode={null}
        nodesDraggable={!isMonitor}
        nodesConnectable={!isMonitor}
        elementsSelectable={!isMonitor}
      >
        <Background gap={20} size={0.6} color="var(--border-soft)" />
        <MiniMap
          nodeColor={(n) => {
            if (n.type === "trigger") return "#5b8def";
            if (n.type === "condition") return "#e8a838";
            return "#8899aa";
          }}
          nodeStrokeWidth={2}
          nodeBorderRadius={4}
          maskColor="rgba(0,0,0,0.2)"
          style={{ background: "#1a1a2e", border: "1px solid #2a2a4a", borderRadius: 6 }}
        />
      </ReactFlow>

      {!isMonitor && crud.handlePicker && (
        <div
          className="wf-connect-picker"
          style={{ left: crud.handlePicker.x, top: crud.handlePicker.y }}
        >
          {STEP_PALETTE.map((s) => (
            <button
              key={s.type}
              className="wf-connect-picker-item"
              onClick={() => crud.handlePickerSelect(s.type)}
              title={s.desc}
            >
              <span className="wf-cpi-icon">{s.icon}</span>
              <span className="wf-cpi-label">{s.tip}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="wf-flow-wrap">
      <div className="wf-tab-bar">
        <button
          className={`wf-tab-item${ir.activeTab === "design" ? " active" : ""}`}
          onClick={() => ir.setActiveTab("design")}
        >
          Design
        </button>
        <button
          className={`wf-tab-item${ir.activeTab === "monitor" ? " active" : ""}`}
          onClick={() => ir.setActiveTab("monitor")}
        >
          Monitor
        </button>
        <div className="wf-tab-separator" />
        <button className="wf-tab-btn" title="Zoom in" onClick={() => rfInstance.zoomIn()}>＋</button>
        <button className="wf-tab-btn" title="Zoom out" onClick={() => rfInstance.zoomOut()}>－</button>
        <button className="wf-tab-btn" title="Fit view" onClick={() => rfInstance.fitView({ padding: 0.3 })}>⊞</button>
        <div className="wf-tb-sep" />
        <button className="wf-tab-btn" title="Auto-arrange layout" onClick={handleRearrange}>⇅</button>
        {ir.activeTab === "design" && crud.undoStack.length > 0 && (
          <>
            <div className="wf-tb-sep" />
            <button className="wf-tab-btn" title="Undo" onClick={crud.handleUndo}>↩</button>
          </>
        )}
      </div>

      {ir.activeTab === "design" ? (
        <div className="wf-design-row">
          {canvasEl}
          {selectedNode && (
            <ConfigPanel
              mode={selectedNode.id.startsWith("trigger-") ? "trigger" : "step"}
              step={
                selectedNode.id.startsWith("step-")
                  ? migratedWf.steps.find((s) => s.id === selectedNode.id.replace("step-", ""))
                  : undefined
              }
              trigger={
                selectedNode.id.startsWith("trigger-")
                  ? migratedWf.triggers.find((t) => t.id === selectedNode.id.replace("trigger-", ""))
                  : undefined
              }
              allSteps={migratedWf.steps}
              onChangeStep={crud.handleChangeStep}
              onChangeTrigger={crud.handleChangeTrigger}
              onDelete={selectedNode.id.startsWith("step-") ? crud.handleDeleteStep : crud.handleDeleteTrigger}
              onClose={() => crud.setSelectedId(null)}
              onOpenEditor={() => {
                const stepId = selectedNode.id.replace("step-", "");
                if (stepId) ir.handleOpenInspector(stepId);
              }}
              wfPath={wfPath}
            />
          )}

          {crud.deleteConfirm && (
            <Modal
              kind="confirm"
              danger
              title={`Delete ${crud.deleteConfirm.type === "step" ? "Step" : "Trigger"}`}
              message={`Are you sure you want to delete this ${crud.deleteConfirm.type}?`}
              onSubmit={crud.confirmDelete}
              onCancel={() => crud.setDeleteConfirm(null)}
            />
          )}

          {ir.showPayloadInput && (
            <div className="wf-payload-overlay" onClick={() => ir.setShowPayloadInput(false)}>
              <div className="wf-payload-modal" onClick={(e) => e.stopPropagation()}>
                <div className="wf-payload-header">
                  <span className="wf-payload-title">
                    {ir.payloadInputMode === "all" ? "Run All Steps" : "Run Trigger"}
                  </span>
                  <button className="wf-payload-close" onClick={() => ir.setShowPayloadInput(false)}>✕</button>
                </div>
                <div className="wf-payload-body">
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <label style={{ fontSize: 10, fontWeight: 600, color: "var(--fg-dim)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
                      Trigger Payload
                    </label>
                    <select
                      value={ir.payloadFormat}
                      onChange={(e) => ir.setPayloadFormat(e.target.value as "json" | "plain" | "xml")}
                      style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg)" }}
                    >
                      <option value="json">JSON</option>
                      <option value="plain">Plain Text</option>
                      <option value="xml">XML</option>
                    </select>
                  </div>
                  <textarea
                    value={ir.payloadText}
                    onChange={(e) => ir.setPayloadText(e.target.value)}
                    placeholder={ir.payloadFormat === "json" ? '{"key": "value"}' : ir.payloadFormat === "xml" ? "<root><item>value</item></root>" : "Enter text..."}
                  />
                  <div className="wf-payload-actions">
                    <button className="wf-payload-btn wf-payload-btn-cancel" onClick={() => ir.setShowPayloadInput(false)}>
                      Cancel
                    </button>
                    <button className="wf-payload-btn wf-payload-btn-run" onClick={ir.handlePayloadSubmit}>
                      {ir.payloadInputMode === "all" ? "⏩ Run All" : "▶ Run Trigger"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {ir.inspectorStepId && wfPath && (() => {
            const step = migratedWf.steps.find((s) => s.id === ir.inspectorStepId);
            if (!step) return null;
            const sr = ir.stepRunMap[ir.inspectorStepId] || null;
            return (
              <StepInspector
                step={step}
                stepRun={sr}
                allStepRuns={Object.values(ir.stepRunMap)}
                allSteps={migratedWf.steps}
                triggerPayload={ir.triggerPayload}
                variables={migratedWf.variables}
                wfPath={wfPath}
                onStepPatch={(patch) => {
                  if (!crud.selectedId) {
                    const nodeId = `step-${ir.inspectorStepId}`;
                    crud.setSelectedId(nodeId);
                  }
                  crud.saveWithUndo({
                    ...wfRef.current,
                    steps: wfRef.current.steps.map((s) =>
                      s.id === ir.inspectorStepId ? { ...s, ...patch } : s
                    ),
                  });
                }}
                onExecute={() => ir.executeStep(ir.inspectorStepId!)}
                onClose={() => ir.setInspectorStepId(null)}
                executing={ir.executingStep === ir.inspectorStepId}
              />
            );
          })()}

          {ir.showTriggerTest && wfPath && migratedWf.triggers[0] && (
            <TriggerTestModal
              wfPath={wfPath}
              triggerId={migratedWf.triggers[0].id}
              triggerType={migratedWf.triggers[0].type}
              triggerConfig={migratedWf.triggers[0]}
              onClose={() => ir.setShowTriggerTest(false)}
              onRunWithPayload={ir.handleTriggerTestPayload}
            />
          )}
        </div>
      ) : (
        <MonitorTab
          wfPath={wfPath || ""}
          onExecutionLoad={ir.setMonitorDetail}
          inspectorSlot={monitorInspectorSlot}
          hasMonitorDetail={!!ir.monitorDetail}
        >
          {canvasEl}
        </MonitorTab>
      )}
    </div>
  );
}

export default function WorkflowFlow({
  wf,
  onSave,
  onFlushSave,
  wfPath,
}: {
  wf: WorkflowDef;
  onSave: (updated: WorkflowDef) => void;
  onFlushSave?: () => Promise<void>;
  wfPath?: string;
}) {
  return (
    <ReactFlowProvider>
      <Canvas wf={wf} onSave={onSave} onFlushSave={onFlushSave} wfPath={wfPath} />
    </ReactFlowProvider>
  );
}
