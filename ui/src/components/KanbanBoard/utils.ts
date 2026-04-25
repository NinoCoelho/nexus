export function cardPreview(body: string | undefined): string {
  if (!body) return "";
  const para = body.split(/\n\s*\n/)[0] ?? "";
  return para.length > 120 ? para.slice(0, 117) + "…" : para;
}

export const PRIORITY_CLASS: Record<string, string> = {
  low: "kanban-prio kanban-prio--low",
  med: "kanban-prio kanban-prio--med",
  high: "kanban-prio kanban-prio--high",
  urgent: "kanban-prio kanban-prio--urgent",
};

export function dueBadge(due: string | undefined): { label: string; cls: string } | null {
  if (!due) return null;
  // Compare as ISO yyyy-mm-dd against today's date
  const today = new Date().toISOString().slice(0, 10);
  const cls = due < today ? "kanban-due kanban-due--overdue"
    : due === today ? "kanban-due kanban-due--today"
    : "kanban-due";
  return { label: due, cls };
}

export interface BoardFilters {
  text: string;
  label: string;
  priority: string;
  assignee: string;
}
