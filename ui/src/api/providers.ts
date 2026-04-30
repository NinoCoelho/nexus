// API client for LLM provider management.
import { BASE } from "./base";

export interface Provider {
  name: string;
  base_url?: string;
  has_key: boolean;
  key_env?: string;
  /** When set, the provider resolves its API key via the named credential
   *  store entry (env-first → secrets.toml). Takes precedence over
   *  ``key_env`` and inline storage. */
  credential_ref?: string | null;
  key_source: "inline" | "env" | "anonymous" | "credential" | null;
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

/** Remove a provider + its model entries. Stored credentials are kept. */
export async function deleteProvider(name: string): Promise<void> {
  const res = await fetch(`${BASE}/providers/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `Delete provider error: ${res.status}`);
  }
}

/**
 * Point a provider at a named credential in the store, or clear the link.
 * Pass ``null`` to fall back to legacy inline / env-var paths.
 *
 * Setting a non-null ref also clears ``use_inline_key`` and ``api_key_env``
 * server-side so configuration can't silently drift back to a stale
 * inline key the user thought they replaced.
 */
export async function setProviderCredential(
  name: string,
  credentialRef: string | null,
): Promise<void> {
  const res = await fetch(
    `${BASE}/providers/${encodeURIComponent(name)}/credential`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ credential_ref: credentialRef }),
    },
  );
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `Set provider credential error: ${res.status}`);
  }
}
