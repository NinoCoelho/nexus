import { BASE } from "./base";
import type {
  WorkflowSummary,
  WorkflowDef,
  WorkflowRun,
  RunDetail,
} from "../types/workflow";

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(await res.text());
  return res.json();
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

export async function getWebhookUrl(
  path: string,
): Promise<{ webhooks: { trigger_id: string; token: string; url: string }[] }> {
  const res = await fetch(
    `${BASE}/workflows/${encodeURIComponent(path)}/webhook-url`,
  );
  return _json(res);
}
