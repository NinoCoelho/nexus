// StepDetailModal — shared types and type-guard helpers.

export interface TerminalResult {
  ok: boolean;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  stdout_truncated?: boolean;
  stderr_truncated?: boolean;
  duration_ms?: number;
  timed_out?: boolean;
  denied?: boolean;
  error?: string | null;
}

export interface HttpResult {
  status: number | null;
  ok: boolean;
  body: string;
  error?: string | null;
}

export interface VaultEntry { path: string; type: string; size: number; }
export interface SearchMatch { path: string; snippet: string; score: number; }

export function tryParseJson(val: unknown): unknown {
  if (val == null) return null;
  if (typeof val === "object") return val;
  if (typeof val !== "string") return null;
  try {
    return JSON.parse(val);
  } catch {
    return null;
  }
}

export function isTerminalResult(obj: unknown): obj is TerminalResult {
  if (!obj || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return typeof o.ok === "boolean" && "exit_code" in o && "stdout" in o && "stderr" in o;
}

export function isHttpResult(obj: unknown): obj is HttpResult {
  if (!obj || typeof obj !== "object") return false;
  const o = obj as Record<string, unknown>;
  return typeof o.ok === "boolean" && "status" in o && "body" in o;
}

export function isMarkdownLike(s: string): boolean {
  return /^#{1,6} |\*\*[\w]|^- [\w]|^> |```/.test(s);
}

export function metaLabel(tool: string): string {
  const map: Record<string, string> = {
    vault_list: "Listing vault",
    vault_read: "Reading",
    vault_write: "Writing",
    vault_search: "Searching vault",
    vault_tags: "Tags",
    vault_backlinks: "Backlinks",
    kanban_manage: "Kanban",
    http_call: "HTTP Request",
    terminal: "Terminal",
    skill_manage: "Authoring skill",
    skill_view: "Reading skill",
    skills_list: "Listing skills",
  };
  return map[tool] ?? tool.replace(/_/g, " ");
}
