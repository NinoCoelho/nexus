import type { SessionSearchResult, SessionSummary } from "../../api";
import SessionItem from "./SessionItem";

interface Props {
  sessions: SessionSummary[];
  sessionsError: boolean;
  activeSessionId: string | null;
  searchQuery: string;
  searchResults: SessionSearchResult[];
  renamingId: string | null;
  renameValue: string;
  toVaultBusy: Set<string>;
  /** Coordinator master session id — badges the row. */
  masterSessionId?: string | null;
  /** Coordinator display name for the badge (default "Master"). */
  masterLabel?: string;
  /** session ids that belong to a project — project chats live in the
   * Projects view, so both the list and message-search results skip them. */
  projectSessionIds?: Set<string>;
  hasMore: boolean;
  onSearchChange: (q: string) => void;
  onSessionSelect: (id: string) => void;
  onContextMenu: (e: React.MouseEvent, id: string) => void;
  onMenuBtnClick: (e: React.MouseEvent, id: string) => void;
  onTitleDoubleClick: (e: React.MouseEvent, id: string, title: string) => void;
  onRenameChange: (v: string) => void;
  onRenameCommit: (id: string) => void;
  onRenameCancel: () => void;
  onLoadMore: () => void;
}

export default function SessionsPanel({
  sessions, sessionsError, activeSessionId, searchQuery, searchResults,
  renamingId, renameValue, toVaultBusy, masterSessionId, masterLabel = "Master", projectSessionIds,
  hasMore, onSearchChange, onSessionSelect,
  onContextMenu, onMenuBtnClick, onTitleDoubleClick, onRenameChange,
  onRenameCommit, onRenameCancel, onLoadMore,
}: Props) {
  const visibleSearch = projectSessionIds?.size
    ? searchResults.filter((r) => !projectSessionIds.has(r.session_id))
    : searchResults;

  return (
    <div className="sidebar-section sidebar-sessions-section">
      <div className="sidebar-section-label">Chats</div>
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
          {visibleSearch.length === 0 ? (
            <div className="sidebar-search-empty">No results</div>
          ) : (
            visibleSearch.map((r) => (
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
        {sessions.map((s) => (
          <SessionItem
            key={s.id}
            session={s}
            isActive={s.id === activeSessionId}
            isRenaming={renamingId === s.id}
            renameValue={renameValue}
            toVaultBusy={toVaultBusy}
            isMaster={s.id === masterSessionId}
            masterLabel={masterLabel}
            onSelect={() => onSessionSelect(s.id)}
            onContextMenu={(e) => onContextMenu(e, s.id)}
            onMenuBtnClick={(e) => onMenuBtnClick(e, s.id)}
            onTitleDoubleClick={(e) => onTitleDoubleClick(e, s.id, s.title || "")}
            onRenameChange={onRenameChange}
            onRenameCommit={() => onRenameCommit(s.id)}
            onRenameCancel={onRenameCancel}
          />
        ))}
        {sessions.length === 0 && !sessionsError && (
          <div className="sidebar-sessions-empty">No chats yet</div>
        )}
        {hasMore && (
          <button className="sidebar-load-more-btn" onClick={onLoadMore}>
            Load more
          </button>
        )}
      </div>
    </div>
  );
}
