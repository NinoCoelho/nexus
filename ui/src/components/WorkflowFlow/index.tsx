import { useCallback, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from "@xyflow/react";

import type {
  WorkflowDef,
  StepConfig,
  StepType,
  TriggerConfig,
} from "../../types/workflow";
import { TriggerNode } from "./TriggerNode";
import { StepNode } from "./StepNode";
import { AddNode } from "./AddNode";
import ConfigPanel from "./ConfigPanel";
import "./WorkflowFlow.css";

const NODE_TYPES = {
  trigger: TriggerNode,
  step: StepNode,
  add: AddNode,
};

const V_SPACING = 120;

function triggerSummary(t: TriggerConfig): string {
  switch (t.type) {
    case "webhook": return t.token ? `${t.token.slice(0, 8)}…` : "Pending token";
    case "schedule": return t.cron || "No cron set";
    case "fs_watch": return `${t.path || "?"}/${t.pattern || "*"}`;
    case "event": return t.event || "No event set";
    default: return "Manual";
  }
}

function stepSummary(s: StepConfig): string {
  switch (s.type) {
    case "tool_call": return s.tool || "—";
    case "agent_session": return (s.prompt || "—").slice(0, 50);
    case "condition": return (s.expression || "—").slice(0, 50);
    case "delay": return `${s.duration_seconds || 0}s`;
    case "transform": return (s.template || "—").slice(0, 50);
    case "http_request": return `${s.method || "GET"} ${(s.url || "—").slice(0, 30)}`;
    case "mcp_call": return `${s.mcp_server || "?"}/${s.mcp_tool || "?"}`;
    default: return "";
  }
}

function buildNodes(wf: WorkflowDef, selectedId: string | null, onAdd: (type: StepType) => void): Node[] {
  const nodes: Node[] = [];
  let y = 40;

  for (const t of wf.triggers) {
    nodes.push({
      id: `trigger-${t.id}`,
      type: "trigger",
      position: { x: 300, y },
      data: {
        triggerType: t.type,
        triggerId: t.id,
        label: t.type,
        detail: triggerSummary(t),
        selected: selectedId === `trigger-${t.id}`,
      },
      selectable: true,
      draggable: false,
    });
    y += V_SPACING;
  }

  for (let i = 0; i < wf.steps.length; i++) {
    const s = wf.steps[i];
    nodes.push({
      id: `step-${s.id}`,
      type: "step",
      position: { x: 300, y },
      data: {
        stepId: s.id,
        stepName: s.name,
        stepType: s.type,
        summary: stepSummary(s),
        condition: s.condition,
        selected: selectedId === `step-${s.id}`,
      },
      selectable: true,
    });
    y += V_SPACING;
  }

  nodes.push({
    id: "add-node",
    type: "add",
    position: { x: 300, y },
    data: { onAdd },
    draggable: false,
    selectable: false,
  });

  return nodes;
}

function buildEdges(wf: WorkflowDef): Edge[] {
  const edges: Edge[] = [];
  const ids: string[] = [];

  for (const t of wf.triggers) ids.push(`trigger-${t.id}`);
  for (const s of wf.steps) ids.push(`step-${s.id}`);

  for (let i = 0; i < ids.length - 1; i++) {
    const src = wf.steps[i];
    const animated = src?.type === "delay";
    edges.push({
      id: `e-${ids[i]}-${ids[i + 1]}`,
      source: ids[i],
      target: ids[i + 1],
      animated,
      style: { stroke: "var(--text-muted)" },
    });
  }

  edges.push({
    id: `e-${ids[ids.length - 1] || "start"}-add`,
    source: ids[ids.length - 1] || "start",
    target: "add-node",
    style: { stroke: "var(--border)", strokeDasharray: "4" },
  });

  return edges;
}

export default function WorkflowFlow({
  wf,
  onSave,
}: {
  wf: WorkflowDef;
  onSave: (updated: WorkflowDef) => void;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const addStep = useCallback((type: StepType) => {
    const id = Math.random().toString(36).substring(2, 10);
    const step: StepConfig = {
      id,
      name: `${type.replace(/_/g, " ")} ${wf.steps.length + 1}`,
      type,
    };
    onSave({ ...wf, steps: [...wf.steps, step] });
  }, [wf, onSave]);

  const nodes = useMemo(
    () => buildNodes(wf, selectedId, addStep),
    [wf, selectedId, addStep],
  );
  const edges = useMemo(() => buildEdges(wf), [wf]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    if (node.id === "add-node") return;
    setSelectedId((prev) => (prev === node.id ? null : node.id));
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedId(null);
  }, []);

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

  const handleChangeStep = useCallback((patch: Partial<StepConfig>) => {
    if (!selectedId?.startsWith("step-")) return;
    const stepId = selectedId.replace("step-", "");
    onSave({
      ...wf,
      steps: wf.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)),
    });
  }, [selectedId, wf, onSave]);

  const handleChangeTrigger = useCallback((patch: Partial<TriggerConfig>) => {
    if (!selectedId?.startsWith("trigger-")) return;
    const triggerId = selectedId.replace("trigger-", "");
    onSave({
      ...wf,
      triggers: wf.triggers.map((t) => (t.id === triggerId ? { ...t, ...patch } : t)),
    });
  }, [selectedId, wf, onSave]);

  const handleAddTrigger = useCallback((type: "webhook" | "schedule" | "fs_watch" | "event" | "manual") => {
    const t: TriggerConfig = { id: Math.random().toString(36).substring(2, 10), type };
    onSave({ ...wf, triggers: [...wf.triggers, t] });
  }, [wf, onSave]);

  return (
    <div className="wf-flow-wrap">
      <div className="wf-flow-canvas">
        <div className="wf-flow-toolbar">
          <button onClick={() => handleAddTrigger("manual")}>+ Trigger</button>
          <button onClick={() => handleAddTrigger("webhook")}>🔗 Webhook</button>
          <button onClick={() => handleAddTrigger("schedule")}>📅 Schedule</button>
          <button onClick={() => handleAddTrigger("fs_watch")}>📁 File Watch</button>
          <button onClick={() => handleAddTrigger("event")}>📡 Event</button>
        </div>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          proOptions={{ hideAttribution: true }}
          minZoom={0.3}
          maxZoom={1.5}
          defaultEdgeOptions={{ type: "default" }}
        >
          <Background gap={24} size={0.8} color="var(--border)" />
          <Controls showInteractive={false} />
          <MiniMap
            nodeColor={() => "var(--text-muted)"}
            maskColor="rgba(0,0,0,0.08)"
            style={{ background: "var(--bg)" }}
          />
        </ReactFlow>
      </div>

      {selectedNode && (
        <ConfigPanel
          mode={selectedNode.id.startsWith("trigger-") ? "trigger" : "step"}
          step={selectedNode.id.startsWith("step-")
            ? wf.steps.find((s) => s.id === selectedNode.id.replace("step-", ""))
            : undefined}
          trigger={selectedNode.id.startsWith("trigger-")
            ? wf.triggers.find((t) => t.id === selectedNode.id.replace("trigger-", ""))
            : undefined}
          steps={wf.steps}
          onChangeStep={handleChangeStep}
          onChangeTrigger={handleChangeTrigger}
          onDelete={selectedNode.id.startsWith("step-") ? handleDeleteStep : handleDeleteTrigger}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
