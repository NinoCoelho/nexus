// API client for the encrypted site credential store
// (~/.nexus/site_credentials.db) — per-site logins used by browser fills.
import { BASE } from "./base";

export interface SiteCredential {
  site: string;
  username: string;
  created_at?: string | null;
  updated_at?: string | null;
  last_used_at?: string | null;
}

export async function listSiteCredentials(): Promise<SiteCredential[]> {
  const res = await fetch(`${BASE}/site-credentials`);
  if (!res.ok) throw new Error(`Site logins list error: ${res.status}`);
  return res.json();
}

export async function setSiteCredential(
  site: string,
  username: string,
  password: string,
): Promise<void> {
  const res = await fetch(`${BASE}/site-credentials/${encodeURIComponent(site)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `Save site login error: ${res.status}`);
  }
}

export async function deleteSiteCredential(site: string): Promise<void> {
  const res = await fetch(`${BASE}/site-credentials/${encodeURIComponent(site)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete site login error: ${res.status}`);
}
