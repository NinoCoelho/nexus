import { useCallback, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  type Node,
  type Edge,
  type Connection,
  type EdgeChange,
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
import { AddNode } from "./AddNode";
import ConfigPanel from "./ConfigPanel";
import "./WorkflowFlow.css";

const NODE_TYPES = {
  trigger: TriggerNode,
  step: StepNode,
  condition: ConditionNode,
  add: AddNode,
};

const STEP_PALETTE: { type: StepType; icon: string; tip: string }[] = [
  { type: "tool_call", icon: "🔧", tip: "Tool Call" },
  { type: "agent_session", icon: "🤖", tip: "Agent Session" },
  { type: "condition", icon: "◇", tip: "Condition" },
  { type: "transform", icon: "🔄", tip: "Transform" },
  { type: "delay", icon: "⏱", tip: "Delay" },
  { type: "http_request", icon: "🌐", tip: "HTTP Request" },
  { type: "mcp_call", icon: "🔌", tip: "MCP Call" },
];

const TRIGGER_PALETTE: { type: TriggerConfig["type"]; icon: string; tip: string }[] = [
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
    default: return "";
  }
}

const NODE_H = 56;
const COND_H = 90;
const V_GAP = 100;
const BRANCH_OFFSET = 240;

type PosMap = Record<string, { x: number; y: number }>;

function computeLayout(wf: WorkflowDef): PosMap {
  const pos: PosMap = {};
  const branchTargets = new Set<string>();

  for (const s of wf.steps) {
    if (s.type === "condition") {
      if (s.then_step) branchTargets.add(s.then_step);
      if (s.else_step) branchTargets.add(s.else_step);
    }
  }

  let y = 40;
  for (const t of wf.triggers) {
    pos[`trigger-${t.id}`] = { x: 300, y };
    y += NODE_H + V_GAP;
  }

  for (const s of wf.steps) {
    if (branchTargets.has(s.id)) continue;
    pos[`step-${s.id}`] = { x: 300, y };
    y += (s.type === "condition" ? COND_H : NODE_H) + V_GAP;

    if (s.type === "condition") {
      if (s.then_step) {
        pos[`step-${s.then_step}`] = { x: 300 - BRANCH_OFFSET, y };
      }
      if (s.else_step) {
        pos[`step-${s.else_step}`] = { x: 300 + BRANCH_OFFSET, y };
      }
      if (s.then_step || s.else_step) {
        y += NODE_H + V_GAP;
      }
    }
  }

  pos["add-node"] = { x: 370, y };
  return pos;
}

function buildNodes(
  wf: WorkflowDef,
  selectedId: string | null,
  pos: PosMap,
  addBranch: (stepId: string, branch: "then" | "else") => void,
  onAdd: (type: StepType) => void,
): Node[] {
  const nodes: Node[] = [];

  for (const t of wf.triggers) {
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
      },
    });
  }

  for (const s of wf.steps) {
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
          onAddBranch: addBranch,
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
        },
      });
    }
  }

  nodes.push({
    id: "add-node",
    type: "add",
    position: pos["add-node"] || { x: 370, y: 400 },
    data: { onAdd },
    draggable: false,
    selectable: false,
  });

  return nodes;
}

function buildEdges(wf: WorkflowDef, deletedEdges: Set<string>): Edge[] {
  const edges: Edge[] = [];
  const branchTargets = new Set<string>();

  for (const s of wf.steps) {
    if (s.type === "condition") {
      if (s.then_step) branchTargets.add(s.then_step);
      if (s.else_step) branchTargets.add(s.else_step);
    }
  }

  if (wf.triggers.length > 0 && wf.steps.length > 0) {
    const triggerId = `trigger-${wf.triggers[wf.triggers.length - 1].id}`;
    const firstStep = wf.steps.find((s) => !branchTargets.has(s.id));
    if (firstStep) {
      const id = `seq:${triggerId}:step-${firstStep.id}`;
      if (!deletedEdges.has(id)) {
        edges.push({
          id,
          source: triggerId,
          target: `step-${firstStep.id}`,
          style: { stroke: "var(--fg-dim)", strokeWidth: 1.5 },
        });
      }
    }
  }

  for (let i = 0; i < wf.steps.length; i++) {
    const s = wf.steps[i];
    if (s.type === "condition") {
      if (s.then_step) {
        const id = `branch:step-${s.id}:then:step-${s.then_step}`;
        if (!deletedEdges.has(id)) {
          edges.push({
            id,
            source: `step-${s.id}`,
            sourceHandle: "then",
            target: `step-${s.then_step}`,
            label: "true",
            labelStyle: { fontSize: 9, fontWeight: 700, fill: "var(--accent)" },
            labelBgStyle: { fill: "var(--bg-panel)", fillOpacity: 0.95 },
            labelBgPadding: [4, 3] as [number, number],
            style: { stroke: "var(--accent)", strokeWidth: 1.5 },
          });
        }
      }
      if (s.else_step) {
        const id = `branch:step-${s.id}:else:step-${s.else_step}`;
        if (!deletedEdges.has(id)) {
          edges.push({
            id,
            source: `step-${s.id}`,
            sourceHandle: "else",
            target: `step-${s.else_step}`,
            label: "false",
            labelStyle: { fontSize: 9, fontWeight: 700, fill: "var(--fg-dim)" },
            labelBgStyle: { fill: "var(--bg-panel)", fillOpacity: 0.95 },
            labelBgPadding: [4, 3] as [number, number],
            style: { stroke: "var(--fg-dim)", strokeWidth: 1.5, strokeDasharray: "4 3" },
          });
        }
      }
      continue;
    }

    for (let j = i + 1; j < wf.steps.length; j++) {
      const next = wf.steps[j];
      if (branchTargets.has(next.id)) continue;
      const id = `seq:step-${s.id}:step-${next.id}`;
      if (!deletedEdges.has(id)) {
        edges.push({
          id,
          source: `step-${s.id}`,
          target: `step-${next.id}`,
          animated: s.type === "delay",
          style: { stroke: "var(--fg-dim)", strokeWidth: 1.5 },
        });
      }
      break;
    }
  }

  return edges;
}

function Canvas({
  wf,
  onSave,
  insertStep,
}: {
  wf: WorkflowDef;
  onSave: (updated: WorkflowDef) => void;
  insertStep: (type: StepType, afterStepId: string | null) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [deletedEdges, setDeletedEdges] = useState<Set<string>>(new Set());
  const posRef = useRef<PosMap>({});
  const [layoutVer, setLayoutVer] = useState(0);
  const prevStepsLen = useRef(-1);
  const wfRef = useRef(wf);
  wfRef.current = wf;

  // Initialize or grow layout when steps change
  if (prevStepsLen.current !== wf.steps.length) {
    const grew = wf.steps.length > prevStepsLen.current && prevStepsLen.current >= 0;
    prevStepsLen.current = wf.steps.length;
    if (grew || Object.keys(posRef.current).length === 0) {
      const fresh = computeLayout(wf);
      // Keep user-dragged positions, only add new positions
      posRef.current = { ...fresh, ...posRef.current };
      setLayoutVer((v) => v + 1);
    }
    setDeletedEdges(new Set());
  }

  const addBranch = useCallback((conditionStepId: string, branch: "then" | "else") => {
    const cur = wfRef.current;
    const newId = uid();
    const newStep: StepConfig = { id: newId, name: `${branch} action`, type: "tool_call" };
    const patch = branch === "then" ? { then_step: newId } : { else_step: newId };
    onSave({
      ...cur,
      steps: cur.steps.map((s) => (s.id === conditionStepId ? { ...s, ...patch } : s)).concat([newStep]),
    });
    const condPos = posRef.current[`step-${conditionStepId}`];
    if (condPos) {
      const offsetX = branch === "then" ? -BRANCH_OFFSET : BRANCH_OFFSET;
      posRef.current[`step-${newId}`] = { x: condPos.x + offsetX, y: condPos.y + COND_H + V_GAP };
      setLayoutVer((v) => v + 1);
    }
  }, [onSave]);

  const nodes = useMemo(
    () => buildNodes(wf, selectedId, posRef.current, addBranch, (t) => insertStep(t, selectedId)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [wf, selectedId, addBranch, insertStep, layoutVer],
  );

  const edges = useMemo(
    () => buildEdges(wf, deletedEdges),
    [wf, deletedEdges],
  );

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    if (node.id === "add-node") return;
    setSelectedId((prev) => (prev === node.id ? null : node.id));
  }, []);

  const onPaneClick = useCallback(() => setSelectedId(null), []);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    for (const c of changes) {
      if (c.type === "position" && c.position && c.id) {
        posRef.current[c.id] = c.position;
      }
    }
  }, []);

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const handle = connection.sourceHandle;

      if (handle === "then" || handle === "else") {
        const stepId = connection.source.replace("step-", "");
        const targetId = connection.target.replace("step-", "");
        const patch = handle === "then" ? { then_step: targetId } : { else_step: targetId };
        onSave({
          ...wf,
          steps: wf.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)),
        });
      }

      const prefix = handle ? "branch" : "seq";
      const handlePart = handle ? `:${handle}` : "";
      const edgeId = `${prefix}:${connection.source}${handlePart}:${connection.target}`;
      setDeletedEdges((prev) => {
        const next = new Set(prev);
        next.delete(edgeId);
        return next;
      });
    },
    [wf, onSave],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      for (const change of changes) {
        if (change.type === "remove") {
          const edge = edges.find((e) => e.id === change.id);
          if (!edge) continue;

          if (edge.sourceHandle === "then" || edge.sourceHandle === "else") {
            const stepId = edge.source.replace("step-", "");
            const patch =
              edge.sourceHandle === "then" ? { then_step: undefined } : { else_step: undefined };
            onSave({
              ...wf,
              steps: wf.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)),
            });
          }

          setDeletedEdges((prev) => {
            const next = new Set(prev);
            next.add(change.id);
            return next;
          });
        }
      }
    },
    [edges, wf, onSave],
  );

  const selectedNode = selectedId ? nodes.find((n) => n.id === selectedId) : null;

  const handleDeleteStep = useCallback(() => {
    if (!selectedId?.startsWith("step-")) return;
    const stepId = selectedId.replace("step-", "");
    onSave({ ...wf, steps: wf.steps.filter((s) => s.id !== stepId) });
    setSelectedId(null);
  }, [selectedId, wf, onSave]);

  const handleDeleteTrigger = useCallback(() => {
    if (!selectedId?.startsWith("trigger-")) return;
    const triggerId = selectedId.replace("trigger-", "");
    onSave({ ...wf, triggers: wf.triggers.filter((t) => t.id !== triggerId) });
    setSelectedId(null);
  }, [selectedId, wf, onSave]);

  const handleChangeStep = useCallback(
    (patch: Partial<StepConfig>) => {
      if (!selectedId?.startsWith("step-")) return;
      const stepId = selectedId.replace("step-", "");
      onSave({
        ...wf,
        steps: wf.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)),
      });
    },
    [selectedId, wf, onSave],
  );

  const handleChangeTrigger = useCallback(
    (patch: Partial<TriggerConfig>) => {
      if (!selectedId?.startsWith("trigger-")) return;
      const triggerId = selectedId.replace("trigger-", "");
      onSave({
        ...wf,
        triggers: wf.triggers.map((t) => (t.id === triggerId ? { ...t, ...patch } : t)),
      });
    },
    [selectedId, wf, onSave],
  );

  const handleAddTrigger = useCallback(
    (type: TriggerConfig["type"]) => {
      onSave({ ...wf, triggers: [...wf.triggers, { id: uid(), type }] });
    },
    [wf, onSave],
  );

  const handleRearrange = useCallback(() => {
    posRef.current = computeLayout(wf);
    setDeletedEdges(new Set());
    setLayoutVer((v) => v + 1);
  }, [wf]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      const raw = e.dataTransfer.getData("application/wf-step");
      if (!raw) return;
      insertStep(raw as StepType, selectedId);
    },
    [insertStep, selectedId],
  );

  return (
    <div className="wf-flow-wrap">
      <div className="wf-palette">
        <button
          className="wf-palette-rearrange"
          title="Auto-arrange layout"
          onClick={handleRearrange}
        >
          ⇅
        </button>
        <div className="wf-palette-sep" />
        {STEP_PALETTE.map((p) => (
          <div
            key={p.type}
            className="wf-palette-item"
            title={selectedId ? `Insert ${p.tip} after selected` : `Add ${p.tip}`}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData("application/wf-step", p.type);
              e.dataTransfer.effectAllowed = "move";
            }}
            onClick={() => insertStep(p.type, selectedId)}
          >
            {p.icon}
          </div>
        ))}
        <div className="wf-palette-sep" />
        {TRIGGER_PALETTE.map((p) => (
          <div
            key={p.type}
            className="wf-palette-item"
            title={p.tip}
            onClick={() => handleAddTrigger(p.type)}
          >
            {p.icon}
          </div>
        ))}
      </div>

      <div className="wf-flow-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onDragOver={onDragOver}
          onDrop={onDrop}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
          minZoom={0.3}
          maxZoom={1.5}
          defaultEdgeOptions={{ type: "default" }}
          deleteKeyCode={["Backspace", "Delete"]}
        >
          <Background gap={20} size={0.6} color="var(--border-soft)" />
          <Controls showInteractive={false} />
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
      </div>

      {selectedNode && (
        <ConfigPanel
          mode={selectedNode.id.startsWith("trigger-") ? "trigger" : "step"}
          step={
            selectedNode.id.startsWith("step-")
              ? wf.steps.find((s) => s.id === selectedNode.id.replace("step-", ""))
              : undefined
          }
          trigger={
            selectedNode.id.startsWith("trigger-")
              ? wf.triggers.find((t) => t.id === selectedNode.id.replace("trigger-", ""))
              : undefined
          }
          allSteps={wf.steps}
          onChangeStep={handleChangeStep}
          onChangeTrigger={handleChangeTrigger}
          onDelete={selectedNode.id.startsWith("step-") ? handleDeleteStep : handleDeleteTrigger}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}

export default function WorkflowFlow({
  wf,
  onSave,
}: {
  wf: WorkflowDef;
  onSave: (updated: WorkflowDef) => void;
}) {
  const insertStep = useCallback(
    (type: StepType, afterStepId: string | null) => {
      const id = uid();
      const name = `${type.replace(/_/g, " ")} ${wf.steps.length + 1}`;
      const step: StepConfig = { id, name, slug: slugify(name), type };

      if (!afterStepId) {
        onSave({ ...wf, steps: [...wf.steps, step] });
        return;
      }

      const afterId = afterStepId.startsWith("step-") ? afterStepId.replace("step-", "") : null;
      if (!afterId) {
        onSave({ ...wf, steps: [...wf.steps, step] });
        return;
      }

      const idx = wf.steps.findIndex((s) => s.id === afterId);
      if (idx === -1) {
        onSave({ ...wf, steps: [...wf.steps, step] });
        return;
      }

      const newSteps = [...wf.steps];
      newSteps.splice(idx + 1, 0, step);
      onSave({ ...wf, steps: newSteps });
    },
    [wf, onSave],
  );

  return (
    <ReactFlowProvider>
      <Canvas wf={wf} onSave={onSave} insertStep={insertStep} />
    </ReactFlowProvider>
  );
}
