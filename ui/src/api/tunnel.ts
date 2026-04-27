// API client for the public sharing tunnel.
import { BASE } from "./base";

export interface TunnelStatus {
  active: boolean;
  provider: string | null;
  public_url: string | null;
  share_url: string | null;
  /** Short access code shown on the desktop; only populated on loopback responses. */
  code: string | null;
  started_at: number | null;
  /** Whether the ngrok CLI binary has been downloaded yet. */
  binary_installed: boolean;
}

export interface AuthtokenStatus {
  configured: boolean;
}

export interface InstallResult {
  ok: true;
  path: string;
  installed: true;
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

export async function getTunnelStatus(): Promise<TunnelStatus> {
  return _json(await fetch(`${BASE}/tunnel/status`));
}

export async function startTunnel(): Promise<TunnelStatus> {
  return _json(await fetch(`${BASE}/tunnel/start`, { method: "POST" }));
}

export async function stopTunnel(): Promise<TunnelStatus> {
  return _json(await fetch(`${BASE}/tunnel/stop`, { method: "POST" }));
}

export async function getAuthtokenStatus(): Promise<AuthtokenStatus> {
  return _json(await fetch(`${BASE}/tunnel/authtoken`));
}

export async function setAuthtoken(authtoken: string): Promise<{ ok: true; configured: boolean }> {
  return _json(
    await fetch(`${BASE}/tunnel/authtoken`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ authtoken }),
    }),
  );
}

export async function installNgrokBinary(): Promise<InstallResult> {
  return _json(await fetch(`${BASE}/tunnel/install`, { method: "POST" }));
}
