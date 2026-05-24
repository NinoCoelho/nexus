// API client for per-folder ontology-isolated knowledge graphs.
// Backed by /graph/folder/* endpoints. See the corresponding routes in
// agent/src/nexus/server/routes/graph.py.

import { BASE } from "./base";

export interface FolderOntology {
  entity_types: string[];
  relations: string[];
  allow_custom_relations: boolean;
}

export interface FolderOpenResult {
  path: string;
  abs_path: string;
  exists: boolean;
  ontology: FolderOntology | null;
  ontology_hash: string | null;
  embedder_id: string | null;
  extractor_id: string | null;
  file_count: number;
  last_indexed_at: number | null;
}

export interface FolderStaleResult {
  added: string[];
  changed: string[];
  removed: string[];
}

export interface FolderTab {
  path: string;
  label: string;
}

export interface FolderSubgraphNode {
  id: number;
  name: string;
  type: string;
  degree: number;
}

export interface FolderSubgraphEdge {
  source: number;
  target: number;
  relation: string;
  strength: number;
}

export interface FolderSubgraphData {
  nodes: FolderSubgraphNode[];
  edges: FolderSubgraphEdge[];
  ontology?: FolderOntology;
  exists?: boolean;
}

export type FolderIndexEvent =
  | { type: "phase"; phase: "loading-embedder" | "scanning" | "extracting" | "writing" }
  | { type: "status"; message: string }
  | {
      type: "file";
      path: string;
      files_done: number;
      files_total: number;
      entities: number;
      triples: number;
      skipped: boolean;
    }
  | { type: "error"; path?: string; detail: string }
  | {
      type: "stats";
      files_done: number;
      files_total: number;
      files_indexed: number;
      files_skipped: number;
      entities: number;
      triples: number;
      elapsed_s: number;
    }
  | { type: "done" };

export interface OntologyWizardQuestion {
  text: string;
  choices: string[];
}

export type OntologyWizardEvent =
  | { type: "wizard_id"; wizard_id: string }
  | { type: "status"; message: string }
  | { type: "proposal"; ontology: FolderOntology; rationale: string; turn: number }
  | { type: "question"; question: OntologyWizardQuestion; turn: number }
  | { type: "done"; ontology: FolderOntology; rationale: string }
  | { type: "error"; detail: string };

export async function openFolderGraph(path: string): Promise<FolderOpenResult> {
  const res = await fetch(`${BASE}/graph/folder/open`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) throw new Error(`Open folder graph failed: ${res.status}`);
  return res.json() as Promise<FolderOpenResult>;
}

export async function getFolderStale(path: string): Promise<FolderStaleResult> {
  const res = await fetch(`${BASE}/graph/folder/stale?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Stale check failed: ${res.status}`);
  return res.json() as Promise<FolderStaleResult>;
}

export async function getFolderOntology(path: string): Promise<{
  ontology: FolderOntology | null;
  ontology_hash: string | null;
  exists: boolean;
}> {
  const res = await fetch(`${BASE}/graph/folder/ontology?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`Get ontology failed: ${res.status}`);
  return res.json();
}

export async function putFolderOntology(
  path: string,
  ontology: FolderOntology,
): Promise<{ saved: boolean; ontology_hash: string }> {
  const res = await fetch(`${BASE}/graph/folder/ontology`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, ontology }),
  });
  if (!res.ok) throw new Error(`Save ontology failed: ${res.status}`);
  return res.json();
}

export async function getFolderFullSubgraph(
  path: string,
  maxNodes = 500,
): Promise<FolderSubgraphData> {
  const res = await fetch(
    `${BASE}/graph/folder/full-subgraph?path=${encodeURIComponent(path)}&max_nodes=${maxNodes}`,
  );
  if (!res.ok) throw new Error(`Full subgraph failed: ${res.status}`);
  return res.json();
}

export async function getFolderSubgraph(
  path: string,
  seed: number,
  hops = 2,
): Promise<FolderSubgraphData> {
  const res = await fetch(
    `${BASE}/graph/folder/subgraph?path=${encodeURIComponent(path)}&seed=${seed}&hops=${hops}`,
  );
  if (!res.ok) throw new Error(`Subgraph failed: ${res.status}`);
  return res.json();
}

export async function deleteFolderGraph(path: string): Promise<{ removed: boolean }> {
  const res = await fetch(`${BASE}/graph/folder?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
  return res.json();
}

export async function getFolderTabs(): Promise<FolderTab[]> {
  const res = await fetch(`${BASE}/graph/folder/tabs`);
  if (!res.ok) throw new Error(`Tabs fetch failed: ${res.status}`);
  const data = await res.json();
  return (data.tabs ?? []) as FolderTab[];
}

export async function setFolderTabs(tabs: FolderTab[]): Promise<FolderTab[]> {
  const res = await fetch(`${BASE}/graph/folder/tabs`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tabs }),
  });
  if (!res.ok) throw new Error(`Tabs save failed: ${res.status}`);
  const data = await res.json();
  return (data.tabs ?? []) as FolderTab[];
}

// ---------- SSE consumers ----------

async function* readSse(res: Response): AsyncIterable<{ event: string; data: unknown }> {
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = String((body as { detail: unknown }).detail);
      }
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";
    for (const frame of frames) {
      if (!frame.trim()) continue;
      let event = "message";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
      }
      if (!dataLine) continue;
      try {
        yield { event, data: JSON.parse(dataLine) };
      } catch { /* malformed frame — skip */ }
    }
  }
}

export async function indexFolderStream(
  path: string,
  onEvent: (e: FolderIndexEvent) => void,
  opts: { full?: boolean; ontology?: FolderOntology; signal?: AbortSignal } = {},
): Promise<void> {
  const res = await fetch(`${BASE}/graph/folder/index`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, full: !!opts.full, ontology: opts.ontology }),
    signal: opts.signal,
  });
  for await (const { event, data } of readSse(res)) {
    const d = data as Record<string, unknown>;
    switch (event) {
      case "phase":
        onEvent({
          type: "phase",
          phase: d.phase as "loading-embedder" | "scanning" | "extracting" | "writing",
        });
        break;
      case "status":
        onEvent({ type: "status", message: String(d.message ?? "") });
        break;
      case "file":
        onEvent({ type: "file", ...(d as object) } as FolderIndexEvent);
        break;
      case "error":
        onEvent({ type: "error", path: d.path as string | undefined, detail: String(d.detail ?? "") });
        break;
      case "stats":
        onEvent({ type: "stats", ...(d as object) } as FolderIndexEvent);
        break;
      case "done":
        onEvent({ type: "done" });
        break;
    }
  }
}

export async function startOntologyWizard(
  path: string,
  onEvent: (e: OntologyWizardEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/graph/folder/ontology-wizard/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
    signal,
  });
  for await (const { event, data } of readSse(res)) {
    const d = data as Record<string, unknown>;
    switch (event) {
      case "wizard_id":
        onEvent({ type: "wizard_id", wizard_id: String(d.wizard_id) });
        break;
      case "status":
        onEvent({ type: "status", message: String(d.message ?? "") });
        break;
      case "proposal":
        onEvent({
          type: "proposal",
          ontology: d.ontology as FolderOntology,
          rationale: String(d.rationale ?? ""),
          turn: Number(d.turn ?? 0),
        });
        break;
      case "question":
        onEvent({
          type: "question",
          question: d.question as OntologyWizardQuestion,
          turn: Number(d.turn ?? 0),
        });
        break;
      case "done":
        onEvent({
          type: "done",
          ontology: d.ontology as FolderOntology,
          rationale: String(d.rationale ?? ""),
        });
        break;
      case "error":
        onEvent({ type: "error", detail: String(d.detail ?? "") });
        break;
    }
  }
}

export async function answerOntologyWizard(
  wizardId: string,
  answer: string,
): Promise<{ accepted: boolean }> {
  const res = await fetch(`${BASE}/graph/folder/ontology-wizard/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wizard_id: wizardId, answer }),
  });
  if (!res.ok) throw new Error(`Wizard answer failed: ${res.status}`);
  return res.json();
}
