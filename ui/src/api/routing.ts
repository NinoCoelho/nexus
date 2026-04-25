// API client for model routing configuration.
import { BASE } from "./base";

export interface RoutingConfig {
  default_model: string;
  last_used_model: string;
  routing_mode: "fixed" | "auto";
  available_models: string[];
  embedding_model_id: string;
  extraction_model_id: string;
}

export async function getRouting(): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`);
  if (!res.ok) throw new Error(`Routing error: ${res.status}`);
  return res.json();
}

export async function putRouting(patch: {
  default_model?: string;
  last_used_model?: string;
  routing_mode?: "fixed" | "auto";
  embedding_model_id?: string;
  extraction_model_id?: string;
}): Promise<RoutingConfig> {
  const res = await fetch(`${BASE}/routing`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Routing update error: ${res.status}`);
  return res.json();
}
