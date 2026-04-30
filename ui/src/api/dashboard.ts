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
