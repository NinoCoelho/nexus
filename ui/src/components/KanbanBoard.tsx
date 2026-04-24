/**
 * KanbanBoard — interactive kanban board backed by a single vault .md file.
 *
 * The board's data model lives in vault_kanban.py on the backend; this
 * component reads/writes through the /vault/kanban API endpoints. Each card
 * can optionally link to a chat session (nx:session=<sid>) — clicking a
 * linked card dispatches to that session via onDispatchToChat.
 *
 * Adding/moving/deleting cards is immediate (no explicit save button);
 * the backend writes each operation atomically to the .md file.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Modal, { type ModalProps } from "./Modal";
import CardDetailModal from "./CardDetailModal";
import CardActivityModal from "./CardActivityModal";
import LanePromptDialog from "./LanePromptDialog";
import {
  addVaultKanbanCard,
  addVaultKanbanLane,
  deleteVaultKanbanCard,
  deleteVaultKanbanLane,
  dispatchFromVault,
  getVaultKanban,
  patchVaultKanbanCard,
  patchVaultKanbanLane,
  type KanbanBoard as BoardT,
  type KanbanCard,
  type KanbanLane,
} from "../api";
import "./KanbanBoard.css";

interface Props {
  path: string;
  /**
   * Called when the user explicitly opens a card in chat (icon button).
   * The chat view should POST the seed_message to /chat/stream — it
   * contains a hidden-seed marker the chat bubble renderer filters out.
   */
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string) => void;
}

function cardPreview(body: string | undefined): string {
  if (!body) return "";
  const para = body.split(/\n\s*\n/)[0] ?? "";
  return para.length > 120 ? para.slice(0, 117) + "…" : para;
}

const PRIORITY_CLASS: Record<string, string> = {
  low: "kanban-prio kanban-prio--low",
  med: "kanban-prio kanban-prio--med",
  high: "kanban-prio kanban-prio--high",
  urgent: "kanban-prio kanban-prio--urgent",
};

function dueBadge(due: string | undefined): { label: string; cls: string } | null {
  if (!due) return null;
  // Compare as ISO yyyy-mm-dd against today's date
  const today = new Date().toISOString().slice(0, 10);
  const cls = due < today ? "kanban-due kanban-due--overdue"
    : due === today ? "kanban-due kanban-due--today"
    : "kanban-due";
  return { label: due, cls };
}

interface BoardFilters {
  text: string;
  label: string;
  priority: string;
  assignee: string;
}

export default function KanbanBoard({ path, onOpenInChat }: Props) {
  const [board, setBoard] = useState<BoardT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragCard, setDragCard] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<{ lane: string; index: number } | null>(null);
  const [modal, setModal] = useState<ModalProps | null>(null);
  const [detailCard, setDetailCard] = useState<KanbanCard | null>(null);
  const [activityCard, setActivityCard] = useState<KanbanCard | null>(null);
  const [editLane, setEditLane] = useState<KanbanLane | null>(null);
  const [filters, setFilters] = useState<BoardFilters>({ text: "", label: "", priority: "", assignee: "" });
  const [showFilters, setShowFilters] = useState(false);

  const matchesFilters = (card: KanbanCard): boolean => {
    if (filters.text) {
      const q = filters.text.toLowerCase();
      const hay = `${card.title} ${card.body ?? ""} ${(card.labels ?? []).join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (filters.label && !(card.labels ?? []).includes(filters.label)) return false;
    if (filters.priority && card.priority !== filters.priority) return false;
    if (filters.assignee && !(card.assignees ?? []).includes(filters.assignee)) return false;
    return true;
  };

  const reload = useCallback(() => {
    setError(null);
    getVaultKanban(path)
      .then(setBoard)
      .catch((e) => setError(e instanceof Error ? e.message : "Load failed"));
  }, [path]);

  useEffect(() => { reload(); }, [reload]);

  // Poll while any card is running so the spinner reflects the latest status
  // without needing a full SSE fan-out just for the card grid. The agent may
  // also mutate the board mid-turn (move_card, update_card via tools), so we
  // need polling fresh enough to catch lane changes — not just the final
  // status flip.
  const hasRunning = board?.lanes.some((l) => l.cards.some((c) => c.status === "running")) ?? false;
  useEffect(() => {
    if (!hasRunning) return;
    const t = setInterval(() => reload(), 1500);
    return () => clearInterval(t);
  }, [hasRunning, reload]);

  // When a turn finishes (running → done/failed) the final tool calls may
  // have written to the board file fractionally before the status flip.
  // Schedule one extra reload shortly after the transition so any
  // late-arriving moves/edits surface without requiring a manual refresh.
  const prevHasRunning = useRef(false);
  useEffect(() => {
    const transitioned = prevHasRunning.current && !hasRunning;
    prevHasRunning.current = hasRunning;
    if (transitioned) {
      const t = setTimeout(() => reload(), 500);
      return () => clearTimeout(t);
    }
  }, [hasRunning, reload]);

  if (error) return <div className="kanban-error">Couldn't load board: {error}</div>;
  if (!board) return <div className="kanban-loading">Loading…</div>;

  const findCard = (id: string): { lane: KanbanLane; card: KanbanCard } | null => {
    for (const l of board.lanes) {
      const c = l.cards.find((x) => x.id === id);
      if (c) return { lane: l, card: c };
    }
    return null;
  };

  const handleDrop = async (laneId: string, index: number) => {
    if (!dragCard) return;
    const found = findCard(dragCard);
    const destLane = board.lanes.find((l) => l.id === laneId);
    const cardId = dragCard;
    setDragCard(null);
    setDragOver(null);
    if (!found) return;
    const movedLanes = found.lane.id !== laneId;
    try {
      await patchVaultKanbanCard(path, cardId, { lane: laneId, position: index });
      reload();
      if (movedLanes && destLane?.prompt) {
        try {
          // Fire-and-forget: server runs the agent in the background.
          // The card flips to status=running and polling picks up the
          // spinner. User stays on the board.
          await dispatchFromVault({ path, card_id: cardId, mode: "background" });
          reload();
        } catch { /* dispatch failure is non-fatal */ }
      }
    } catch { /* ignore move errors */ }
  };

  const handleOpenInChat = async (card: KanbanCard) => {
    try {
      const res = await dispatchFromVault({ path, card_id: card.id, mode: "chat-hidden" });
      if (res.seed_message) {
        onOpenInChat?.(res.session_id, res.seed_message, card.title);
        // Pick up the linked session_id the backend stamped on the card.
        reload();
      }
    } catch { /* ignore */ }
  };

  const handleAddCard = (laneId: string) => {
    setModal({
      kind: "prompt",
      title: "New card",
      placeholder: "Card title",
      confirmLabel: "Add",
      onCancel: () => setModal(null),
      onSubmit: async (title) => {
        setModal(null);
        try { await addVaultKanbanCard(path, { lane: laneId, title }); reload(); }
        catch { /* ignore */ }
      },
    });
  };

  const handleAddLane = () => {
    setModal({
      kind: "prompt",
      title: "New lane",
      placeholder: "Lane title",
      confirmLabel: "Add",
      onCancel: () => setModal(null),
      onSubmit: async (title) => {
        setModal(null);
        try { await addVaultKanbanLane(path, title); reload(); }
        catch { /* ignore */ }
      },
    });
  };

  const handleDeleteCard = (cardId: string) => {
    setModal({
      kind: "confirm",
      title: "Delete card",
      message: "This card will be removed from the board.",
      confirmLabel: "Delete",
      danger: true,
      onCancel: () => setModal(null),
      onSubmit: async () => {
        setModal(null);
        try { await deleteVaultKanbanCard(path, cardId); reload(); }
        catch { /* ignore */ }
      },
    });
  };

  const handleDeleteLane = (laneId: string) => {
    const lane = board?.lanes.find((l) => l.id === laneId);
    const count = lane?.cards.length ?? 0;
    setModal({
      kind: "confirm",
      title: "Delete lane",
      message: count > 0
        ? `"${lane?.title}" contains ${count} card${count === 1 ? "" : "s"}. All will be removed.`
        : `Delete empty lane "${lane?.title}"?`,
      confirmLabel: "Delete",
      danger: true,
      onCancel: () => setModal(null),
      onSubmit: async () => {
        setModal(null);
        try { await deleteVaultKanbanLane(path, laneId); reload(); }
        catch { /* ignore */ }
      },
    });
  };

  const handleEditLanePrompt = (lane: KanbanLane) => {
    setEditLane(lane);
  };

  return (
    <div className="kanban-board">
      <div className="kanban-board-header">
        <div className="kanban-board-title">{board.title}</div>
        <div className="kanban-board-header-actions">
          <button
            className="kanban-pill"
            onClick={() => setShowFilters((v) => !v)}
            title="Filter cards"
          >
            {showFilters ? "Hide filters" : "Filters"}
            {(filters.text || filters.label || filters.priority || filters.assignee) && " •"}
          </button>
          <button className="kanban-pill" onClick={() => void handleAddLane()}>+ Lane</button>
        </div>
      </div>
      {showFilters && (
        <div className="kanban-filter-bar">
          <input
            className="kanban-filter-input"
            type="search"
            placeholder="Search…"
            value={filters.text}
            onChange={(e) => setFilters((f) => ({ ...f, text: e.target.value }))}
          />
          <input
            className="kanban-filter-input"
            placeholder="Label"
            value={filters.label}
            onChange={(e) => setFilters((f) => ({ ...f, label: e.target.value }))}
          />
          <input
            className="kanban-filter-input"
            placeholder="Assignee"
            value={filters.assignee}
            onChange={(e) => setFilters((f) => ({ ...f, assignee: e.target.value }))}
          />
          <select
            className="kanban-filter-input"
            value={filters.priority}
            onChange={(e) => setFilters((f) => ({ ...f, priority: e.target.value }))}
          >
            <option value="">Any priority</option>
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="med">Medium</option>
            <option value="low">Low</option>
          </select>
          {(filters.text || filters.label || filters.priority || filters.assignee) && (
            <button
              className="kanban-pill"
              onClick={() => setFilters({ text: "", label: "", priority: "", assignee: "" })}
            >
              Clear
            </button>
          )}
        </div>
      )}
      <div className="kanban-lanes">
        {board.lanes.map((lane) => (
          <div key={lane.id} className="kanban-lane">
            <div className="kanban-lane-header">
              <span className="kanban-lane-title">
                {lane.title}
                {lane.prompt && (
                  <span className="kanban-lane-prompt-indicator" title="Auto-dispatch prompt set">⚡</span>
                )}
              </span>
              <span className="kanban-lane-count">{lane.cards.length}</span>
              <button
                className="kanban-icon-btn"
                title={lane.prompt ? "Edit lane prompt" : "Set lane prompt"}
                onClick={() => void handleEditLanePrompt(lane)}
              >⚙</button>
              <button
                className="kanban-icon-btn"
                title="Delete lane"
                onClick={() => void handleDeleteLane(lane.id)}
              >×</button>
            </div>
            <div
              className="kanban-lane-cards"
              onDragOver={(e) => {
                e.preventDefault();
                if (dragCard) setDragOver({ lane: lane.id, index: lane.cards.length });
              }}
              onDrop={(e) => {
                e.preventDefault();
                const idx = dragOver?.lane === lane.id ? dragOver.index : lane.cards.length;
                void handleDrop(lane.id, idx);
              }}
            >
              {lane.cards.filter(matchesFilters).map((card, i) => {
                const preview = cardPreview(card.body);
                const due = dueBadge(card.due);
                return (
                  <div
                    key={card.id}
                    className={`kanban-card${dragCard === card.id ? " kanban-card--dragging" : ""}`}
                    draggable
                    onDragStart={() => setDragCard(card.id)}
                    onDragEnd={() => { setDragCard(null); setDragOver(null); }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (!dragCard) return;
                      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                      const mid = rect.top + rect.height / 2;
                      const insertIdx = e.clientY < mid ? i : i + 1;
                      setDragOver({ lane: lane.id, index: insertIdx });
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      const idx = dragOver?.lane === lane.id ? dragOver.index : i;
                      void handleDrop(lane.id, idx);
                    }}
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest(".kanban-card-actions")) return;
                      setDetailCard(card);
                    }}
                  >
                    <div className="kanban-card-title">
                      {card.priority && (
                        <span
                          className={PRIORITY_CLASS[card.priority] ?? "kanban-prio"}
                          title={`Priority: ${card.priority}`}
                        />
                      )}
                      <span>{card.title}</span>
                    </div>
                    {preview && <div className="kanban-card-body">{preview}</div>}
                    {(due || (card.labels && card.labels.length > 0) || (card.assignees && card.assignees.length > 0)) && (
                      <div className="kanban-card-meta">
                        {due && <span className={due.cls}>{due.label}</span>}
                        {(card.labels ?? []).map((l) => (
                          <span key={l} className="kanban-label">{l}</span>
                        ))}
                        {(card.assignees ?? []).map((a) => (
                          <span key={a} className="kanban-assignee">@{a}</span>
                        ))}
                      </div>
                    )}
                    <div className="kanban-card-actions">
                      {(card.status === "running" || card.status === "done" || card.status === "failed") && (
                        <button
                          className={`kanban-card-status kanban-card-status--${card.status}`}
                          onClick={(e) => { e.stopPropagation(); setActivityCard(card); }}
                          title={
                            card.status === "running" ? "Agent is working — click to view"
                            : card.status === "done" ? "Agent finished — click to review"
                            : "Agent failed — click to view"
                          }
                        >
                          {card.status === "running" && <span className="kanban-card-spin" />}
                          {card.status === "done" && (
                            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <polyline points="3 8 7 12 13 4" />
                            </svg>
                          )}
                          {card.status === "failed" && (
                            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <line x1="4" y1="4" x2="12" y2="12" />
                              <line x1="12" y1="4" x2="4" y2="12" />
                            </svg>
                          )}
                        </button>
                      )}
                      <button
                        className="kanban-card-btn kanban-card-btn--icon"
                        onClick={(e) => { e.stopPropagation(); void handleOpenInChat(card); }}
                        title="Open in chat"
                      >
                        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6A1.5 1.5 0 0 1 12.5 11H6l-3 3V3.5z" />
                        </svg>
                      </button>
                      <button
                        className="kanban-card-btn kanban-card-btn--danger"
                        onClick={(e) => { e.stopPropagation(); void handleDeleteCard(card.id); }}
                        title="Delete card"
                      >×</button>
                    </div>
                  </div>
                );
              })}
              <button
                className="kanban-add-card"
                onClick={() => void handleAddCard(lane.id)}
              >+ Add card</button>
            </div>
          </div>
        ))}
      </div>
      {modal && <Modal {...modal} />}
      {editLane && (
        <LanePromptDialog
          lane={editLane}
          onCancel={() => setEditLane(null)}
          onSubmit={async (patch) => {
            try {
              await patchVaultKanbanLane(path, editLane.id, patch);
              setEditLane(null);
              reload();
            } catch {
              setEditLane(null);
            }
          }}
        />
      )}
      {activityCard && activityCard.session_id && (
        <CardActivityModal
          sessionId={activityCard.session_id}
          cardTitle={activityCard.title}
          status={activityCard.status}
          onClose={() => { setActivityCard(null); reload(); }}
        />
      )}
      {detailCard && (
        <CardDetailModal
          card={detailCard}
          boardPath={path}
          onClose={() => setDetailCard(null)}
          onSaved={() => { setDetailCard(null); reload(); }}
        />
      )}
    </div>
  );
}
