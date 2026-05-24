import { describe, it, expect } from "vitest";
import {
  migrateWorkflow,
  computeLayout,
  buildEdges,
  CENTER_X,
  LANE_W,
  ROW_H,
} from "../workflow-layout";
import type { WorkflowDef, StepConfig } from "../../../types/workflow";

function s(id: string, type: StepConfig["type"] = "tool_call", extra: Partial<StepConfig> = {}): StepConfig {
  return { id, name: id, type, ...extra };
}

function cond(id: string, thenId?: string, elseId?: string, extra: Partial<StepConfig> = {}): StepConfig {
  return s(id, "condition", { then_step: thenId, else_step: elseId, ...extra });
}

function wf(
  steps: StepConfig[],
  opts: { triggers?: WorkflowDef["triggers"] } = {},
): WorkflowDef {
  return {
    title: "test",
    enabled: true,
    triggers: opts.triggers ?? [{ id: "t1", type: "manual" }],
    variables: {},
    steps,
  };
}

function hasEdge(
  edges: ReturnType<typeof buildEdges>,
  source: string,
  target: string,
  sourceHandle?: string,
) {
  return edges.some(
    (e) =>
      e.source === source &&
      e.target === target &&
      (sourceHandle === undefined || e.sourceHandle === sourceHandle),
  );
}

describe("migrateWorkflow", () => {
  it("returns workflow unchanged when next_step already set", () => {
    const w = wf([s("a", "tool_call", { next_step: "b" }), s("b")]);
    const result = migrateWorkflow(w);
    expect(result).toBe(w);
  });

  it("returns workflow unchanged for empty steps", () => {
    const w = wf([]);
    const result = migrateWorkflow(w);
    expect(result).toBe(w);
  });

  it("sets next_step for simple chain from flat array", () => {
    const w = wf([s("a"), s("b"), s("c")]);
    const result = migrateWorkflow(w);
    expect(result.steps[0].next_step).toBe("b");
    expect(result.steps[1].next_step).toBe("c");
    expect(result.steps[2].next_step).toBeUndefined();
  });

  it("sets next_step for main chain excluding branch targets", () => {
    const w = wf([cond("c1", "t1", "f1"), s("t1"), s("f1")]);
    const result = migrateWorkflow(w);
    expect(result.steps[0].next_step).toBeUndefined();
  });

  it("sets next_step within branch chains", () => {
    const w = wf([cond("c1", "t1", "f1"), s("t1"), s("extra"), s("f1")]);
    const result = migrateWorkflow(w);
    const t1 = result.steps.find((s) => s.id === "t1")!;
    const extra = result.steps.find((s) => s.id === "extra")!;
    expect(t1.next_step).toBe("extra");
    expect(extra.next_step).toBeUndefined();
  });

  it("sets next_step for condition with merge step", () => {
    const w = wf([cond("c1", "t1", "f1"), s("t1"), s("f1"), s("after")]);
    const result = migrateWorkflow(w);
    const c1 = result.steps.find((s) => s.id === "c1")!;
    expect(c1.next_step).toBe("after");
  });
});

describe("computeLayout", () => {
  it("places trigger at (400, 0)", () => {
    const w = wf([]);
    const pos = computeLayout(w);
    expect(pos["trigger-t1"]).toEqual({ x: CENTER_X, y: 0 });
  });

  it("simple chain: trigger + 2 steps", () => {
    const w = wf([
      s("a", "tool_call", { next_step: "b" }),
      s("b"),
    ]);
    const pos = computeLayout(w);
    expect(pos["trigger-t1"]).toEqual({ x: CENTER_X, y: 0 });
    expect(pos["step-a"]).toEqual({ x: CENTER_X, y: ROW_H });
    expect(pos["step-b"]).toEqual({ x: CENTER_X, y: 2 * ROW_H });
  });

  it("condition with both branches places them left and right", () => {
    const w = wf([
      cond("c1", "t1", "f1"),
      s("t1"),
      s("f1"),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-c1"]).toEqual({ x: CENTER_X, y: ROW_H });
    expect(pos["step-t1"]).toEqual({ x: CENTER_X - LANE_W, y: 2 * ROW_H });
    expect(pos["step-f1"]).toEqual({ x: CENTER_X + LANE_W, y: 2 * ROW_H });
  });

  it("condition with multi-step then chain", () => {
    const w = wf([
      cond("c1", "t1", "f1"),
      s("t1", "tool_call", { next_step: "extra" }),
      s("extra"),
      s("f1"),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-c1"]).toEqual({ x: CENTER_X, y: ROW_H });
    expect(pos["step-t1"]).toEqual({ x: CENTER_X - LANE_W, y: 2 * ROW_H });
    expect(pos["step-extra"]).toEqual({ x: CENTER_X - LANE_W, y: 3 * ROW_H });
    expect(pos["step-f1"]).toEqual({ x: CENTER_X + LANE_W, y: 2 * ROW_H });
  });

  it("steps after condition merge point continue on center", () => {
    const w = wf([
      cond("c1", "t1", "f1", { next_step: "after" }),
      s("t1"),
      s("f1"),
      s("after"),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-after"]).toBeDefined();
    expect(pos["step-after"]!.x).toBe(CENTER_X);
    expect(pos["step-after"]!.y).toBeGreaterThan(pos["step-t1"]!.y);
  });

  it("nested condition places inner branches correctly", () => {
    const w = wf([
      cond("outer", "innerCond", "outerElse", { next_step: undefined }),
      cond("innerCond", "deepT", undefined),
      s("deepT"),
      s("outerElse"),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-outer"]).toEqual({ x: CENTER_X, y: ROW_H });
    expect(pos["step-innerCond"]!.x).toBeLessThan(CENTER_X);
    expect(pos["step-innerCond"]!.y).toBe(2 * ROW_H);
    expect(pos["step-deepT"]!.x).toBeLessThan(pos["step-innerCond"]!.x);
    expect(pos["step-deepT"]!.y).toBe(3 * ROW_H);
    expect(pos["step-outerElse"]!.x).toBeGreaterThan(CENTER_X);
    expect(pos["step-outerElse"]!.y).toBe(2 * ROW_H);
  });

  it("no triggers: steps start at row 0", () => {
    const w = wf([
      s("a", "tool_call", { next_step: "b" }),
      s("b"),
    ], { triggers: [] });
    const pos = computeLayout(w);
    expect(pos["step-a"]).toEqual({ x: CENTER_X, y: 0 });
    expect(pos["step-b"]).toEqual({ x: CENTER_X, y: ROW_H });
  });

  it("empty workflow returns only trigger position", () => {
    const w = wf([]);
    const pos = computeLayout(w);
    expect(Object.keys(pos)).toEqual(["trigger-t1"]);
  });

  it("long branch forces row offset for continuation", () => {
    const w = wf([
      cond("c1", "t1", "f1", { next_step: "after" }),
      s("t1", "tool_call", { next_step: "t1b" }),
      s("t1b", "tool_call", { next_step: "t1c" }),
      s("t1c"),
      s("f1"),
      s("after"),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-t1"]!.x).toBe(CENTER_X - LANE_W);
    expect(pos["step-t1b"]!.x).toBe(CENTER_X - LANE_W);
    expect(pos["step-t1c"]!.x).toBe(CENTER_X - LANE_W);
    expect(pos["step-f1"]!.x).toBe(CENTER_X + LANE_W);
    expect(pos["step-after"]!.x).toBe(CENTER_X);
    expect(pos["step-after"]!.y).toBeGreaterThan(pos["step-t1c"]!.y);
  });
});

describe("buildEdges", () => {
  it("trigger → first step edge", () => {
    const w = wf([s("a")]);
    const edges = buildEdges(w);
    expect(hasEdge(edges, "trigger-t1", "step-a")).toBe(true);
  });

  it("no edges for empty steps", () => {
    const w = wf([]);
    const edges = buildEdges(w);
    expect(edges).toHaveLength(0);
  });

  it("sequential edges between main chain steps", () => {
    const w = wf([
      s("a", "tool_call", { next_step: "b" }),
      s("b", "tool_call", { next_step: "c" }),
      s("c"),
    ]);
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-a", "step-b")).toBe(true);
    expect(hasEdge(edges, "step-b", "step-c")).toBe(true);
    expect(hasEdge(edges, "trigger-t1", "step-a")).toBe(true);
    expect(edges).toHaveLength(3);
  });

  it("condition produces branch edges with sourceHandle", () => {
    const w = wf([
      cond("c1", "t1", "f1"),
      s("t1"),
      s("f1"),
    ]);
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-c1", "step-t1", "then")).toBe(true);
    expect(hasEdge(edges, "step-c1", "step-f1", "else")).toBe(true);
  });

  it("multi-step branch chain has sequential edges within branch", () => {
    const w = wf([
      cond("c1", "t1", "f1"),
      s("t1", "tool_call", { next_step: "extra" }),
      s("extra"),
      s("f1"),
    ]);
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-c1", "step-t1", "then")).toBe(true);
    expect(hasEdge(edges, "step-t1", "step-extra")).toBe(true);
    expect(hasEdge(edges, "step-c1", "step-f1", "else")).toBe(true);
  });

  it("no spurious edges between branches", () => {
    const w = wf([
      cond("c1", "t1", "f1"),
      s("t1"),
      s("f1"),
    ]);
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-t1", "step-f1")).toBe(false);
    expect(hasEdge(edges, "step-f1", "step-t1")).toBe(false);
  });

  it("condition with merge produces edge to merge step", () => {
    const w = wf([
      cond("c1", "t1", "f1", { next_step: "after" }),
      s("t1"),
      s("f1"),
      s("after"),
    ]);
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-c1", "step-t1", "then")).toBe(true);
    expect(hasEdge(edges, "step-c1", "step-f1", "else")).toBe(true);
    expect(hasEdge(edges, "step-c1", "step-after")).toBe(true);
  });

  it("condition with only then_step", () => {
    const w = wf([cond("c1", "t1"), s("t1")]);
    const pos = computeLayout(w);
    expect(pos["step-c1"]).toEqual({ x: CENTER_X, y: ROW_H });
    expect(pos["step-t1"]).toEqual({ x: CENTER_X - LANE_W, y: 2 * ROW_H });
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-c1", "step-t1", "then")).toBe(true);
    expect(edges.filter((e) => e.sourceHandle === "else")).toHaveLength(0);
  });
});

describe("computeLayout + buildEdges integration", () => {
  it("all steps are positioned and connected in simple chain", () => {
    const w = wf([
      s("a", "tool_call", { next_step: "b" }),
      s("b", "tool_call", { next_step: "c" }),
      s("c"),
    ]);
    const pos = computeLayout(w);
    const edges = buildEdges(w);
    expect(Object.keys(pos)).toEqual(
      expect.arrayContaining(["step-a", "step-b", "step-c"]),
    );
    expect(edges).toHaveLength(3);
  });

  it("condition layout: all nodes positioned at correct x", () => {
    const w = wf([
      cond("c1", "t1", "f1", { next_step: "after" }),
      s("t1"),
      s("f1"),
      s("after"),
    ]);
    const pos = computeLayout(w);
    const edges = buildEdges(w);

    expect(pos["step-c1"]!.x).toBe(CENTER_X);
    expect(pos["step-t1"]!.x).toBe(CENTER_X - LANE_W);
    expect(pos["step-f1"]!.x).toBe(CENTER_X + LANE_W);
    expect(pos["step-after"]!.x).toBe(CENTER_X);

    expect(hasEdge(edges, "step-c1", "step-t1", "then")).toBe(true);
    expect(hasEdge(edges, "step-c1", "step-f1", "else")).toBe(true);
    expect(hasEdge(edges, "step-c1", "step-after")).toBe(true);
  });

  it("nested condition within branch", () => {
    const w = wf([
      cond("outer", "t1", "f1", { next_step: "after" }),
      cond("t1", "deepT", "deepF"),
      s("deepT"),
      s("deepF"),
      s("f1"),
      s("after"),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-outer"]).toBeDefined();
    expect(pos["step-t1"]).toBeDefined();
    expect(pos["step-deepT"]).toBeDefined();
    expect(pos["step-deepF"]).toBeDefined();
    expect(pos["step-f1"]).toBeDefined();
    expect(pos["step-after"]).toBeDefined();

    expect(pos["step-outer"]!.x).toBe(CENTER_X);
    expect(pos["step-t1"]!.x).toBeLessThan(pos["step-outer"]!.x);
    expect(pos["step-deepT"]!.x).toBeLessThan(pos["step-t1"]!.x);
    expect(pos["step-deepF"]!.x).toBeGreaterThan(pos["step-t1"]!.x);
    expect(pos["step-f1"]!.x).toBeGreaterThan(pos["step-outer"]!.x);
    expect(pos["step-f1"]!.x).toBeGreaterThan(pos["step-deepF"]!.x);

    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-outer", "step-t1", "then")).toBe(true);
    expect(hasEdge(edges, "step-outer", "step-f1", "else")).toBe(true);
    expect(hasEdge(edges, "step-outer", "step-after")).toBe(true);
    expect(hasEdge(edges, "step-t1", "step-deepT", "then")).toBe(true);
    expect(hasEdge(edges, "step-t1", "step-deepF", "else")).toBe(true);
  });

  it("multiple conditions in sequence", () => {
    const w = wf([
      cond("c1", "t1", undefined, { next_step: "c2" }),
      s("t1"),
      cond("c2", "t2", undefined, { next_step: "end" }),
      s("t2"),
      s("end"),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-c1"]).toBeDefined();
    expect(pos["step-c2"]).toBeDefined();
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-c1", "step-t1", "then")).toBe(true);
    expect(hasEdge(edges, "step-c1", "step-c2")).toBe(true);
    expect(hasEdge(edges, "step-c2", "step-t2", "then")).toBe(true);
    expect(hasEdge(edges, "step-c2", "step-end")).toBe(true);
  });
});

describe("onConnect simulation with next_step", () => {
  function simulateConnect(
    w: WorkflowDef,
    srcStepId: string,
    targetStepId: string,
  ): WorkflowDef {
    let steps = w.steps.map((s) => {
      const patches: Partial<StepConfig> = {};
      if (s.next_step === targetStepId) patches.next_step = undefined;
      if (s.then_step === targetStepId) patches.then_step = undefined;
      if (s.else_step === targetStepId) patches.else_step = undefined;
      return Object.keys(patches).length > 0 ? { ...s, ...patches } : s;
    });

    steps = steps.map((s) =>
      s.id === srcStepId ? { ...s, next_step: targetStepId } : s,
    );

    const target = steps.find((s) => s.id === targetStepId);
    if (!target) return w;
    const rest = steps.filter((s) => s.id !== targetStepId);
    const srcIdx = rest.findIndex((s) => s.id === srcStepId);
    if (srcIdx === -1) return { ...w, steps };
    rest.splice(srcIdx + 1, 0, target);
    return { ...w, steps: rest };
  }

  it("connects step to orphan step", () => {
    const w = wf([s("a", "tool_call", { next_step: "b" }), s("b"), s("orphan")]);
    const result = simulateConnect(w, "a", "orphan");
    const src = result.steps.find((s) => s.id === "a")!;
    expect(src.next_step).toBe("orphan");
  });

  it("clears old predecessor when connecting to branch target", () => {
    const w = wf([
      cond("c1", "t1", "f1"),
      s("t1"),
      s("f1"),
      s("other"),
    ]);
    const result = simulateConnect(w, "other", "t1");
    const c1 = result.steps.find((s) => s.id === "c1")!;
    expect(c1.then_step).toBeUndefined();
  });

  it("inserts after source in array", () => {
    const w = wf([
      s("a"),
      s("b"),
      s("c"),
    ]);
    const result = simulateConnect(w, "a", "c");
    const ids = result.steps.map((s) => s.id);
    const aIdx = ids.indexOf("a");
    const cIdx = ids.indexOf("c");
    expect(cIdx).toBe(aIdx + 1);
  });
});

describe("edge cases", () => {
  it("workflow with only a trigger produces no edges", () => {
    const w = wf([]);
    expect(buildEdges(w)).toHaveLength(0);
    const pos = computeLayout(w);
    expect(Object.keys(pos)).toEqual(["trigger-t1"]);
  });

  it("single step with no trigger", () => {
    const w = wf([s("a")], { triggers: [] });
    const pos = computeLayout(w);
    const edges = buildEdges(w);
    expect(pos["step-a"]).toEqual({ x: CENTER_X, y: 0 });
    expect(edges).toHaveLength(0);
  });

  it("step with next_step pointing to non-existent id is handled gracefully", () => {
    const w = wf([s("a", "tool_call", { next_step: "nonexistent" })]);
    const pos = computeLayout(w);
    expect(pos["step-a"]).toBeDefined();
    const edges = buildEdges(w);
    expect(hasEdge(edges, "step-a", "step-nonexistent")).toBe(true);
  });

  it("sibling conditions' branches never collide", () => {
    const w = wf([
      cond("outer", "condL", "condR"),
      cond("condL", undefined, "fL"),
      s("fL"),
      cond("condR", "tR", undefined),
      s("tR"),
    ]);
    const pos = computeLayout(w);

    expect(pos["step-fL"]!.x).not.toBe(pos["step-tR"]!.x);
    expect(Math.abs(pos["step-fL"]!.x - pos["step-tR"]!.x)).toBeGreaterThanOrEqual(LANE_W);
  });

  it("deeply nested conditions have no x collisions among steps", () => {
    const w = wf([
      cond("root", "L", "R"),
      cond("L", "LL", "LR"),
      s("LL"),
      s("LR"),
      cond("R", "RL", "RR"),
      s("RL"),
      s("RR"),
    ]);
    const pos = computeLayout(w);
    const stepXs = Object.entries(pos)
      .filter(([k]) => k.startsWith("step-"))
      .map(([, v]) => v.x);
    const uniqueXs = new Set(stepXs);
    expect(uniqueXs.size).toBe(stepXs.length);
  });

  it("cycle in next_step is detected and stopped", () => {
    const w = wf([
      s("a", "tool_call", { next_step: "b" }),
      s("b", "tool_call", { next_step: "a" }),
    ]);
    const pos = computeLayout(w);
    expect(pos["step-a"]).toBeDefined();
    expect(pos["step-b"]).toBeDefined();
    expect(buildEdges(w).length).toBeGreaterThan(0);
  });
});
