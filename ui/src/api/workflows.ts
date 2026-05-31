import { BASE } from "./base";
import type {
  WorkflowSummary,
  WorkflowDef,
  WorkflowRun,
  RunDetail,
  ToolInfo,
  EventType,
  VaultFolder,
} from "../types/workflow";

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listWorkflowTools(): Promise<ToolInfo[]> {
  const res = await fetch(`${BASE}/workflows/tools`);
  const data = await _json<{ tools: ToolInfo[] }>(res);
  return data.tools;
}

export async function listWorkflows(): Promise<WorkflowSummary[]> {
  const res = await fetch(`${BASE}/workflows`);
  const data = await _json<{ workflows: WorkflowSummary[] }>(res);
  return data.workflows;
}

export async function getWorkflow(
  path: string,
): Promise<{ definition: WorkflowDef; runs: WorkflowRun[] }> {
  const res = await fetch(`${BASE}/workflows/${encodeURIComponent(path)}`);
  return _json(res);
}

export async function createWorkflow(
  path: string,
  title = "Untitled Workflow",
): Promise<{ ok: boolean; path: string }> {
  const res = await fetch(`${BASE}/workflows`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, title }),
  });
  return _json(res);
}

export async function updateWorkflow(
  path: string,
  updates: Partial<{
    title: string;
    enabled: boolean;
    triggers: Record<string, unknown>[];
    variables: Record<string, string>;
    steps: Record<string, unknown>[];
  }>,
): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/workflows/${encodeURIComponent(path)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  return _json(res);
}

export async function deleteWorkflow(path: string): Promise<{ ok: boolean }> {
  const res = await fetch(`${BASE}/workflows/${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  return _json(res);
}

export async function runWorkflow(
  path: string,
  payload?: Record<string, unknown>,
): Promise<WorkflowRun> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload }),
    },
  );
  return _json(res);
}

export async function listRuns(
  path: string,
  limit = 50,
): Promise<WorkflowRun[]> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/runs?limit=${limit}`,
  );
  const data = await _json<{ runs: WorkflowRun[] }>(res);
  return data.runs;
}

export async function getRun(
  path: string,
  runId: string,
): Promise<RunDetail> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/runs/${runId}`,
  );
  return _json(res);
}

export async function clearRuns(
  path: string,
  statuses?: string[],
): Promise<{ deleted: number }> {
  const params = statuses ? `?statuses=${statuses.join(",")}` : "";
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/runs${params}`,
    { method: "DELETE" },
  );
  return _json(res);
}

export interface WebhookUrlResponse {
  webhooks: { trigger_id: string; token: string; url: string | null; has_broker?: boolean }[];
  broker_connected: boolean;
  signed_in: boolean;
  broker_ok: boolean | null;
  broker_error: string | null;
}

export async function getWebhookUrl(path: string): Promise<WebhookUrlResponse> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/webhook-url`,
  );
  return _json(res);
}

export async function startDebug(
  path: string,
  payload?: Record<string, unknown>,
): Promise<WorkflowRun> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/debug`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload }),
    },
  );
  return _json(res);
}

export async function debugContinue(
  path: string,
  runId: string,
  stepId?: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/debug/${runId}/continue`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(stepId ? { step_id: stepId } : {}),
    },
  );
  return _json(res);
}

export async function debugRerunStep(
  path: string,
  runId: string,
  stepId: string,
): Promise<import("../types/workflow").StepRun> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/debug/${runId}/step/${stepId}/rerun`,
    { method: "POST" },
  );
  return _json(res);
}

export async function debugCancel(
  path: string,
  runId: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/debug/${runId}/cancel`,
    { method: "POST" },
  );
  return _json(res);
}

export async function getWorkflowSchema(
  path: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/schema`,
  );
  if (!res.ok) return {};
  return _json(res);
}

export async function testTrigger(
  path: string,
  triggerId: string,
): Promise<{
  trigger_payload: Record<string, unknown>;
  schema?: unknown;
  sample?: unknown;
}> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/test-trigger`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger_id: triggerId }),
    },
  );
  return _json(res);
}

export async function testStep(
  path: string,
  stepId: string,
  triggerPayload: Record<string, unknown>,
  stepOutputs: Record<string, unknown>,
): Promise<{
  step_id: string;
  step_name: string;
  status: string;
  input_resolved?: Record<string, unknown>;
  output?: unknown;
  error?: string;
  schema?: unknown;
  sample?: unknown;
}> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/test-step`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        step_id: stepId,
        trigger_payload: triggerPayload,
        step_outputs: stepOutputs,
      }),
    },
  );
  return _json(res);
}

export function debugEventsUrl(path: string, runId: string): string {
  return `${BASE}/workflows/${encodeURIComponent(path)}/debug/${runId}/events`;
}

export async function startInteractiveRun(
  path: string,
  payload: Record<string, unknown> = {},
  mode: "trigger" | "all" = "trigger",
  seedFromSamples = false,
  payloadFormat: string = "json",
  payloadRaw: string = "",
): Promise<{ run: WorkflowRun; mode: string }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/interactive-run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        payload,
        mode,
        seed_from_samples: seedFromSamples,
        payload_format: payloadFormat,
        payload_raw: payloadRaw,
      }),
    },
  );
  return _json(res);
}

export async function getInteractiveState(
  path: string,
  runId: string,
): Promise<{
  run: WorkflowRun;
  steps: import("../types/workflow").StepRun[];
  condition_branches: Record<string, string>;
}> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/interactive-run/${runId}`,
  );
  return _json(res);
}

export async function interactiveExecuteStep(
  path: string,
  runId: string,
  stepId: string,
  stepConfig?: Record<string, unknown>,
): Promise<import("../types/workflow").StepRun> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/interactive-run/${runId}/execute-step/${stepId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step_config: stepConfig || null }),
    },
  );
  return _json(res);
}

export async function interactiveExecuteAll(
  path: string,
  runId: string,
): Promise<WorkflowRun> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/interactive-run/${runId}/execute-all`,
    { method: "POST" },
  );
  return _json(res);
}

export async function interactiveCancel(
  path: string,
  runId: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/interactive-run/${runId}/cancel`,
    { method: "POST" },
  );
  return _json(res);
}

export function interactiveEventsUrl(path: string, runId: string): string {
  return `${BASE}/workflows/${encodeURIComponent(path)}/interactive-run/${runId}/events`;
}

export async function getWorkflowSamples(
  path: string,
): Promise<{
  trigger_payload: Record<string, unknown>;
  steps: Record<string, { name: string; slug: string; input_resolved?: unknown; output?: unknown }>;
}> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/samples`,
  );
  if (!res.ok) return { trigger_payload: {}, steps: {} };
  return _json(res);
}

export async function generateScript(
  path: string,
  description: string,
  inputSchema: Record<string, unknown> = {},
  triggerKeys: string[] = [],
): Promise<{ code: string }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/generate-script`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, input_schema: inputSchema, trigger_keys: triggerKeys }),
    },
  );
  return _json(res);
}

export async function resolveTemplate(
  template: string,
  triggerPayload: Record<string, unknown>,
  stepOutputs: Record<string, unknown>,
  variables: Record<string, string>,
): Promise<string> {
  return template.replace(/\{\{([^}]+)\}\}/g, (_match, expr: string) => {
    const parts = expr.trim().split(".");
    let current: unknown;
    if (parts[0] === "trigger") {
      current = triggerPayload;
      parts.shift();
    } else if (parts[0] === "steps") {
      current = stepOutputs;
      parts.shift();
    } else if (parts[0] === "vars") {
      current = variables;
      parts.shift();
    } else {
      return _match;
    }
    for (const part of parts) {
      if (current && typeof current === "object") {
        current = (current as Record<string, unknown>)[part];
      } else {
        return _match;
      }
    }
    return current !== undefined ? JSON.stringify(current) : _match;
  });
}

export async function seedFromRun(
  path: string,
  runId: string,
): Promise<import("../types/workflow").InteractiveRunState> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/seed-from-run/${runId}`,
    { method: "POST" },
  );
  return _json(res);
}

export async function listEventTypes(): Promise<EventType[]> {
  const res = await fetch(`${BASE}/workflows/event-types`);
  const data = await _json<{ event_types: EventType[] }>(res);
  return data.event_types;
}

export async function listVaultFolders(): Promise<VaultFolder[]> {
  const res = await fetch(`${BASE}/vault/folders`);
  const data = await _json<{ folders: VaultFolder[] }>(res);
  return data.folders;
}

export function testTriggerListenUrl(path: string): string {
  return `${BASE}/workflows/${encodeURIComponent(path)}/test-trigger/listen`;
}

export async function cancelTestListener(
  path: string,
  testId: string,
): Promise<{ ok: boolean }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/test-trigger/${testId}`,
    { method: "DELETE" },
  );
  return _json(res);
}

export interface FsWatchFile {
  name: string;
  path: string;
  size: number;
  modified: string;
}

export async function listFsWatchFiles(
  path: string,
  triggerId: string,
): Promise<{ files: FsWatchFile[]; watch_path: string }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/test-trigger/fs-watch-list`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger_id: triggerId }),
    },
  );
  return _json(res);
}

export async function pickFsTestFile(
  path: string,
  triggerId: string,
  filePath: string,
  testId?: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/test-trigger/fs-watch-pick`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger_id: triggerId, file_path: filePath, test_id: testId }),
    },
  );
  return _json(res);
}

export async function brokerDequeue(
  path: string,
  triggerId: string,
): Promise<{ payload: Record<string, unknown> | null; message_id?: string; message?: string }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/test-trigger/broker-dequeue`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trigger_id: triggerId }),
    },
  );
  return _json(res);
}
