import type { StepConfig } from "../../types/workflow";

export function uid(): string {
  return Math.random().toString(36).substring(2, 10);
}

export function slugify(name: string): string {
  return name
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[\s_\-]+/)
    .map((w, i) => i === 0 ? w.toLowerCase() : w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join("");
}

export function insertStepAfter(steps: StepConfig[], afterId: string, newStep: StepConfig): StepConfig[] {
  const result = [...steps];
  const idx = result.findIndex((s) => s.id === afterId);
  if (idx === -1) {
    result.push(newStep);
    return result;
  }
  result.splice(idx + 1, 0, newStep);
  return result;
}

export function clearPredecessorTo(steps: StepConfig[], targetId: string): StepConfig[] {
  return steps.map((s) => {
    const patches: Partial<StepConfig> = {};
    if (s.next_step === targetId) patches.next_step = undefined;
    if (s.then_step === targetId) patches.then_step = undefined;
    if (s.else_step === targetId) patches.else_step = undefined;
    return Object.keys(patches).length > 0 ? { ...s, ...patches } : s;
  });
}

export function moveAfter(steps: StepConfig[], afterId: string, targetId: string): StepConfig[] {
  const target = steps.find((s) => s.id === targetId);
  if (!target) return steps;
  const rest = steps.filter((s) => s.id !== targetId);
  const idx = rest.findIndex((s) => s.id === afterId);
  if (idx === -1) return [...rest, target];
  rest.splice(idx + 1, 0, target);
  return rest;
}
