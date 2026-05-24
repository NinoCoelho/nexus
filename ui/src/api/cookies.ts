import { BASE } from "./base";

export interface CookieDomain {
  domain: string;
  count: number;
  file: string;
}

async function _json(res: Response): Promise<any> {
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function listCookies(): Promise<CookieDomain[]> {
  return _json(await fetch(`${BASE}/cookies`));
}

export async function deleteCookies(domain: string): Promise<void> {
  const res = await fetch(`${BASE}/cookies/${encodeURIComponent(domain)}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {}
    throw new Error(detail || `HTTP ${res.status}`);
  }
}
