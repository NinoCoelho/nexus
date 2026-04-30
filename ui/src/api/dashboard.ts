// API client for vault data-dashboards (per-database `_data.md`).
import { BASE } from "./base";

export type OperationKind = "chat" | "form";

export interface DashboardOperation {
  id: string;
  label: string;
  kind: OperationKind;
  prompt: string;
  /** Required when kind === "form": vault path of the target table. */
  table?: string;
  /** Optional pre-fill for form kind operations. */
  prefill?: Record<string, unknown>;
  icon?: string;
  order?: number;
}

export interface Dashboard {
  folder: string;
  title: string;
  chat_session_id: string | null;
  operations: DashboardOperation[];
  /** True iff `_data.md` exists on disk; false means caller is seeing defaults. */
  exists: boolean;
  schema_version?: number;
}

export async function fetchDashboard(folder: string): Promise<Dashboard> {
  const res = await fetch(`${BASE}/vault/dashboard?folder=${encodeURIComponent(folder)}`);
  if (!res.ok) throw new Error(`Dashboard load error: ${res.status}`);
  return res.json();
}

export async function putDashboard(
  folder: string,
  patch: Partial<Pick<Dashboard, "title" | "chat_session_id" | "operations">>,
): Promise<Dashboard> {
  const res = await fetch(`${BASE}/vault/dashboard`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, ...patch }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Dashboard PUT error: ${res.status}`);
  }
  return res.json();
}

export async function addOperation(
  folder: string,
  operation: DashboardOperation,
): Promise<Dashboard> {
  const res = await fetch(`${BASE}/vault/dashboard/operations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, operation }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Add operation error: ${res.status}`);
  }
  return res.json();
}

export interface RunOperationResult {
  session_id: string;
  folder: string;
  op_id: string;
  status: "running";
}

export interface OperationRun {
  op_id: string;
  session_id: string;
  status: "done" | "failed";
  error: string | null;
  at: string | null;
}

export interface RunHistory {
  folder: string;
  runs: OperationRun[];
}

/**
 * Fetch the most recent persisted run per dashboard operation.
 *
 * Used on dashboard mount to rehydrate per-chip last-run state — failures
 * stay visible (warning chip) until the user clicks the popup to acknowledge,
 * at which point the UI deletes the session. Successful runs returned here
 * are stale (the run completed but the UI never shipped the success tick) —
 * the UI deletes them straight away as part of its mount cleanup.
 */
export async function fetchRunHistory(folder: string): Promise<RunHistory> {
  const res = await fetch(
    `${BASE}/vault/dashboard/run-history?folder=${encodeURIComponent(folder)}`,
  );
  if (!res.ok) throw new Error(`Run-history error: ${res.status}`);
  return res.json();
}

/**
 * Kick a chat-kind dashboard operation as an ephemeral hidden session.
 *
 * The session is created server-side, marked hidden (so it stays out of
 * the sidebar), and the agent loop runs in the background. The UI tracks
 * progress via `subscribeSessionEvents(session_id)` and opens the result
 * in `CardActivityModal` on demand.
 */
export async function runOperation(
  folder: string,
  opId: string,
): Promise<RunOperationResult> {
  const res = await fetch(`${BASE}/vault/dashboard/run-operation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, op_id: opId }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Run operation error: ${res.status}`);
  }
  return res.json();
}

export async function deleteOperation(folder: string, opId: string): Promise<Dashboard> {
  const res = await fetch(
    `${BASE}/vault/dashboard/operations/${encodeURIComponent(opId)}?folder=${encodeURIComponent(folder)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`Delete operation error: ${res.status}`);
  return res.json();
}

export interface DeleteDatabaseResult {
  folder: string;
  deleted: number;
  paths: string[];
}

/**
 * Permanently delete an entire database (folder + every `.md` inside).
 *
 * Server requires ``confirm`` to equal the folder's basename — pass it
 * explicitly so callers can't trigger a wipe by mistake.
 */
export async function deleteDatabase(
  folder: string,
  confirm: string,
): Promise<DeleteDatabaseResult> {
  const res = await fetch(
    `${BASE}/vault/dashboard?folder=${encodeURIComponent(folder)}&confirm=${encodeURIComponent(confirm)}`,
    { method: "DELETE" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Delete database error: ${res.status}`);
  }
  return res.json();
}
