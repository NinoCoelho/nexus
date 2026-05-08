/**
 * @file API client for vault-native Kanban boards.
 *
 * Kanban boards are Markdown files in the vault with `kanban-plugin: basic` frontmatter.
 * All CRUD operations (cards, lanes, boards) go through the backend at `/vault/kanban/*`,
 * which edits the Markdown file directly — there is no separate store.
 */
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
  board_prompt?: string;
}

export interface KanbanBoardSummary {
  path: string;
  title: string;
}

/**
 * List every Kanban board in the vault as `{path, title}` pairs.
 * Boards are detected by `kanban-plugin:` frontmatter and may live anywhere
 * in the vault folder structure.
 */
export async function listKanbanBoards(): Promise<{ boards: KanbanBoardSummary[]; count: number }> {
  const res = await fetch(`${BASE}/vault/kanban/boards`);
  if (!res.ok) throw new Error(`Kanban list error: ${res.status}`);
  return res.json();
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

/**
 * Load an existing Kanban board from the vault.
 *
 * @param path - Path to the `.md` file relative to the vault root.
 * @throws {Error} If the file does not exist or is not a valid Kanban board.
 */
export async function getVaultKanban(path: string): Promise<KanbanBoard> {
  const res = await fetch(`${BASE}/vault/kanban?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Kanban load error: ${res.status}`);
  return res.json();
}

/**
 * Create a new Kanban board as a Markdown file in the vault.
 *
 * @param path - Destination path for the `.md` file (created by the backend).
 * @param opts - Creation options: `title` and initial `columns` list.
 * @throws {Error} With the server's `detail` message if creation fails.
 */
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

/**
 * Add a new card to a board lane.
 *
 * @param path - Path to the board's `.md` file.
 * @param card - Card data: `lane` (destination lane ID), `title`, and optional `body`.
 */
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

/**
 * Update fields of an existing card (partial PATCH).
 *
 * To move a card between lanes: include `lane` in the patch. To reorder within a lane:
 * include `position` (zero-based index). Explicit `null` values clear the field.
 *
 * @param path - Path to the board's `.md` file.
 * @param cardId - Unique card ID (`<!-- nx:id=<uuid> -->`).
 * @param patch - Fields to update; absent fields are left unchanged.
 */
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

/**
 * Permanently remove a card from the board.
 *
 * @param path - Path to the board's `.md` file.
 * @param cardId - Unique ID of the card to remove.
 */
export async function deleteVaultKanbanCard(path: string, cardId: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/kanban/cards/${encodeURIComponent(cardId)}?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban card delete error: ${res.status}`);
}

/**
 * Add a new lane to the board.
 *
 * @param path - Path to the board's `.md` file.
 * @param title - Lane title (displayed as the column header).
 */
export async function addVaultKanbanLane(path: string, title: string): Promise<KanbanLane> {
  const res = await fetch(`${BASE}/vault/kanban/lanes?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`Kanban lane create error: ${res.status}`);
  return res.json();
}

/**
 * Remove a lane and all its cards from the board.
 *
 * @param path - Path to the board's `.md` file.
 * @param laneId - Unique ID of the lane to remove.
 */
export async function deleteVaultKanbanLane(path: string, laneId: string): Promise<void> {
  const res = await fetch(`${BASE}/vault/kanban/lanes/${encodeURIComponent(laneId)}?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Kanban lane delete error: ${res.status}`);
}

/**
 * Run a cross-board query across all Kanban boards in the vault.
 *
 * Combines text, label, assignee, priority, status, and date-range filters.
 * Useful for aggregated views (e.g. "all urgent cards across any board").
 *
 * @param q - Search criteria; all fields are optional (implicit AND).
 * @returns List of hits with full location info (board, lane, card) and total `count`.
 */
export async function queryVaultKanban(q: KanbanQuery): Promise<{ hits: KanbanQueryHit[]; count: number }> {
  const res = await fetch(`${BASE}/vault/kanban/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(q),
  });
  if (!res.ok) throw new Error(`Kanban query error: ${res.status}`);
  return res.json();
}

/**
 * Update a lane's metadata (title, automation prompt, or default model)
 * and/or move it to a different column index within the board.
 *
 * `prompt` and `model` control the lane's auto-dispatch behaviour: when a card
 * is moved into the lane, the agent can be triggered automatically with that
 * prompt and model. Sending `null` clears the field. To reorder columns,
 * include `position` (zero-based index against the post-removal list).
 *
 * @param path - Path to the board's `.md` file.
 * @param laneId - Unique ID of the lane to update.
 * @param patch - Fields to update.
 */
export async function patchVaultKanbanLane(
  path: string,
  laneId: string,
  patch: { title?: string; prompt?: string | null; model?: string | null; position?: number },
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

export async function cancelVaultKanbanCard(path: string, cardId: string): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/vault/kanban/cards/${encodeURIComponent(cardId)}/cancel?path=${encodeURIComponent(path)}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Kanban card cancel error: ${res.status}`);
  return res.json();
}

export async function retryVaultKanbanCard(path: string, cardId: string): Promise<Record<string, unknown>> {
  const res = await fetch(
    `${BASE}/vault/kanban/cards/${encodeURIComponent(cardId)}/retry?path=${encodeURIComponent(path)}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Kanban card retry error: ${res.status}`);
  return res.json();
}

export interface LaneWebhookInfo {
  enabled: boolean;
  url: string | null;
  token: string | null;
}

export async function getLaneWebhook(path: string, laneId: string): Promise<LaneWebhookInfo> {
  const res = await fetch(
    `${BASE}/vault/kanban/lanes/${encodeURIComponent(laneId)}/webhook?path=${encodeURIComponent(path)}`,
  );
  if (!res.ok) throw new Error(`Lane webhook get error: ${res.status}`);
  return res.json();
}

export async function setLaneWebhook(
  path: string,
  laneId: string,
  patch: { enabled: boolean },
): Promise<LaneWebhookInfo> {
  const res = await fetch(
    `${BASE}/vault/kanban/lanes/${encodeURIComponent(laneId)}/webhook?path=${encodeURIComponent(path)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    },
  );
  if (!res.ok) throw new Error(`Lane webhook set error: ${res.status}`);
  return res.json();
}

export async function patchVaultKanbanBoard(
  path: string,
  patch: { title?: string; board_prompt?: string | null },
): Promise<KanbanBoard> {
  const res = await fetch(`${BASE}/vault/kanban?path=${encodeURIComponent(path)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Kanban board patch error: ${res.status}`);
  return res.json();
}
