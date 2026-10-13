/**
 * browser — HTTP surface for the managed-Chrome computer-use subsystem.
 *
 * One Chrome per context: "main" (coordinator + unprojected chats) and
 * "project:<id>". The BrowserPane renders a live screenshot and scales
 * pixel clicks into the fixed 1440×900 logical viewport.
 */

import { BASE } from "./base";

export interface BrowserStatus {
  key: string;
  connected: boolean;
  running: boolean;
  port?: number;
  url?: string | null;
  title?: string | null;
}

export interface BrowserActionResult {
  ok: boolean;
  key: string;
  error?: string;
  url?: string | null;
  title?: string | null;
  screenshot?: string;
  value?: unknown;
  text?: string;
}

export async function getBrowserStatus(key: string): Promise<BrowserStatus> {
  const res = await fetch(`${BASE}/browser/status?key=${encodeURIComponent(key)}`);
  if (!res.ok) throw new Error("Failed to get browser status");
  return res.json();
}

export async function listBrowserInstances(): Promise<BrowserStatus[]> {
  const res = await fetch(`${BASE}/browser/instances`);
  if (!res.ok) throw new Error("Failed to list browser instances");
  return res.json();
}

export async function browserAction(
  key: string,
  action: string,
  args: Record<string, unknown> = {},
): Promise<BrowserActionResult> {
  const res = await fetch(`${BASE}/browser/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, action, ...args }),
  });
  if (!res.ok) throw new Error(`Browser action ${action} failed`);
  return res.json();
}

export async function stopBrowser(key: string): Promise<{ key: string; stopped: boolean }> {
  const res = await fetch(`${BASE}/browser/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  });
  if (!res.ok) throw new Error("Failed to stop browser");
  return res.json();
}

/** Live-screenshot URL with a cache-buster (bump on every browser_event). */
export function browserScreenshotUrl(key: string, ts: number): string {
  return `${BASE}/browser/screenshot?key=${encodeURIComponent(key)}&t=${ts}`;
}
