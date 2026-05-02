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
  /** Opt-in: when true, chat-kind operations show a plan-review step before
   *  executing. The agent first produces a JSON plan of what it would do;
   *  the user approves, refines, or cancels; only then does the real run
   *  start. Off by default — most ops are routine and a mandatory approval
   *  trains users to rubber-stamp. Reserve for ops with risky writes. */
  preview?: boolean;
}

export type WidgetKind = "chart" | "report" | "kpi";
export type WidgetRefresh = "manual" | "daily";
export type WidgetSize = "sm" | "md" | "lg";

export interface DashboardWidget {
  id: string;
  kind: WidgetKind;
  title: string;
  prompt: string;
  refresh: WidgetRefresh;
  /** ISO 8601 UTC timestamp of the most recent successful refresh, or null. */
  last_refreshed_at: string | null;
  /** Optional size override. When absent, a per-kind default is used at
   *  render time (chart = md, report = md, kpi = sm). */
  size?: WidgetSize;
  order?: number;
}

export interface Dashboard {
  folder: string;
  title: string;
  chat_session_id: string | null;
  operations: DashboardOperation[];
  widgets: DashboardWidget[];
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

/**
 * Plan-only run for an operation marked ``preview: true``. The agent reads
 * the data it needs but doesn't write — instead it emits a JSON ``nexus-plan``
 * fence the UI parses for user approval.
 */
export async function planOperation(
  folder: string,
  opId: string,
): Promise<RunOperationResult> {
  const res = await fetch(`${BASE}/vault/dashboard/run-operation/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, op_id: opId }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Plan operation error: ${res.status}`);
  }
  return res.json();
}

/**
 * Execute an operation against an approved (and possibly edited) plan. The
 * agent receives the plan in its seed and is instructed to execute it.
 */
export async function executeOperation(
  folder: string,
  opId: string,
  approvedPlan: string,
): Promise<RunOperationResult> {
  const res = await fetch(`${BASE}/vault/dashboard/run-operation/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, op_id: opId, approved_plan: approvedPlan }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Execute operation error: ${res.status}`);
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
// ── Widgets ───────────────────────────────────────────────────────────────

export interface RefreshWidgetResult {
  session_id: string;
  folder: string;
  widget_id: string;
  status: "running";
}

export async function addWidget(
  folder: string,
  widget: DashboardWidget,
): Promise<Dashboard> {
  const res = await fetch(`${BASE}/vault/dashboard/widgets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, widget }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Add widget error: ${res.status}`);
  }
  return res.json();
}

export async function deleteWidget(folder: string, widgetId: string): Promise<Dashboard> {
  const res = await fetch(
    `${BASE}/vault/dashboard/widgets/${encodeURIComponent(widgetId)}?folder=${encodeURIComponent(folder)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`Delete widget error: ${res.status}`);
  return res.json();
}

export async function fetchWidgetContent(
  folder: string,
  widgetId: string,
): Promise<{ content: string }> {
  const res = await fetch(
    `${BASE}/vault/dashboard/widgets/${encodeURIComponent(widgetId)}/content?folder=${encodeURIComponent(folder)}`,
  );
  if (!res.ok) throw new Error(`Widget content error: ${res.status}`);
  return res.json();
}

export async function refreshWidget(
  folder: string,
  widgetId: string,
): Promise<RefreshWidgetResult> {
  const res = await fetch(
    `${BASE}/vault/dashboard/widgets/${encodeURIComponent(widgetId)}/refresh`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Refresh widget error: ${res.status}`);
  }
  return res.json();
}

/**
 * Re-run a widget refresh after a failed render with the prior attempt's
 * output and error message embedded in the seed, so the agent can correct
 * its own output (e.g. emit YAML when it had emitted JSON).
 */
export async function refineWidget(
  folder: string,
  widgetId: string,
  previousOutput: string,
  errorMessage: string,
): Promise<RefreshWidgetResult> {
  const res = await fetch(
    `${BASE}/vault/dashboard/widgets/${encodeURIComponent(widgetId)}/refine`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder,
        previous_output: previousOutput,
        error_message: errorMessage,
      }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Refine widget error: ${res.status}`);
  }
  return res.json();
}

// ── Wizard ────────────────────────────────────────────────────────────────

export type WizardKind = "widget" | "operation";

export interface WizardStartResult {
  session_id: string;
  folder: string;
  kind: WizardKind;
  status: "running";
}

/**
 * Start a back-and-forth design wizard for a widget or operation.
 *
 * Creates a hidden chat session pre-seeded with the wizard's role + the
 * user's initial goal, and kicks the first turn. The UI then drives the
 * conversation via the regular `/chat/stream` endpoint with the returned
 * `session_id`. The wizard is instructed to ask at most one clarifying
 * question per turn (max 2 total) and emit a fenced JSON proposal that
 * the UI parses and commits via addWidget / addOperation.
 */
export async function startWizard(
  folder: string,
  kind: WizardKind,
  goal: string,
): Promise<WizardStartResult> {
  const res = await fetch(`${BASE}/vault/dashboard/wizard/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder, kind, goal }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Wizard start error: ${res.status}`);
  }
  return res.json();
}

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
