// API client for HITL settings (YOLO mode).
import { BASE } from "./base";

export interface HitlSettings {
  yolo_mode: boolean;
}

export async function getHitlSettings(): Promise<HitlSettings> {
  const res = await fetch(`${BASE}/settings`);
  if (!res.ok) throw new Error(`Settings error: ${res.status}`);
  return res.json();
}

export async function setHitlSettings(
  patch: Partial<HitlSettings>,
): Promise<HitlSettings> {
  const res = await fetch(`${BASE}/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Settings patch error: ${res.status}`);
  return res.json();
}
