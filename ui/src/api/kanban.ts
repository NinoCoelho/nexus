// API client for vault-native kanban boards.
import { BASE } from "./base";

export type KanbanCardStatus = "running" | "done" | "failed";
export type KanbanCardPriority = "low" | "med" | "high" | "urgent";

export interface KanbanCard {
  id: string;
  title: string;
  body?: string;
  session_id?: string;
  status?: KanbanCardStatus;
  due?: string;
  priority?: KanbanCardPriority;
  labels?: string[];
  assignees?: string[];
}

export interface KanbanLane {
  id: string;
  title: string;
  cards: KanbanCard[];
  prompt?: string;
  model?: string;
}

export interface KanbanBoard {
  path: string;
  title: string;
  lanes: KanbanLane[];
}

export interface KanbanQueryHit {
  path: string;
  board_title: string;
  lane_id: string;
  lane_title: string;
  card_id: string;
  title: string;
  body: string;
  due?: string | null;
  priority?: KanbanCardPriority | null;
  labels: string[];
  assignees: string[];
  status?: KanbanCardStatus | null;
  session_id?: string | null;
}

export interface KanbanQuery {
  text?: string;
  label?: string;
  assignee?: string;
  priority?: KanbanCardPriority;
  status?: KanbanCardStatus;
  due_before?: string;
  due_after?: string;
  lane?: string;
  limit?: number;
}

export async function getVaultKanban(path: string): Promise<KanbanBoard> {
  const res = await fetch(`${BASE}/vault/kanban?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Kanban load error: ${res.status}`);
  return res.json();
}

export async function createVaultKanban(
  path: string,
  opts: { title?: string; columns?: string[] } = {},
): Promise<KanbanBoard> {
  const res = await fetch(`${BASE}/vault/kanban`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, ...opts }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Kanban create error: ${res.status}`);
  }
  return res.json();
}

export async function addVaultKanbanCard(
  path: string,
  card: { lane: string; title: string; body?: string },
): Promise<KanbanCard> {
  const res = await fetch(`${BASE}/vault/kanban/cards?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(card),
  });
  if (!res.ok) throw new Error(`Kanban card create error: ${res.status}`);
  return res.json();
}

export async function patchVaultKanbanCard(
  path: string,
  cardId: string,
  patch: {
    title?: string;
    body?: string;
    lane?: string;
    position?: number;
    session_id?: string | null;
    due?: string | null;
    priority?: KanbanCardPriority | "" | null;
    labels?: string[];
    assignees?: string[];
  },
): Promise<KanbanCard> {
  const res = await fetch(`${BASE}/vault/kanban/cards/${encodeURIComponent(cardId)}?path=${encodeURIComponent(path)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Kanban card patch error: ${res.status}`);
  return res.json();
}

export async function deleteVaultKanbanCard(path: string, cardId: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/kanban/cards/${encodeURIComponent(cardId)}?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban card delete error: ${res.status}`);
}

export async function addVaultKanbanLane(path: string, title: string): Promise<KanbanLane> {
  const res = await fetch(`${BASE}/vault/kanban/lanes?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Kanban lane create error: ${res.status}`);
  return res.json();
}

export async function deleteVaultKanbanLane(path: string, laneId: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/kanban/lanes/${encodeURIComponent(laneId)}?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban lane delete error: ${res.status}`);
}

export async function queryVaultKanban(q: KanbanQuery): Promise<{ hits: KanbanQueryHit[]; count: number }> {
  const res = await fetch(`${BASE}/vault/kanban/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(q),
  });
  if (!res.ok) throw new Error(`Kanban query error: ${res.status}`);
  return res.json();
}

export async function patchVaultKanbanLane(
  path: string,
  laneId: string,
  patch: { title?: string; prompt?: string | null; model?: string | null },
): Promise<KanbanLane> {
  const res = await fetch(
    `${BASE}/vault/kanban/lanes/${encodeURIComponent(laneId)}?path=${encodeURIComponent(path)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
  if (!res.ok) throw new Error(`Kanban lane patch error: ${res.status}`);
  return res.json();
}
