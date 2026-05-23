import type { WorkflowDef, StepConfig } from "../../types/workflow";
import type { Edge } from "@xyflow/react";

export const ROW_H = 140;
export const CENTER_X = 400;
export const LANE_W = 220;

export type PosMap = Record<string, { x: number; y: number }>;

// --- Old heuristic functions (kept for migration only) ---

function _branchTargets(wf: { steps: StepConfig[] }): Set<string> {
  const targets = new Set<string>();
  for (const s of wf.steps) {
    if (s.type === "condition") {
      if (s.then_step) targets.add(s.then_step);
      if (s.else_step) targets.add(s.else_step);
    }
  }
  return targets;
}

function _getBranchChain(
  steps: StepConfig[],
  targetId: string,
  branchTargets: Set<string>,
  stopBefore = Infinity,
): string[] {
  const chain: string[] = [targetId];
  const startIdx = steps.findIndex((s) => s.id === targetId);
  if (startIdx === -1) return chain;
  let idx = startIdx + 1;
  while (idx < steps.length && idx < stopBefore) {
    if (branchTargets.has(steps[idx].id)) break;
    chain.push(steps[idx].id);
    idx++;
  }
  return chain;
}

function _scopeEnd(
  steps: StepConfig[],
  condition: StepConfig,
  branchTargets: Set<string>,
): number {
  let maxIdx = -1;
  if (condition.then_step) {
    const i = steps.findIndex((s) => s.id === condition.then_step);
    if (i > maxIdx) maxIdx = i;
  }
  if (condition.else_step) {
    const i = steps.findIndex((s) => s.id === condition.else_step);
    if (i > maxIdx) maxIdx = i;
  }
  for (let i = maxIdx + 1; i < steps.length; i++) {
    if (!branchTargets.has(steps[i].id)) return i;
  }
  return steps.length;
}

// --- Migration ---

export function migrateWorkflow(wf: WorkflowDef): WorkflowDef {
  if (wf.steps.length === 0) return wf;
  if (wf.steps.some((s) => s.next_step)) return wf;

  const steps = wf.steps.map((s) => ({ ...s }));
  const stepsById = new Map(steps.map((s) => [s.id, s]));
  const bt = _branchTargets({ steps });

  function processChain(chainIds: string[]) {
    for (let i = 0; i < chainIds.length; i++) {
      const step = stepsById.get(chainIds[i]);
      if (!step) continue;

      if (i < chainIds.length - 1) {
        step.next_step = chainIds[i + 1];
      }

      if (step.type === "condition") {
        const se = _scopeEnd(steps, step, bt);
        if (step.then_step) {
          const thenChain = _getBranchChain(steps, step.then_step, bt, se);
          processChain(thenChain);
        }
        if (step.else_step) {
          const elseChain = _getBranchChain(steps, step.else_step, bt, se);
          processChain(elseChain);
        }
      }
    }
  }

  const mainChain = steps.filter((s) => !bt.has(s.id)).map((s) => s.id);
  processChain(mainChain);

  return { ...wf, steps };
}

// --- Layout (next_step-based) ---

export function computeLayout(wf: WorkflowDef): PosMap {
  const pos: PosMap = {};
  const stepsById = new Map(wf.steps.map((s) => [s.id, s]));
  const spanCache = new Map<string, number>();

  function stepSpan(stepId: string): number {
    const step = stepsById.get(stepId);
    if (!step || step.type !== "condition") return 1;
    const t = step.then_step ? chainSpan(step.then_step) : 0;
    const e = step.else_step ? chainSpan(step.else_step) : 0;
    return Math.max(t, 1) + Math.max(e, 1);
  }

  function chainSpan(startId: string | undefined): number {
    if (!startId) return 0;
    if (spanCache.has(startId)) return spanCache.get(startId)!;
    let maxSpan = 1;
    const visited = new Set<string>();
    let cur: string | undefined = startId;
    while (cur) {
      if (visited.has(cur)) break;
      visited.add(cur);
      const step = stepsById.get(cur);
      if (!step) break;
      const sp = stepSpan(cur);
      if (sp > maxSpan) maxSpan = sp;
      cur = step.next_step;
    }
    spanCache.set(startId, maxSpan);
    return maxSpan;
  }

  function layoutChain(startId: string | undefined, x: number, startRow: number): number {
    let row = startRow;
    let currentId = startId;

    while (currentId) {
      const step = stepsById.get(currentId);
      if (!step) break;

      const nodeId = `step-${currentId}`;
      if (nodeId in pos) break;

      pos[nodeId] = { x, y: row * ROW_H };

      if (step.type === "condition") {
        const branchRow = row + 1;
        let maxEndRow = row;

        const thenSp = step.then_step ? chainSpan(step.then_step) : 0;
        const elseSp = step.else_step ? chainSpan(step.else_step) : 0;

        if (step.then_step) {
          const thenX = x - Math.max(elseSp, 1) * LANE_W;
          const endRow = layoutChain(step.then_step, thenX, branchRow);
          if (endRow > maxEndRow) maxEndRow = endRow;
        }

        if (step.else_step) {
          const elseX = x + Math.max(thenSp, 1) * LANE_W;
          const endRow = layoutChain(step.else_step, elseX, branchRow);
          if (endRow > maxEndRow) maxEndRow = endRow;
        }

        row = maxEndRow + 1;
      } else {
        row++;
      }

      currentId = step.next_step;
    }

    return row;
  }

  let row = 0;

  if (wf.triggers.length > 0) {
    pos[`trigger-${wf.triggers[0].id}`] = { x: CENTER_X, y: row * ROW_H };
    row++;
  }

  if (wf.steps.length > 0) {
    const firstStep = wf.steps[0];
    if (firstStep) {
      layoutChain(firstStep.id, CENTER_X, row);
    }
  }

  for (const s of wf.steps) {
    const id = `step-${s.id}`;
    if (!(id in pos)) {
      pos[id] = { x: CENTER_X, y: Object.keys(pos).length * ROW_H };
    }
  }

  return pos;
}

// --- Edges (next_step-based) ---

export function buildEdges(wf: WorkflowDef): Edge[] {
  const edges: Edge[] = [];
  const stepsById = new Map(wf.steps.map((s) => [s.id, s]));
  const visited = new Set<string>();

  function addChainEdges(startId: string | undefined) {
    let currentId = startId;

    while (currentId) {
      if (visited.has(currentId)) break;
      visited.add(currentId);

      const step = stepsById.get(currentId);
      if (!step) break;

      if (step.type === "condition") {
        for (const branch of ["then", "else"] as const) {
          const targetId = branch === "then" ? step.then_step : step.else_step;
          if (!targetId) continue;
          edges.push({
            id: `branch:step-${currentId}:${branch}:step-${targetId}`,
            source: `step-${currentId}`,
            sourceHandle: branch,
            target: `step-${targetId}`,
            label: `${branch === "then" ? "true" : "false"}  ×`,
            labelStyle: {
              fontSize: 9,
              fontWeight: 700,
              fill: branch === "then" ? "var(--accent)" : "var(--fg-dim)",
              cursor: "pointer",
            },
            labelBgStyle: { fill: "var(--bg-panel)", fillOpacity: 0.95 },
            labelBgPadding: [4, 3] as [number, number],
            interactionWidth: 20,
            style: {
              stroke: branch === "then" ? "var(--accent)" : "var(--fg-dim)",
              strokeWidth: 1.5,
              strokeDasharray: branch === "else" ? "4 3" : undefined,
              cursor: "pointer",
            },
          });
          addChainEdges(targetId);
        }

        if (step.next_step) {
          edges.push({
            id: `seq:step-${currentId}:step-${step.next_step}`,
            source: `step-${currentId}`,
            target: `step-${step.next_step}`,
            style: { stroke: "var(--fg-dim)", strokeWidth: 1.5 },
          });
        }
      } else {
        if (step.next_step) {
          edges.push({
            id: `seq:step-${currentId}:step-${step.next_step}`,
            source: `step-${currentId}`,
            target: `step-${step.next_step}`,
            animated: step.type === "delay",
            style: { stroke: "var(--fg-dim)", strokeWidth: 1.5 },
          });
        }
      }

      currentId = step.next_step;
    }
  }

  if (wf.triggers.length > 0 && wf.steps.length > 0) {
    edges.push({
      id: `seq:trigger:step-${wf.steps[0].id}`,
      source: `trigger-${wf.triggers[0].id}`,
      target: `step-${wf.steps[0].id}`,
      style: { stroke: "var(--fg-dim)", strokeWidth: 1.5 },
    });
  }

  addChainEdges(wf.steps[0]?.id);

  return edges;
}
