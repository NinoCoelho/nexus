/**
 * BotsPane — the "Bots" main-area surface. With a bot selected: its
 * workspace — persona, metadata, active chats (with archive/cleanup), an
 * activity tab of internal runs, and restorable archived chats. Without
 * one: the bot card grid. The coordinator ("main") bot renders a
 * read-only coordinator info panel instead of the editable workspace.
 * Selection is lifted to App (the sidebar BotsListPanel shares it).
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, MessageSquare, Search } from "lucide-react";
import {
  archiveSession,
  botCleanup,
  deleteSession,
  getBotActivity,
  getBots,
  getConfig,
  getProject,
  getSessions,
  searchSessions,
  type Bot,
  type BotActivity,
  type Project,
  type SessionSearchResult,
  type SessionSummary,
} from "../../api";
import ProjectEditModal from "../Sidebar/ProjectEditModal";
import DeleteBotModal from "../Sidebar/DeleteBotModal";
import Modal, { type ModalProps } from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import "./BotsPane.css";
import "./views.css";

const SESSIONS_BATCH = 200;
const ARCHIVED_BATCH = 100;

interface Props {
  activeSessionId: string | null;
  /** Selected bot id ("main" = coordinator) — controlled by App. */
  selectedId: string | null;
  onSelectId: (id: string | null) => void;
  /** Opens a chat; callers pass the owning bot so App routes correctly. */
  onSessionSelect: (id: string, projectId?: string | null) => void;
  /** New chat inside a bot (null = main/unprojected). */
  onNewChatInBot: (botId: string | null) => void;
  /** Opens a hidden internal run session read-only in the chat view. */
  onOpenActivitySession: (sessionId: string) => void;
  onOpenInVault: (path: string) => void;
  /** Bumped when sessions/bots change so the pane refreshes. */
  refreshKey: number;
  /** Bump after mutations so the rest of the app refreshes too. */
  onRefresh: () => void;
}

function formatDate(raw: string | number): string {
  const d = typeof raw === "number" ? new Date(raw * 1000) : new Date(raw);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function updatedSec(s: SessionSummary): number {
  return typeof s.updated_at === "number" ? s.updated_at : Date.parse(s.updated_at) / 1000;
}

export default function BotsPane({
  activeSessionId,
  selectedId,
  onSelectId,
  onSessionSelect,
  onNewChatInBot,
  onOpenActivitySession,
  onOpenInVault,
  refreshKey,
  onRefresh,
}: Props) {
  const toast = useToast();
  const [bots, setBots] = useState<Bot[]>([]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [archived, setArchived] = useState<SessionSummary[]>([]);
  const [selectedFull, setSelectedFull] = useState<Project | null>(null);
  const [coordinatorPersona, setCoordinatorPersona] = useState<{ name: string; persona: string } | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [messageHits, setMessageHits] = useState<SessionSearchResult[]>([]);
  const [tab, setTab] = useState<"chats" | "activity">("chats");
  const [activity, setActivity] = useState<BotActivity[] | null>(null);
  const [cleanupBusy, setCleanupBusy] = useState(false);
  const [cleanupConfirm, setCleanupConfirm] = useState<ModalProps | null>(null);
  const [archiveBusy, setArchiveBusy] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<Bot | null>(null);

  useEffect(() => {
    getBots().then(setBots).catch(() => {});
  }, [refreshKey]);

  useEffect(() => {
    getSessions(SESSIONS_BATCH, 0)
      .then(({ sessions: s }) => setSessions(s))
      .catch(() => {});
    getSessions(ARCHIVED_BATCH, 0, { archived: true })
      .then(({ sessions: s }) => setArchived(s))
      .catch(() => {});
  }, [refreshKey]);

  const isMain = selectedId === "main";
  const selected = bots.find((b) => b.id === selectedId) ?? null;

  useEffect(() => {
    setSelectedFull(null);
    setTab("chats");
    setQuery("");
    setMessageHits([]);
    setActivity(null);
    if (!selectedId) return;
    if (selectedId === "main") {
      getConfig()
        .then((cfg) => setCoordinatorPersona({
          name: cfg.coordinator?.name || "Main",
          persona: cfg.coordinator?.persona || "",
        }))
        .catch(() => setCoordinatorPersona(null));
      return;
    }
    let cancelled = false;
    getProject(selectedId)
      .then((p) => { if (!cancelled) setSelectedFull(p); })
      .catch(() => { if (!cancelled) setSelectedFull(null); });
    return () => { cancelled = true; };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || selectedId === "main") { setActivity(null); return; }
    let cancelled = false;
    getBotActivity(selectedId)
      .then((rows) => { if (!cancelled) setActivity(rows); })
      .catch(() => { if (!cancelled) setActivity(null); });
    return () => { cancelled = true; };
  }, [selectedId, refreshKey]);

  const botSessions = useMemo(() => {
    if (!selectedId) return [];
    const own = isMain
      ? sessions.filter((s) => !s.project_id)
      : sessions.filter((s) => s.project_id === selectedId);
    return own.sort((a, b) => updatedSec(b) - updatedSec(a));
  }, [sessions, selectedId, isMain]);

  const botArchived = useMemo(() => {
    if (!selectedId) return [];
    return isMain
      ? archived.filter((s) => !s.project_id)
      : archived.filter((s) => s.project_id === selectedId);
  }, [archived, selectedId, isMain]);

  // Message-content search (FTS), scoped to the selected bot's sessions.
  useEffect(() => {
    if (!selectedId || !query.trim()) { setMessageHits([]); return; }
    const timer = setTimeout(() => {
      const ids = new Set(botSessions.map((s) => s.id));
      searchSessions(query)
        .then((results) => setMessageHits(results.filter((r) => ids.has(r.session_id))))
        .catch(() => setMessageHits([]));
    }, 300);
    return () => clearTimeout(timer);
  }, [query, selectedId, botSessions]);

  const titleFiltered = query.trim()
    ? botSessions.filter((s) => (s.title ?? "").toLowerCase().includes(query.trim().toLowerCase()))
    : botSessions;
  const titleHitIds = new Set(titleFiltered.map((s) => s.id));
  const listedSessions = query.trim() ? titleFiltered : botSessions;

  const markBusy = (id: string, busy: boolean) => {
    setArchiveBusy((prev) => {
      const next = new Set(prev);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const handleArchive = async (s: SessionSummary) => {
    markBusy(s.id, true);
    try {
      await archiveSession(s.id, true);
      toast.success("Chat archived", { detail: s.title || "Untitled" });
      onRefresh();
    } catch {
      toast.error("Couldn't archive the chat");
    } finally {
      markBusy(s.id, false);
    }
  };

  const handleRestore = async (s: SessionSummary) => {
    markBusy(s.id, true);
    try {
      await archiveSession(s.id, false);
      toast.success("Chat restored", { detail: s.title || "Untitled" });
      onRefresh();
    } catch {
      toast.error("Couldn't restore the chat");
    } finally {
      markBusy(s.id, false);
    }
  };

  const handleDeleteArchived = async (s: SessionSummary) => {
    markBusy(s.id, true);
    try {
      await deleteSession(s.id);
      toast.success("Chat deleted", { detail: s.title || "Untitled" });
      onRefresh();
    } catch {
      toast.error("Couldn't delete the chat");
    } finally {
      markBusy(s.id, false);
    }
  };

  const handleCleanupPreview = async () => {
    if (!selectedId || selectedId === "main" || cleanupBusy) return;
    setCleanupBusy(true);
    try {
      const preview = await botCleanup(selectedId, { keep: 1, dry_run: true });
      if (preview.archived.length === 0) {
        toast.info("Nothing to clean up — this bot has a single active chat.");
        return;
      }
      setCleanupConfirm({
        kind: "confirm",
        title: "Archive old chats",
        message: `Archive ${preview.archived.length} ${preview.archived.length === 1 ? "chat" : "chats"}? The most recent one stays active. Archived chats remain restorable below.`,
        confirmLabel: "Archive",
        onCancel: () => setCleanupConfirm(null),
        onSubmit: () => {
          setCleanupConfirm(null);
          void (async () => {
            try {
              const result = await botCleanup(selectedId, { keep: 1 });
              toast.success(`Archived ${result.archived.length} ${result.archived.length === 1 ? "chat" : "chats"}`);
              onRefresh();
            } catch (e) {
              toast.error("Cleanup failed", { detail: e instanceof Error ? e.message : undefined });
            }
          })();
        },
      });
    } catch (e) {
      toast.error("Cleanup preview failed", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setCleanupBusy(false);
    }
  };

  const reloadBots = () => {
    getBots().then(setBots).catch(() => {});
  };

  const editModal = (
    <ProjectEditModal
      open={editId !== null}
      projectId={editId}
      onClose={() => setEditId(null)}
      onSaved={() => { reloadBots(); onRefresh(); }}
    />
  );

  // No selection: the card grid.
  if (!selectedId) {
    return (
      <div className="view-pane-col">
        <div className="view-head">
          <h2 className="view-title">Bots</h2>
          <p className="view-sub">Chat partners with their own persona, instructions, and chats — @mention them from any conversation</p>
        </div>
        <div className="view-body">
          {bots.length === 0 ? (
            <div className="view-empty">
              <p>No bots yet</p>
              <p className="view-empty-hint">Create one from the sidebar — Bots → New bot</p>
            </div>
          ) : (
            <div className="view-grid">
              {bots.map((b) => {
                const count = b.kind === "coordinator"
                  ? sessions.filter((s) => !s.project_id).length
                  : sessions.filter((s) => s.project_id === b.id).length;
                return (
                  <button key={b.id} className="view-card" onClick={() => onSelectId(b.id)}>
                    <span className="view-card-dot" style={{ background: b.color || "var(--accent)" }} />
                    <span className="view-card-title">{b.name}</span>
                    <span className="bots-card-desc">@{b.handle || b.id}{b.description ? ` — ${b.description}` : ""}</span>
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

  const handleLabel = selected?.handle || (isMain ? "main" : selectedId);
  const displayName = isMain ? coordinatorPersona?.name ?? selected?.name ?? "Main" : selected?.name ?? selectedFull?.name ?? selectedId;
  const persona = isMain ? coordinatorPersona?.persona ?? selected?.persona ?? "" : selected?.persona ?? selectedFull?.persona ?? "";
  const mainSessionId = selected?.kind === "coordinator" ? selected.session_id : "";

  const archivedSection = (
    <details className="bots-archived">
      <summary>Archived chats ({botArchived.length})</summary>
      {botArchived.length === 0 ? (
        <div className="bots-archived-empty">No archived chats</div>
      ) : (
        <div className="bots-archived-rows">
          {botArchived.map((s) => (
            <div key={s.id} className="bots-archived-row">
              <span className="bots-session-main">
                <span className="bots-session-title">{s.title || "Untitled"}</span>
                <span className="bots-session-date">{formatDate(s.updated_at)}</span>
              </span>
              <span className="bots-session-actions">
                <button
                  className="bots-session-action"
                  disabled={archiveBusy.has(s.id)}
                  onClick={() => void handleRestore(s)}
                >
                  Restore
                </button>
                <button
                  className="bots-session-action bots-session-action--danger"
                  disabled={archiveBusy.has(s.id)}
                  onClick={() => void handleDeleteArchived(s)}
                >
                  Delete
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
    </details>
  );

  // The coordinator "main" bot: read-only info + its chats/archived list.
  if (isMain) {
    return (
      <div className="view-pane-col">
        <div className="view-toolbar">
          <button className="view-back-btn" onClick={() => onSelectId(null)}>
            <ArrowLeft size={13} /> All bots
          </button>
        </div>
        <div className="bots-workspace">
          <div className="bots-workspace-header">
            <span className="view-card-dot" style={{ background: "var(--accent)" }} />
            <div className="bots-workspace-title">
              <strong>{displayName}</strong>
              <span className="bots-card-desc">Your main chat — unprojected conversations and the coordinator</span>
            </div>
            <span className="bots-handle-chip">@{handleLabel}</span>
            <button
              className="bots-new-chat-btn"
              onClick={() => onNewChatInBot(null)}
            >
              + New chat
            </button>
          </div>
          <div className="bots-open-chat-cta">
            <button
              className="bots-open-chat-btn"
              disabled={!mainSessionId}
              title={mainSessionId ? "Open the coordinator master chat" : "Enable the coordinator in Settings → Features"}
              onClick={() => mainSessionId && onSessionSelect(mainSessionId, null)}
            >
              <MessageSquare size={13} /> Open master chat
            </button>
          </div>
          {(persona || coordinatorPersona) && (
            <details className="bots-persona" open={!!persona}>
              <summary>Persona</summary>
              <pre>{persona || "No persona set."}</pre>
            </details>
          )}
          {coordinatorPersona && (
            <div className="bots-persona-note">The coordinator's name and persona are edited in Settings → Features → Coordinator.</div>
          )}
          <div className="bots-chats-toolbar">
            <span className="bots-chats-toolbar-label">Chats</span>
          </div>
          <div className="bots-session-list">
            {botSessions.length === 0 && <div className="bots-workspace-empty">No chats yet</div>}
            {botSessions.map((s) => (
              <div
                key={s.id}
                className={`bots-session-row${activeSessionId === s.id ? " bots-session-row--active" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => onSessionSelect(s.id, null)}
                onKeyDown={(e) => { if (e.key === "Enter") onSessionSelect(s.id, null); }}
              >
                <span className="bots-session-main">
                  <span className="bots-session-title">
                    {s.title || "Untitled"}
                    {!!s.team && <span className="bots-team-tag" title="Group chat (team)">team</span>}
                  </span>
                  <span className="bots-session-date">{formatDate(s.updated_at)}</span>
                </span>
                <span className="bots-session-actions">
                  <button
                    className="bots-session-action"
                    disabled={archiveBusy.has(s.id)}
                    onClick={(e) => { e.stopPropagation(); void handleArchive(s); }}
                  >
                    Archive
                  </button>
                </span>
              </div>
            ))}
          </div>
          {archivedSection}
        </div>
        {editModal}
      </div>
    );
  }

  // A project-backed bot: the editable workspace.
  return (
    <div className="view-pane-col">
      <div className="view-toolbar">
        <button className="view-back-btn" onClick={() => onSelectId(null)}>
          <ArrowLeft size={13} /> All bots
        </button>
      </div>
      <div className="bots-workspace">
        <div className="bots-workspace-header">
          <span className="view-card-dot" style={{ background: selected?.color || "var(--accent)" }} />
          <div className="bots-workspace-title">
            <strong>{displayName}</strong>
            {(selected?.description || selectedFull?.description) && (
              <span className="bots-card-desc">{selected?.description ?? selectedFull?.description}</span>
            )}
          </div>
          <span className="bots-handle-chip">@{handleLabel}</span>
          <button className="bots-icon-btn" title="Edit bot" onClick={() => setEditId(selectedId)}>
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M11.5 2.5l2 2-7.5 7.5-2.5.5.5-2.5z" />
              <path d="M10 4l2 2" />
            </svg>
          </button>
          <button className="bots-new-chat-btn" onClick={() => onNewChatInBot(selectedId)}>
            + New chat
          </button>
        </div>

        <div className="bots-workspace-meta">
          {selectedFull?.vault_path && (
            <button
              className="bots-meta-chip"
              title={`Open ${selectedFull.vault_path} in the Vault`}
              onClick={() => onOpenInVault(selectedFull.vault_path!)}
            >
              Vault folder
            </button>
          )}
        </div>

        {persona && (
          <details className="bots-persona">
            <summary>Persona — how this bot behaves</summary>
            <pre>{persona}</pre>
          </details>
        )}
        {selectedFull?.instructions && (
          <details className="bots-instructions">
            <summary>Instructions</summary>
            <pre>{selectedFull.instructions}</pre>
          </details>
        )}

        <div className="bots-tabs">
          <button
            className={`bots-tab${tab === "chats" ? " bots-tab--active" : ""}`}
            onClick={() => setTab("chats")}
          >
            Chats
          </button>
          <button
            className={`bots-tab${tab === "activity" ? " bots-tab--active" : ""}`}
            onClick={() => setTab("activity")}
          >
            Activity
          </button>
        </div>

        {tab === "chats" ? (
          <>
            <div className="bots-workspace-search">
              <Search size={13} />
              <input
                type="search"
                placeholder="Search chats and messages…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="bots-chats-toolbar">
              <span className="bots-chats-toolbar-label">
                {botSessions.length} active {botSessions.length === 1 ? "chat" : "chats"}
              </span>
              <button
                className="bots-cleanup-btn"
                disabled={cleanupBusy || botSessions.length <= 1}
                title="Archive all but the most recent chat"
                onClick={() => void handleCleanupPreview()}
              >
                Clean up… (archive all but latest)
              </button>
            </div>
            <div className="bots-session-list">
              {listedSessions.length === 0 && messageHits.length === 0 && (
                <div className="bots-workspace-empty">
                  No chats{query.trim() ? " match" : " yet"}
                </div>
              )}
              {listedSessions.map((s) => (
                <div
                  key={s.id}
                  className={`bots-session-row${activeSessionId === s.id ? " bots-session-row--active" : ""}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSessionSelect(s.id, selectedId)}
                  onKeyDown={(e) => { if (e.key === "Enter") onSessionSelect(s.id, selectedId); }}
                >
                  <span className="bots-session-main">
                    <span className="bots-session-title">
                      {s.title || "Untitled"}
                      {!!s.team && <span className="bots-team-tag" title="Group chat (team)">team</span>}
                    </span>
                    <span className="bots-session-date">{formatDate(s.updated_at)}</span>
                  </span>
                  <span className="bots-session-actions">
                    <button
                      className="bots-session-action"
                      disabled={archiveBusy.has(s.id)}
                      onClick={(e) => { e.stopPropagation(); void handleArchive(s); }}
                    >
                      Archive
                    </button>
                  </span>
                </div>
              ))}
              {messageHits
                .filter((r) => !titleHitIds.has(r.session_id))
                .map((r) => (
                  <button
                    key={`${r.session_id}-${r.snippet.slice(0, 24)}`}
                    className="bots-session-row"
                    onClick={() => onSessionSelect(r.session_id, selectedId)}
                  >
                    <span className="bots-session-hit">
                      <MessageSquare size={11} />
                      <span
                        className="bots-activity-sub"
                        dangerouslySetInnerHTML={{ __html: r.snippet.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>") }}
                      />
                    </span>
                  </button>
                ))}
            </div>
          </>
        ) : (
          <div className="bots-session-list">
            {activity == null && <div className="bots-workspace-empty">Loading activity…</div>}
            {activity != null && activity.length === 0 && (
              <div className="bots-workspace-empty">
                No internal runs
                <div className="bots-archived-empty" style={{ paddingTop: 8 }}>
                  Workflow and scheduled runs for this bot appear here.
                </div>
              </div>
            )}
            {activity?.map((a) => (
              <button
                key={a.id}
                className="bots-activity-row"
                title="Open read-only"
                onClick={() => onOpenActivitySession(a.id)}
              >
                <span className="bots-activity-main">
                  <span className="bots-activity-title">{a.title || "Untitled run"}</span>
                  <span className="bots-activity-sub">
                    {a.context || "internal run"} · {a.message_count} {a.message_count === 1 ? "message" : "messages"}
                  </span>
                </span>
                {a.origin && <span className="bots-origin-badge">{a.origin.kind}</span>}
                <span className="bots-session-date">{formatDate(a.updated_at)}</span>
              </button>
            ))}
          </div>
        )}

        {archivedSection}

        <div className="bots-danger-zone">
          <button
            className="bots-delete-bot-btn"
            onClick={() => setDeleteTarget(selected ?? null)}
            disabled={!selected}
          >
            Delete bot…
          </button>
          <span className="bots-danger-hint">
            Removes the bot and its persona — you choose what happens to its chats.
          </span>
        </div>
      </div>
      {editModal}
      {cleanupConfirm && <Modal {...cleanupConfirm} />}
      <DeleteBotModal
        bot={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onDeleted={() => onSelectId(null)}
      />
    </div>
  );
}
