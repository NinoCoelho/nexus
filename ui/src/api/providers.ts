// API client for LLM provider management.
import { BASE } from "./base";

export interface Provider {
  name: string;
  base_url?: string;
  has_key: boolean;
  key_env?: string;
  key_source: "inline" | "env" | "anonymous" | null;
  type?: "openai_compat" | "anthropic" | "ollama";
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
