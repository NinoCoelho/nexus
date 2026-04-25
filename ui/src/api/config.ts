// API client for agent configuration.
import { BASE } from "./base";
import type { Model } from "./models";

export interface AgentConfig {
  default_model: string;
  routing_mode: "fixed" | "auto";
  max_iterations: number;
}

export interface TranscriptionConfig {
  mode: "local" | "remote";
  model: string;
  language: string;
  device: "cpu" | "cuda" | "auto";
  compute_type: string;
  remote: {
    base_url: string;
    api_key_env: string;
    model: string;
  };
}

export interface Config {
  agent: AgentConfig;
  providers: Record<string, { base_url?: string; key_env?: string; has_key: boolean }>;
  models: Model[];
  transcription?: TranscriptionConfig;
}

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
