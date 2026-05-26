import type { SessionSearchResult, SessionSummary } from "../../api";
import type { ProjectSummary } from "../../api/projects";
import ProjectSection from "./ProjectSection";
import SessionItem from "./SessionItem";

interface Props {
  sessions: SessionSummary[];
  projects: ProjectSummary[];
  sessionsError: boolean;
  activeSessionId: string | null;
  searchQuery: string;
  searchResults: SessionSearchResult[];
  renamingId: string | null;
  renameValue: string;
  toVaultBusy: Set<string>;
  canCreateProject: boolean;
  onSearchChange: (q: string) => void;
  onSessionSelect: (id: string) => void;
  onContextMenu: (e: React.MouseEvent, id: string) => void;
  onMenuBtnClick: (e: React.MouseEvent, id: string) => void;
  onTitleDoubleClick: (e: React.MouseEvent, id: string, title: string) => void;
  onRenameChange: (v: string) => void;
  onRenameCommit: (id: string) => void;
  onRenameCancel: () => void;
  onNewProject: () => void;
  onProjectContextMenu?: (e: React.MouseEvent, projectId: string) => void;
  onNewChatInProject?: (projectId: string) => void;
}

export default function SessionsPanel({
  sessions, projects, sessionsError, activeSessionId, searchQuery, searchResults,
  renamingId, renameValue, toVaultBusy, canCreateProject,
  onSearchChange, onSessionSelect,
  onContextMenu, onMenuBtnClick, onTitleDoubleClick, onRenameChange,
  onRenameCommit, onRenameCancel, onNewProject, onProjectContextMenu,
  onNewChatInProject,
}: Props) {
  const projectMap = new Map<string, SessionSummary[]>();
  const ungrouped: SessionSummary[] = [];

  for (const s of sessions) {
    if (s.project_id) {
      const list = projectMap.get(s.project_id) || [];
      list.push(s);
      projectMap.set(s.project_id, list);
    } else {
      ungrouped.push(s);
    }
  }

  const hasProjects = projects.length > 0 || projectMap.size > 0;

  return (
    <div className="sidebar-section sidebar-sessions-section">
      <div className="sidebar-section-label">Sessions</div>
      <div className="sidebar-search-wrap">
        <input
          id="nx-session-search"
          className="sidebar-search-input"
          type="search"
          placeholder="Search messages…"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          aria-label="Search session messages"
        />
      </div>
      {searchQuery.trim() && (
        <div className="sidebar-search-results">
          {searchResults.length === 0 ? (
            <div className="sidebar-search-empty">No results</div>
          ) : (
            searchResults.map((r) => (
              <button
                key={`${r.session_id}-${r.snippet}`}
                className="sidebar-search-result"
                onClick={() => { onSessionSelect(r.session_id); onSearchChange(""); }}
              >
                <span className="sidebar-search-result-title">{r.title}</span>
                <span
                  className="sidebar-search-result-snippet"
                  dangerouslySetInnerHTML={{ __html: r.snippet.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>") }}
                />
              </button>
            ))
          )}
        </div>
      )}
      {sessionsError && (
        <div className="sidebar-error">Couldn&apos;t load — is the server running?</div>
      )}
      <div className="sidebar-sessions">
        {canCreateProject && (
          <button className="sidebar-new-project-btn" onClick={onNewProject}>
            <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <line x1="10" y1="4" x2="10" y2="16" />
              <line x1="4" y1="10" x2="16" y2="10" />
            </svg>
            New project
          </button>
        )}
        {hasProjects && (
          <>
            {projects.map((p) => {
              const pSessions = projectMap.get(p.id) || [];
              return (
                <ProjectSection
                  key={p.id}
                  project={p}
                  sessions={pSessions}
                  activeSessionId={activeSessionId}
                  renamingId={renamingId}
                  renameValue={renameValue}
                  toVaultBusy={toVaultBusy}
                  onSessionSelect={onSessionSelect}
                  onContextMenu={onContextMenu}
                  onMenuBtnClick={onMenuBtnClick}
                  onTitleDoubleClick={onTitleDoubleClick}
                  onRenameChange={onRenameChange}
                  onRenameCommit={onRenameCommit}
                  onRenameCancel={onRenameCancel}
                  onProjectContextMenu={onProjectContextMenu}
                  onNewChatInProject={onNewChatInProject}
                />
              );
            })}
            {ungrouped.length > 0 && (
              <>
                <div className="sidebar-section-label" style={{ marginTop: 4 }}>Other</div>
                {ungrouped.map((s) => (
                  <SessionItem
                    key={s.id}
                    session={s}
                    isActive={s.id === activeSessionId}
                    isRenaming={renamingId === s.id}
                    renameValue={renameValue}
                    toVaultBusy={toVaultBusy}
                    onSelect={() => onSessionSelect(s.id)}
                    onContextMenu={(e) => onContextMenu(e, s.id)}
                    onMenuBtnClick={(e) => onMenuBtnClick(e, s.id)}
                    onTitleDoubleClick={(e) => onTitleDoubleClick(e, s.id, s.title || "")}
                    onRenameChange={onRenameChange}
                    onRenameCommit={() => onRenameCommit(s.id)}
                    onRenameCancel={onRenameCancel}
                  />
                ))}
              </>
            )}
          </>
        )}
        {!hasProjects && sessions.map((s) => (
          <SessionItem
            key={s.id}
            session={s}
            isActive={s.id === activeSessionId}
            isRenaming={renamingId === s.id}
            renameValue={renameValue}
            toVaultBusy={toVaultBusy}
            onSelect={() => onSessionSelect(s.id)}
            onContextMenu={(e) => onContextMenu(e, s.id)}
            onMenuBtnClick={(e) => onMenuBtnClick(e, s.id)}
            onTitleDoubleClick={(e) => onTitleDoubleClick(e, s.id, s.title || "")}
            onRenameChange={onRenameChange}
            onRenameCommit={() => onRenameCommit(s.id)}
            onRenameCancel={onRenameCancel}
          />
        ))}
        {!hasProjects && sessions.length === 0 && !sessionsError && (
          <div className="sidebar-sessions-empty">No sessions yet</div>
        )}
      </div>
    </div>
  );
}
