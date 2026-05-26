import { BASE } from "./base";

export interface Project {
  id: string;
  name: string;
  description: string;
  instructions: string;
  vault_path: string;
  color: string;
  icon: string;
  created_at: number;
  updated_at: number;
}

export interface ProjectSummary {
  id: string;
  name: string;
  description: string;
  color: string;
  icon: string;
  session_count: number;
  created_at: number;
  updated_at: number;
}

export async function listProjects(limit = 50): Promise<ProjectSummary[]> {
  const res = await fetch(`${BASE}/projects?limit=${limit}`);
  if (!res.ok) throw new Error("Failed to list projects");
  return res.json();
}

export async function createProject(data: {
  name: string;
  description?: string;
  instructions?: string;
  color?: string;
  icon?: string;
}): Promise<Project> {
  const res = await fetch(`${BASE}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail || "Failed to create project";
    throw new Error(detail);
  }
  return res.json();
}

export async function getProject(id: string): Promise<Project> {
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error("Project not found");
  return res.json();
}

export async function updateProject(
  id: string,
  data: Partial<Pick<Project, "name" | "description" | "instructions" | "color" | "icon">>,
): Promise<void> {
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update project");
}

export async function deleteProject(id: string): Promise<void> {
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error("Failed to delete project");
}

export async function moveSessionToProject(
  projectId: string,
  sessionId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE}/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error("Failed to move session");
}

export async function removeSessionFromProject(
  projectId: string,
  sessionId: string,
): Promise<void> {
  const res = await fetch(
    `${BASE}/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error("Failed to remove session from project");
}
