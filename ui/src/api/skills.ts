// API client for skill management.
import { BASE } from "./base";

export interface SkillSummary {
  name: string;
  description: string;
  trust: "builtin" | "user" | "agent";
}

export interface SkillDetail {
  name: string;
  body: string;
  frontmatter: Record<string, unknown>;
}

export async function getSkills(): Promise<SkillSummary[]> {
  const res = await fetch(`${BASE}/skills`);
  if (!res.ok) throw new Error(`Skills error: ${res.status}`);
  return res.json();
}

export async function getSkill(name: string): Promise<SkillDetail> {
  const res = await fetch(`${BASE}/skills/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`Skill error: ${res.status}`);
  return res.json();
}

export async function updateSkill(name: string, body: string): Promise<SkillDetail> {
  const res = await fetch(`${BASE}/skills/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `Skill update error: ${res.status}`);
  }
  return res.json();
}
