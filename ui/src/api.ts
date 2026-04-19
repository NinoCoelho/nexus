const BASE = import.meta.env.VITE_NEXUS_API ?? "http://localhost:18989";

export interface TraceEvent {
  iter: number;
  tool?: string;
  args?: unknown;
  result?: unknown;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  trace: TraceEvent[];
  skills_touched: string[];
}

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

export async function postChat(
  message: string,
  session_id?: string,
  context?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id, context }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch {
      // body was not JSON; keep the status
    }
    throw new Error(detail);
  }
  return res.json();
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

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${BASE}/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function getHealth(): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}

// ── Settings types ────────────────────────────────────────────────────────────

export interface ModelStrengths {
  speed: number;
  cost: number;
  reasoning: number;
  coding: number;
}

export interface Model {
  id: string;
  provider: string;
  model_name: string;
  tags: string[];
  strengths: ModelStrengths;
}

export interface Provider {
  name: string;
  base_url?: string;
  has_key: boolean;
  key_env?: string;
  key_source: "inline" | "env" | "anonymous" | null;
  type?: "openai_compat" | "anthropic" | "ollama";
}

export interface AgentConfig {
  default_model: string;
  routing_mode: "fixed" | "auto";
  max_iterations: number;
}

export interface Config {
  agent: AgentConfig;
  providers: Record<string, { base_url?: string; key_env?: string; has_key: boolean }>;
  models: Model[];
}

export interface RoutingConfig {
  mode: "fixed" | "auto";
  default_model: string;
  available_models: string[];
}

// ── Settings API calls ────────────────────────────────────────────────────────

export async function getConfig(): Promise<Config> {
  const res = await fetch(`${BASE}/config`);
  if (!res.ok) throw new Error(`Config error: ${res.status}`);
  return res.json();
}

export async function patchConfig(patch: Partial<Config>): Promise<Config> {
  const res = await fetch(`${BASE}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Config patch error: ${res.status}`);
  return res.json();
}

export async function getProviders(): Promise<Provider[]> {
  const res = await fetch(`${BASE}/providers`);
  if (!res.ok) throw new Error(`Providers error: ${res.status}`);
  return res.json();
}

export async function fetchProviderModels(name: string): Promise<{ models: string[]; ok: boolean; error: string | null }> {
  const res = await fetch(`${BASE}/providers/${encodeURIComponent(name)}/models`);
  if (!res.ok) throw new Error(`Provider models error: ${res.status}`);
  return res.json();
}

export async function setProviderKey(name: string, apiKey: string): Promise<void> {
  const res = await fetch(`${BASE}/providers/${encodeURIComponent(name)}/key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) throw new Error(`Set key error: ${res.status}`);
}

export async function clearProviderKey(name: string): Promise<void> {
  const res = await fetch(`${BASE}/providers/${encodeURIComponent(name)}/key`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Clear key error: ${res.status}`);
}

export async function getModels(): Promise<Model[]> {
  const res = await fetch(`${BASE}/models`);
  if (!res.ok) throw new Error(`Models error: ${res.status}`);
  return res.json();
}

export async function postModel(model: Model): Promise<Model> {
  const res = await fetch(`${BASE}/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(model),
  });
  if (!res.ok) throw new Error(`Model create error: ${res.status}`);
  return res.json();
}

export async function deleteModel(id: string): Promise<void> {
  const res = await fetch(`${BASE}/models/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Model delete error: ${res.status}`);
}

export async function getRouting(): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`);
  if (!res.ok) throw new Error(`Routing error: ${res.status}`);
  return res.json();
}

export async function putRouting(patch: { mode?: "fixed" | "auto"; default_model?: string }): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Routing update error: ${res.status}`);
  return res.json();
}
