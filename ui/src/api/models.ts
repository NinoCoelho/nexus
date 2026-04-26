// API client for model registry management.
import { BASE } from "./base";

export type ModelTier = "fast" | "balanced" | "heavy";

export interface Model {
  id: string;
  provider: string;
  model_name: string;
  tags: string[];
  tier: ModelTier;
  notes: string;
  is_embedding_capable?: boolean;
  context_window?: number;
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

export async function patchModel(
  id: string,
  patch: { model_name?: string; tags?: string[]; tier?: ModelTier; notes?: string; is_embedding_capable?: boolean; context_window?: number },
): Promise<Model> {
  const res = await fetch(`${BASE}/models/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Model update error: ${res.status}`);
  return res.json();
}

export async function suggestModelTier(model_name: string): Promise<{ tier: ModelTier; source: string }> {
  const res = await fetch(`${BASE}/models/suggest-tier`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model_name }),
  });
  if (!res.ok) throw new Error(`Suggest tier error: ${res.status}`);
  return res.json();
}

export async function setModelRole(role: string, model_id: string): Promise<{ role: string; model_id: string }> {
  const res = await fetch(`${BASE}/models/roles`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, model_id }),
  });
  if (!res.ok) throw new Error(`Model role error: ${res.status}`);
  return res.json();
}

export async function clearModelRole(role: string): Promise<void> {
  const res = await fetch(`${BASE}/models/roles`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, model_id: "" }),
  });
  if (!res.ok) throw new Error(`Model role clear error: ${res.status}`);
}
