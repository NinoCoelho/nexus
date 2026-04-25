import type { ModelTier } from "../../api";

export const EMBEDDING_COMPAT_TYPES = new Set(["openai_compat", "ollama"]);
export const TIERS: ModelTier[] = ["fast", "balanced", "heavy"];

export interface ModelForm {
  id: string;
  id_touched: boolean;
  provider: string;
  model_name: string;
  tags: string;
  tier: ModelTier;
  notes: string;
  tier_source: "heuristic" | "default" | "manual";
}

export interface DiscoveryState {
  models: string[];
  fetchedAt: number;
  error: string | null;
}

export const CACHE_TTL_MS = 30_000;

export const emptyForm: ModelForm = {
  id: "",
  id_touched: false,
  provider: "",
  model_name: "",
  tags: "",
  tier: "balanced",
  notes: "",
  tier_source: "default",
};
