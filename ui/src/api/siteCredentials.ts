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
): Promise<string> {
  // Site goes in the BODY: user-typed values are often full URLs, and a
  // site in the path breaks routing (unencoded "/" → multi-segment path
  // → 405). The backend normalizes and returns the canonical site key.
  const res = await fetch(`${BASE}/site-credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ site, username, password }),
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
  const data = (await res.json()) as { site: string };
  return data.site || site;
}

export async function deleteSiteCredential(site: string, username?: string): Promise<void> {
  const qs = username ? `?username=${encodeURIComponent(username)}` : "";
  const res = await fetch(`${BASE}/site-credentials/${encodeURIComponent(site)}${qs}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete site login error: ${res.status}`);
}
