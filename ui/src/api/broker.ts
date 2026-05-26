import { BASE } from "./base";

export interface WebhookAssignment {
  type: "kanban" | "workflow";
  path: string;
  lane_id?: string;
  trigger_id?: string;
}

export interface BrokerWebhook {
  broker_id: string;
  broker_slug: string;
  name: string;
  url: string;
  assigned: boolean;
  assignment: WebhookAssignment | null;
  is_active: boolean;
  exists_on_broker: boolean;
  message_count: number;
  created_at: string | null;
  last_verified_at: string | null;
  orphan?: boolean;
}

export interface WebhookListResponse {
  connected: boolean;
  signed_in: boolean;
  webhooks: BrokerWebhook[];
  quota: {
    used: number;
    local_assigned: number;
    local_unassigned: number;
  } | null;
}

export async function listBrokerWebhooks(): Promise<WebhookListResponse> {
  const res = await fetch(`${BASE}/broker/webhooks`);
  if (!res.ok) throw new Error(`Broker webhooks list error: ${res.status}`);
  return res.json();
}

export async function createBrokerWebhook(name?: string): Promise<{
  broker_id: string;
  broker_slug: string;
  name: string;
  url: string;
  assigned: boolean;
}> {
  const res = await fetch(`${BASE}/broker/webhooks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name || "Nexus Webhook" }),
  });
  if (!res.ok) throw new Error(`Broker webhook create error: ${res.status}`);
  return res.json();
}

export async function deleteBrokerWebhook(brokerId: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/broker/webhooks/${encodeURIComponent(brokerId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Broker webhook delete error: ${res.status}`);
  return res.json();
}

export async function assignBrokerWebhook(
  brokerId: string,
  target: {
    type: "kanban" | "workflow";
    path: string;
    lane_id?: string;
    trigger_id?: string;
  },
): Promise<{ ok: boolean; url: string; token: string }> {
  const res = await fetch(`${BASE}/broker/webhooks/${encodeURIComponent(brokerId)}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(target),
  });
  if (!res.ok) throw new Error(`Broker webhook assign error: ${res.status}`);
  return res.json();
}

export async function unassignBrokerWebhook(brokerId: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/broker/webhooks/${encodeURIComponent(brokerId)}/unassign`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Broker webhook unassign error: ${res.status}`);
  return res.json();
}
