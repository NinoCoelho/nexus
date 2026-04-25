// Shared types for ProvidersSection sub-components.

export interface EditState {
  name: string;
  base_url: string;
  key_env: string;
  api_key: string;
}

export interface AddState {
  name: string;
  base_url: string;
  key_env: string;
  key_env_touched: boolean;
  api_key: string;
  type: "openai_compat" | "anthropic" | "ollama";
}
