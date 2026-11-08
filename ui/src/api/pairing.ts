// API client for native-app pairing (tailnet devices).
import { BASE } from "./base";

export interface PairingInfo {
  v: number;
  /** Machine display name — what the phone shows in its pairing confirm sheet. */
  name: string;
  /** Best tailnet host for this server (Tailscale IPv4 when available). */
  host: string;
  port: number;
  url: string;
  /** Long-lived bearer token — loopback-only response, never tunnel-served. */
  token: string;
  /** `nexus://pair?...` URL encoded by the QR code. */
  deep_link: string;
  features: Record<string, boolean>;
}

async function _json(res: Response): Promise<any> {
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json())?.detail ?? "";
    } catch {
      // ignore
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getPairingInfo(): Promise<PairingInfo> {
  return _json(await fetch(`${BASE}/pair`));
}

/** Remint the token — every previously paired device is revoked. */
export async function rotatePairingToken(): Promise<PairingInfo> {
  return _json(await fetch(`${BASE}/pair/rotate`, { method: "POST" }));
}
