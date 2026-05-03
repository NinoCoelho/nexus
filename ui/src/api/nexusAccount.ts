/**
 * Client for /auth/nexus/* — the Nexus account integration that ships
 * the demo + (paid) nexus hosted models.
 *
 * The apiKey lives only in the Python backend. The UI never sees it.
 */
import { BASE } from "./base";

export interface NexusStatusSnapshot {
  tier: "free" | "pro" | string;
  spend: number;
  maxBudget: number;
  remaining: number;
  budgetDuration: string;
  models: string[];
  rpmLimit: number;
  tpmLimit: number;
  budgetResetAt: string;
}

export interface NexusAccountStatus {
  signedIn: boolean;
  email: string;
  tier: "free" | "pro" | string;
  /** ISO date when pro access ends (set when user cancels, cleared on reactivation). */
  cancelsAt: string | null;
  /** True once /api/keys/confirm has flipped the website's connected flag. */
  connected: boolean;
  models: string[];
  refreshedAt: string;
  /** Live spend/budget snapshot from the last /api/status poll. */
  status?: NexusStatusSnapshot;
}

export interface NexusVerifyResponse {
  uid: string;
  email: string;
  displayName: string;
  tier: "free" | "pro" | string;
  apiKey: string;
  isNew: boolean;
}

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function verifyNexusIdToken(idToken: string): Promise<NexusVerifyResponse> {
  return _json(
    await fetch(`${BASE}/auth/nexus/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idToken }),
    }),
  );
}

export async function getNexusAccountStatus(): Promise<NexusAccountStatus> {
  return _json(await fetch(`${BASE}/auth/nexus/status`));
}

export async function refreshNexusAccount(): Promise<NexusAccountStatus> {
  return _json(
    await fetch(`${BASE}/auth/nexus/refresh`, { method: "POST" }),
  );
}

export async function logoutNexusAccount(): Promise<{ signedIn: false }> {
  return _json(
    await fetch(`${BASE}/auth/nexus/logout`, { method: "POST" }),
  );
}
