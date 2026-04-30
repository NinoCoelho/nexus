// API client for the generic credential store (~/.nexus/secrets.toml).
// For LLM provider keys see ./providers — that endpoint also touches the
// provider config schema (use_inline_key) and the routing registry.
import { BASE } from "./base";

export type CredentialKind = "generic" | "skill" | "provider";

export interface Credential {
  name: string;
  kind: CredentialKind;
  skill?: string | null;
  created_at?: string | null;
  /** Server-side mask, e.g. "sk-…abcd". Never the raw value. */
  masked: string;
  source: "store";
}

export async function listCredentials(): Promise<Credential[]> {
  const res = await fetch(`${BASE}/credentials`);
  if (!res.ok) throw new Error(`Credentials list error: ${res.status}`);
  return res.json();
}

export interface SetCredentialOptions {
  kind?: CredentialKind;
  skill?: string;
}

export async function setCredential(
  name: string,
  value: string,
  opts: SetCredentialOptions = {},
): Promise<void> {
  const res = await fetch(`${BASE}/credentials/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value, ...opts }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `Set credential error: ${res.status}`);
  }
}

export async function deleteCredential(name: string): Promise<void> {
  const res = await fetch(`${BASE}/credentials/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete credential error: ${res.status}`);
}

export async function credentialExists(name: string): Promise<boolean> {
  const res = await fetch(
    `${BASE}/credentials/${encodeURIComponent(name)}/exists`,
  );
  if (!res.ok) throw new Error(`Credential exists error: ${res.status}`);
  const data = await res.json();
  return Boolean(data.exists);
}
