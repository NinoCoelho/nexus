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
  /** Optional name of a credential-store entry to bind the new provider to.
   *  Set via the CredentialPicker; PUT to /providers/{name}/credential after
   *  the provider is created. Null means "leave the provider on legacy
   *  inline/env paths". */
  credential_ref: string | null;
  type: "openai_compat" | "anthropic" | "ollama";
}
