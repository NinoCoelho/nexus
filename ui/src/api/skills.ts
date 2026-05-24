// API client for skill management.
import { BASE } from "./base";

export interface DerivedFromSource {
  slug: string;
  url: string;
  title: string;
}

export interface DerivedFrom {
  wizard_ask: string;
  wizard_built_at: string;
  sources: DerivedFromSource[];
}

export interface SkillSummary {
  name: string;
  description: string;
  trust: "builtin" | "user" | "agent";
  derived_from?: DerivedFrom | null;
}

export interface SkillDetail {
  name: string;
  body: string;
  frontmatter: Record<string, unknown>;
  derived_from?: DerivedFrom | null;
  trust?: "builtin" | "user" | "agent";
  description?: string;
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

/** Delete a skill from disk. The directory is removed and the registry
 *  reloads on the server side. Returns nothing on success. */
export async function deleteSkill(name: string): Promise<void> {
  const res = await fetch(`${BASE}/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (res.status === 204) return;
  const detail = await res.json().catch(() => ({ detail: res.statusText }));
  throw new Error(detail.detail || `Skill delete error: ${res.status}`);
}

/** URL the browser can open / fetch to download the skills ZIP archive. */
export function exportSkillsArchiveUrl(): string {
  return `${BASE}/skills/export/archive`;
}

export interface ImportSkillsResult {
  imported: string[];
  skipped: { name: string; reason: string }[];
}

/** Upload a ZIP of skill directories. Existing skills with matching names
 *  are overwritten. Returns lists of imported / skipped entries so the
 *  caller can show a summary toast. */
export async function importSkillsArchive(file: File): Promise<ImportSkillsResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/skills/import/archive`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `Skill import error: ${res.status}`);
  }
  return res.json();
}

// ── Wizard discovery ───────────────────────────────────────────────────────
//
// `discoverSkills(userAsk)` calls the Phase-1 backend. The response is
// abstract by design — title/summary/complexity/cost/keys — never the raw
// SKILL.md body. The wizard renders these cards directly.

export interface SkillCandidateKey {
  name: string;
  vendor: string;
  get_key_url: string;
  free_tier_available: boolean;
}

export interface SkillCandidateSource {
  slug: string;
  url: string;
  verified: boolean;
}

export interface SkillCandidate {
  id: string;
  title: string;
  summary: string;
  capabilities: string[];
  complexity: number;
  cost_tier: "free" | "low" | "medium" | "high";
  requires_keys: SkillCandidateKey[];
  risks: string[];
  confidence: number;
  score: number;
  source: SkillCandidateSource;
}

export interface DiscoverResponse {
  candidates: SkillCandidate[];
}

export async function discoverSkills(
  userAsk: string,
  language?: string,
  limit = 8,
): Promise<SkillCandidate[]> {
  const res = await fetch(`${BASE}/skills/wizard/discover`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_ask: userAsk, language, limit }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `Skill discovery error: ${res.status}`);
  }
  const data: DiscoverResponse = await res.json();
  return data.candidates;
}

export interface BuildSkillResponse {
  session_id: string;
}

export async function buildSkill(args: {
  candidateId: string;
  userAsk: string;
  relatedIds?: string[];
  language?: string;
}): Promise<BuildSkillResponse> {
  const res = await fetch(`${BASE}/skills/wizard/build`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate_id: args.candidateId,
      user_ask: args.userAsk,
      related_ids: args.relatedIds ?? [],
      language: args.language,
    }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail || `Build skill error: ${res.status}`);
  }
  return res.json();
}
