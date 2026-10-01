/**
 * ProjectsPane — the "Projects" main-area surface. With a project selected:
 * its workspace — metadata (inline edit via the project modal), instructions
 * preview, vault folder link, Telegram binding chip, and the project's chats
 * with title + message search. Without one: the project card grid. Selection
 * is lifted to App (the sidebar ProjectsListPanel shares it). Styling:
 * shared view language (views.css) + workspace bits below.
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, FolderOpen, MessageSquare, Pencil, Search, Send } from "lucide-react";
import {
  getProject,
  getSessions,
  listProjects,
  searchSessions,
  type Project,
  type ProjectSummary,
  type SessionSearchResult,
  type SessionSummary,
  type SessionsResponse,
} from "../../api";
import { getTelegramBindings, type TelegramBindingInfo } from "../../api/telegram";
import ProjectEditModal from "../Sidebar/ProjectEditModal";
import "./ProjectsPane.css";
import "./views.css";

const SESSIONS_BATCH = 200;

interface Props {
  activeSessionId: string | null;
  /** Selected project id — controlled by App (sidebar list shares it). */
  selectedId: string | null;
  onSelectId: (id: string | null) => void;
  onSessionSelect: (id: string) => void;
  onNewChatInProject: (projectId: string) => void;
  onOpenInVault: (path: string) => void;
  /** Bumped when sessions/projects change so the pane refreshes. */
  refreshKey: number;
}

export default function ProjectsPane({
  activeSessionId,
  selectedId,
  onSelectId,
  onSessionSelect,
  onNewChatInProject,
  onOpenInVault,
  refreshKey,
}: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selectedFull, setSelectedFull] = useState<Project | null>(null);
  const [bindings, setBindings] = useState<TelegramBindingInfo[]>([]);
  const [editId, setEditId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [messageHits, setMessageHits] = useState<SessionSearchResult[]>([]);

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {});
    getTelegramBindings().then(setBindings).catch(() => {});
  }, [refreshKey]);

  useEffect(() => {
    getSessions(SESSIONS_BATCH, 0)
      .then(({ sessions: s }: SessionsResponse) => setSessions(s))
      .catch(() => {});
  }, [refreshKey]);

  // Load full metadata (vault path, instructions) for the selected project.
  useEffect(() => {
    if (!selectedId) { setSelectedFull(null); return; }
    let cancelled = false;
    getProject(selectedId)
      .then((p) => { if (!cancelled) setSelectedFull(p); })
      .catch(() => { if (!cancelled) setSelectedFull(null); });
    return () => { cancelled = true; };
  }, [selectedId, refreshKey]);

  // Reset search whenever the selection changes (sidebar or cards).
  useEffect(() => {
    setQuery("");
    setMessageHits([]);
  }, [selectedId]);

  const sessionsByProject = useMemo(() => {
    const map = new Map<string, SessionSummary[]>();
    for (const s of sessions) {
      if (!s.project_id) continue;
      const list = map.get(s.project_id) || [];
      list.push(s);
      map.set(s.project_id, list);
    }
    for (const list of map.values()) {
      list.sort((a, b) => {
        const av = typeof a.updated_at === "number" ? a.updated_at : Date.parse(a.updated_at) / 1000;
        const bv = typeof b.updated_at === "number" ? b.updated_at : Date.parse(b.updated_at) / 1000;
        return bv - av;
      });
    }
    return map;
  }, [sessions]);

  const selected = projects.find((p) => p.id === selectedId) ?? null;
  const selectedSessions = selected ? sessionsByProject.get(selected.id) ?? [] : [];

  // Message-content search (FTS), scoped to the selected project's sessions.
  useEffect(() => {
    if (!selected || !query.trim()) { setMessageHits([]); return; }
    const timer = setTimeout(() => {
      const ids = new Set(selectedSessions.map((s) => s.id));
      searchSessions(query)
        .then((results) => setMessageHits(results.filter((r) => ids.has(r.session_id))))
        .catch(() => setMessageHits([]));
    }, 300);
    return () => clearTimeout(timer);
  }, [query, selected?.id]);

  const titleFiltered = query.trim()
    ? selectedSessions.filter((s) => (s.title ?? "").toLowerCase().includes(query.trim().toLowerCase()))
    : selectedSessions;
  const titleHitIds = new Set(titleFiltered.map((s) => s.id));
  const listedSessions = query.trim() ? titleFiltered : selectedSessions;
  const selectedBindings = selected ? bindings.filter((b) => b.project_id === selected.id) : [];

  const reloadProjects = () => {
    listProjects().then(setProjects).catch(() => {});
    getTelegramBindings().then(setBindings).catch(() => {});
  };

  const editModal = (
    <ProjectEditModal
      open={editId !== null}
      projectId={editId}
      onClose={() => setEditId(null)}
      onSaved={reloadProjects}
    />
  );

  // No selection: the card grid.
  if (!selected) {
    return (
      <div className="view-pane-col">
        <div className="view-head">
          <h2 className="view-title">Projects</h2>
          <p className="view-sub">Workspaces with their own chats, instructions, and vault folder</p>
        </div>
        <div className="view-body">
          {projects.length === 0 ? (
            <div className="view-empty">
              <p>No projects yet</p>
              <p className="view-empty-hint">Create one from the sidebar — Projects → New</p>
            </div>
          ) : (
            <div className="view-grid">
              {projects.map((p) => {
                const count = sessionsByProject.get(p.id)?.length ?? 0;
                return (
                  <button key={p.id} className="view-card" onClick={() => onSelectId(p.id)}>
                    <span className="view-card-dot" style={{ background: p.color || "var(--accent)" }} />
                    <span className="view-card-title">{p.name}</span>
                    {p.description && <span className="view-card-desc">{p.description}</span>}
                    <span className="view-card-meta">
                      {count} {count === 1 ? "chat" : "chats"}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
        {editModal}
      </div>
    );
  }

  // Selection: the workspace.
  return (
    <div className="view-pane-col">
      <div className="view-toolbar">
        <button className="view-back-btn" onClick={() => onSelectId(null)}>
          <ArrowLeft size={13} /> All projects
        </button>
      </div>
      <div className="projects-workspace projects-workspace--solo">
        <div className="projects-workspace-header">
          <span className="view-card-dot" style={{ background: selected.color || "var(--accent)" }} />
          <div className="projects-workspace-title">
            <strong>{selected.name}</strong>
            {selected.description && <span className="projects-card-desc">{selected.description}</span>}
          </div>
          <button className="projects-icon-btn" title="Edit project" onClick={() => setEditId(selected.id)}>
            <Pencil size={13} />
          </button>
          <button className="projects-new-chat-btn" onClick={() => onNewChatInProject(selected.id)}>
            + New chat
          </button>
        </div>

        <div className="projects-workspace-meta">
          {selectedFull?.vault_path && (
            <button
              className="projects-meta-chip"
              title={`Open ${selectedFull.vault_path} in the Vault`}
              onClick={() => onOpenInVault(selectedFull.vault_path!)}
            >
              <FolderOpen size={11} /> {selectedFull.vault_path}
            </button>
          )}
          {selectedBindings.map((b) => (
            <span key={`${b.chat_id}-${b.thread_id}`} className="projects-meta-chip" title={`Telegram ${b.kind} — chat ${b.chat_id}${b.thread_id ? `, topic ${b.thread_id}` : ""}`}>
              <Send size={11} /> Telegram
            </span>
          ))}
        </div>

        {selectedFull?.instructions && (
          <details className="projects-instructions">
            <summary>Instructions</summary>
            <pre>{selectedFull.instructions}</pre>
          </details>
        )}

        <div className="projects-workspace-search">
          <Search size={13} />
          <input
            type="search"
            placeholder="Search chats and messages…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="projects-workspace-sessions">
          {listedSessions.length === 0 && messageHits.length === 0 && (
            <div className="projects-workspace-empty">
              No chats{query.trim() ? " match" : " yet"}
            </div>
          )}
          {listedSessions.map((s) => (
            <button
              key={s.id}
              className={`projects-session-row${activeSessionId === s.id ? " projects-session-row--active" : ""}`}
              onClick={() => onSessionSelect(s.id)}
            >
              <span className="projects-session-title">{s.title || "Untitled"}</span>
              <span className="projects-session-date">{formatDate(s.updated_at)}</span>
            </button>
          ))}
          {messageHits
            .filter((r) => !titleHitIds.has(r.session_id))
            .map((r) => (
              <button
                key={`${r.session_id}-${r.snippet.slice(0, 24)}`}
                className="projects-session-row projects-session-row--hit"
                onClick={() => onSessionSelect(r.session_id)}
              >
                <span className="projects-session-hit">
                  <MessageSquare size={11} />
                  <span
                    className="projects-session-snippet"
                    dangerouslySetInnerHTML={{ __html: r.snippet.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>") }}
                  />
                </span>
              </button>
            ))}
        </div>
      </div>
      {editModal}
    </div>
  );
}

function formatDate(raw: string | number): string {
  const d = typeof raw === "number" ? new Date(raw * 1000) : new Date(raw);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
