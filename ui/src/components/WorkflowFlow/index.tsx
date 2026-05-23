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
  StepType,
  TriggerConfig,
} from "../../types/workflow";
import { TriggerNode } from "./TriggerNode";
import { StepNode } from "./StepNode";
import { ConditionNode } from "./ConditionNode";
import ConfigPanel from "./ConfigPanel";
import Modal from "../Modal";
import {
  migrateWorkflow,
  computeLayout,
  buildEdges,
} from "./workflow-layout";
import type { PosMap } from "./workflow-layout";
import "./WorkflowFlow.css";

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
  { type: "event", icon: "📡", tip: "Event" },
  { type: "manual", icon: "👆", tip: "Manual" },
];

function uid(): string {
  return Math.random().toString(36).substring(2, 10);
}

export function slugify(name: string): string {
  return name
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[\s_\-]+/)
    .map((w, i) => i === 0 ? w.toLowerCase() : w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join("");
}

function triggerSummary(t: TriggerConfig): string {
  switch (t.type) {
    case "webhook": return t.token ? `${t.token.slice(0, 8)}…` : "Pending";
    case "schedule": return t.cron || "—";
    case "fs_watch": return `${t.path || "?"}/${t.pattern || "*"}`;
    case "event": return t.event || "—";
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
    default: return "";
  }
}

const MAX_UNDO = 5;

function insertStepAfter(steps: StepConfig[], afterId: string, newStep: StepConfig): StepConfig[] {
  const result = [...steps];
  const idx = result.findIndex((s) => s.id === afterId);
  if (idx === -1) {
    result.push(newStep);
    return result;
  }
  result.splice(idx + 1, 0, newStep);
  return result;
}

function clearPredecessorTo(steps: StepConfig[], targetId: string): StepConfig[] {
  return steps.map((s) => {
    const patches: Partial<StepConfig> = {};
    if (s.next_step === targetId) patches.next_step = undefined;
    if (s.then_step === targetId) patches.then_step = undefined;
    if (s.else_step === targetId) patches.else_step = undefined;
    return Object.keys(patches).length > 0 ? { ...s, ...patches } : s;
  });
}

function moveAfter(steps: StepConfig[], afterId: string, targetId: string): StepConfig[] {
  const target = steps.find((s) => s.id === targetId);
  if (!target) return steps;
  const rest = steps.filter((s) => s.id !== targetId);
  const idx = rest.findIndex((s) => s.id === afterId);
  if (idx === -1) return [...rest, target];
  rest.splice(idx + 1, 0, target);
  return rest;
}

function Canvas({
  wf,
  onSave,
  wfPath,
}: {
  wf: WorkflowDef;
  onSave: (updated: WorkflowDef) => void;
  wfPath?: string;
}) {
  const rfInstance = useReactFlow();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const migratedWf = useMemo(() => migrateWorkflow(wf), [wf]);
  const wfRef = useRef(migratedWf);
  wfRef.current = migratedWf;

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

  const nodePosMap = useRef<PosMap>({});
  const prevFingerprint = useRef("");

  const saveWithUndo = useCallback((next: WorkflowDef) => {
    setUndoStack((prev) => [...prev.slice(-(MAX_UNDO - 1)), wfRef.current]);
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

  const fingerprint = `${migratedWf.triggers.map((t) => t.id).join(",")}|${migratedWf.steps.map((s) => `${s.id}:${s.next_step || ""}:${s.then_step || ""}:${s.else_step || ""}`).join(",")}`;

  if (fingerprint !== prevFingerprint.current) {
    nodePosMap.current = computeLayout(migratedWf);
    prevFingerprint.current = fingerprint;
  }

  const desiredNodes = useMemo(() => {
    const nodes: Node[] = [];
    const pos = nodePosMap.current;

    for (const t of migratedWf.triggers) {
      const id = `trigger-${t.id}`;
      nodes.push({
        id,
        type: "trigger",
        position: pos[id] || { x: 300, y: 40 },
        data: {
          triggerType: t.type,
          triggerId: t.id,
          label: t.type,
          detail: triggerSummary(t),
          selected: selectedId === id,
          onAddFromHandle,
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
      if (s.type === "condition") {
        nodes.push({
          ...base,
          type: "condition",
          data: {
            stepId: s.id,
            stepName: s.name,
            slug: s.slug || slugify(s.name),
            expression: s.expression || "",
            selected: selectedId === id,
            onAddFromHandle,
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
            selected: selectedId === id,
            onAddFromHandle,
          },
        });
      }
    }

    return nodes;
  }, [migratedWf, selectedId, fingerprint]);

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
        saveWithUndo({
          ...wfRef.current,
          steps: wfRef.current.steps.map((s) =>
            s.id === stepId ? { ...s, [field]: undefined } : s,
          ),
        });
      } else {
        const srcStepId = edge.source.replace("step-", "");
        saveWithUndo({
          ...wfRef.current,
          steps: wfRef.current.steps.map((s) =>
            s.id === srcStepId ? { ...s, next_step: undefined } : s,
          ),
        });
      }
    },
    [saveWithUndo],
  );

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedId((prev) => (prev === node.id ? null : node.id));
  }, []);

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
        saveWithUndo({
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
        saveWithUndo({ ...wfRef.current, steps });
        return;
      }

      const srcStepId = connection.source.replace("step-", "");

      let steps = clearPredecessorTo(wfRef.current.steps, targetId);
      steps = steps.map((s) =>
        s.id === srcStepId ? { ...s, next_step: targetId } : s,
      );
      steps = moveAfter(steps, srcStepId, targetId);

      saveWithUndo({ ...wfRef.current, steps });
    },
    [saveWithUndo],
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

  const selectedNode = selectedId ? controlledNodes.find((n) => n.id === selectedId) : null;

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

  return (
    <div className="wf-flow-wrap">
      <div className="wf-mini-toolbar">
        <button className="wf-tb-btn" title="Zoom in" onClick={() => rfInstance.zoomIn()}>＋</button>
        <button className="wf-tb-btn" title="Zoom out" onClick={() => rfInstance.zoomOut()}>－</button>
        <button className="wf-tb-btn" title="Fit view" onClick={() => rfInstance.fitView({ padding: 0.3 })}>⊞</button>
        <div className="wf-tb-sep" />
        <button className="wf-tb-btn" title="Auto-arrange layout" onClick={handleRearrange}>⇅</button>
        {undoStack.length > 0 && (
          <>
            <div className="wf-tb-sep" />
            <button className="wf-tb-btn" title="Undo" onClick={handleUndo}>↩</button>
          </>
        )}
      </div>

      <div className="wf-flow-canvas" style={{ position: "relative" }}>
        {migratedWf.triggers.length === 0 && (
          <button
            className="wf-empty-add"
            onClick={() => addStep("trigger")}
          >
            + Add Trigger
          </button>
        )}
        <ReactFlow
          nodes={controlledNodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          onPaneClick={() => { setSelectedId(null); setHandlePicker(null); }}
          onNodesChange={onNodesChange}
          onConnect={onConnect}
          connectionMode={ConnectionMode.Loose}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
          minZoom={0.3}
          maxZoom={1.5}
          defaultEdgeOptions={{ type: "deletable" }}
          deleteKeyCode={null}
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

        {handlePicker && (
          <div
            className="wf-connect-picker"
            style={{ left: handlePicker.x, top: handlePicker.y }}
          >
            {STEP_PALETTE.map((s) => (
              <button
                key={s.type}
                className="wf-connect-picker-item"
                onClick={() => handlePickerSelect(s.type)}
                title={s.desc}
              >
                <span className="wf-cpi-icon">{s.icon}</span>
                <span className="wf-cpi-label">{s.tip}</span>
              </button>
            ))}
          </div>
        )}
      </div>

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
          onChangeStep={handleChangeStep}
          onChangeTrigger={handleChangeTrigger}
          onDelete={selectedNode.id.startsWith("step-") ? handleDeleteStep : handleDeleteTrigger}
          onClose={() => setSelectedId(null)}
          wfPath={wfPath}
        />
      )}

      {deleteConfirm && (
        <Modal
          kind="confirm"
          danger
          title={`Delete ${deleteConfirm.type === "step" ? "Step" : "Trigger"}`}
          message={`Are you sure you want to delete this ${deleteConfirm.type}?`}
          onSubmit={confirmDelete}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </div>
  );
}

export default function WorkflowFlow({
  wf,
  onSave,
  wfPath,
}: {
  wf: WorkflowDef;
  onSave: (updated: WorkflowDef) => void;
  wfPath?: string;
}) {
  return (
    <ReactFlowProvider>
      <Canvas wf={wf} onSave={onSave} wfPath={wfPath} />
    </ReactFlowProvider>
  );
}
