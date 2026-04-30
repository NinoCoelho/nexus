// API client for agent configuration.
import { BASE } from "./base";
import type { Model } from "./models";

export interface AgentConfig {
  default_model: string;
  max_iterations: number;
  temperature?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  anti_repeat_threshold?: number;
  default_max_output_tokens?: number;
}

export const AGENT_DEFAULTS = {
  max_iterations: 16,
  temperature: 0.0,
  frequency_penalty: 0.3,
  presence_penalty: 0.0,
  anti_repeat_threshold: 200,
  default_max_output_tokens: 0,
} as const;

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

export interface SearchProviderEntry {
  type: "ddgs" | "brave" | "tavily" | string;
  key_env: string;
  timeout: number;
  ready?: boolean;
}

export interface SearchConfig {
  enabled: boolean;
  strategy: "concurrent" | "fallback" | string;
  providers: SearchProviderEntry[];
}

export interface UIConfig {
  language: "en" | "pt-BR";
}

export interface Config {
  agent: AgentConfig;
  providers: Record<string, { base_url?: string; key_env?: string; has_key: boolean }>;
  models: Model[];
  transcription?: TranscriptionConfig;
  search?: SearchConfig;
  ui?: UIConfig;
}

// Patch payload — every nested object is independently partial because the
// backend (config.py:99) shallow-merges keys it sees and leaves the rest alone.
export interface ConfigPatch {
  agent?: Partial<AgentConfig>;
  providers?: Partial<Config["providers"]>;
  models?: Model[];
  transcription?: Partial<TranscriptionConfig>;
  search?: Partial<SearchConfig>;
  ui?: Partial<UIConfig>;
}

export async function getConfig(): Promise<Config> {
  const res = await fetch(`${BASE}/config`);
  if (!res.ok) throw new Error(`Config error: ${res.status}`);
  return res.json();
}

export async function patchConfig(patch: ConfigPatch): Promise<Config> {
  const res = await fetch(`${BASE}/config`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Config patch error: ${res.status}`);
  return res.json();
}

export async function patchAgentConfig(patch: Partial<AgentConfig>): Promise<Config> {
  return patchConfig({ agent: patch });
}
