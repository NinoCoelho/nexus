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
import { useTranslation } from "react-i18next";
import Modal, { type ModalProps } from "../Modal";
import CardDetailModal from "../CardDetailModal";
import CardActivityModal from "../CardActivityModal";
import LanePromptDialog from "../LanePromptDialog";
import BoardPromptDialog from "../BoardPromptDialog";
import {
  addVaultKanbanLane,
  cancelVaultKanbanCard,
  deleteVaultKanbanCard,
  deleteVaultKanbanLane,
  dispatchFromVault,
  fetchCardSessions,
  getVaultKanban,
  patchVaultKanbanBoard,
  patchVaultKanbanCard,
  patchVaultKanbanLane,
  retryVaultKanbanCard,
  type KanbanBoard as BoardT,
  type KanbanCard,
  type KanbanLane,
} from "../../api";
import type { CardSession } from "../../api/dispatch";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import "../KanbanBoard.css";
import KanbanLaneColumn from "./KanbanLaneColumn";
import KanbanFilterBar from "./KanbanFilterBar";
import SessionPickerModal from "./SessionPickerModal";
import { type BoardFilters } from "./utils";

interface Props {
  path: string;
  /**
   * Called when the user explicitly opens a card in chat (icon button).
   * The chat view should POST the seed_message to /chat/stream — it
   * contains a hidden-seed marker the chat bubble renderer filters out.
   */
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string, model?: string) => void;
  /** Navigate to an existing chat session (no new seed/auto-send). */
  onNavigateToSession?: (sessionId: string) => void;
  /** Navigate the host app to open `path` in the Vault view — forwarded to
   *  card detail/activity modals so vault links opened from there keep
   *  the "Open in Vault" affordance in their preview header. */
  onOpenInVault?: (path: string) => void;
}

export default function KanbanBoard({ path, onOpenInChat, onNavigateToSession, onOpenInVault }: Props) {
  const { t } = useTranslation("kanban");
  const [board, setBoard] = useState<BoardT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragCard, setDragCard] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<{ lane: string; index: number } | null>(null);
  const [dragLane, setDragLane] = useState<string | null>(null);
  const [laneDragOver, setLaneDragOver] = useState<number | null>(null);
  const [modal, setModal] = useState<ModalProps | null>(null);
  const [detailCard, setDetailCard] = useState<KanbanCard | null>(null);
  const [newCardLane, setNewCardLane] = useState<string | null>(null);
  const [activityCard, setActivityCard] = useState<KanbanCard | null>(null);
  const [editLane, setEditLane] = useState<KanbanLane | null>(null);
  const [sessionPicker, setSessionPicker] = useState<{ card: KanbanCard; sessions: CardSession[] } | null>(null);
  const [filters, setFilters] = useState<BoardFilters>({ text: "", label: "", priority: "", assignee: "" });
  const [showFilters, setShowFilters] = useState(false);
  const [editBoard, setEditBoard] = useState(false);

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
      .catch((e) => setError(e instanceof Error ? e.message : t("kanban:board.loading")));
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

  // Live-refresh on writes to this board's file. A single agent turn often
  // fires several mutations (add_card, then update_card, then move_card),
  // so debounce 200ms to coalesce them into a single reload. Skip while a
  // drag is in flight — handleDrop will reload on its own.
  const reloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useVaultEvents((ev) => {
    if (ev.path !== path) return;
    if (ev.type === "vault.removed") {
      setError(t("kanban:board.boardDeleted"));
      setBoard(null);
      return;
    }
    if (ev.type === "vault.indexed") {
      if (dragCard !== null) return;
      if (reloadTimerRef.current) clearTimeout(reloadTimerRef.current);
      reloadTimerRef.current = setTimeout(() => {
        reloadTimerRef.current = null;
        reload();
      }, 200);
    }
  });
  useEffect(() => {
    return () => {
      if (reloadTimerRef.current) clearTimeout(reloadTimerRef.current);
    };
  }, []);

  if (error) return <div className="kanban-error">{t("kanban:board.loadError", { error })}</div>;
  if (!board) return <div className="kanban-loading">{t("kanban:board.loading")}</div>;

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
    const cardId = dragCard;
    setDragCard(null);
    setDragOver(null);
    if (!found) return;
    try {
      // Server-side lane-change hook auto-dispatches the destination lane's
      // prompt (if any), so the UI just persists the move and reloads.
      await patchVaultKanbanCard(path, cardId, { lane: laneId, position: index });
      reload();
    } catch { /* ignore move errors */ }
  };

  const handleLaneDrop = async (insertIndex: number) => {
    if (!dragLane || !board) return;
    const laneId = dragLane;
    const srcIndex = board.lanes.findIndex((l) => l.id === laneId);
    setDragLane(null);
    setLaneDragOver(null);
    if (srcIndex < 0) return;
    // The backend treats `position` as the index into the post-removal list,
    // so an insertIndex past the source needs to shift down by one to land
    // visually where the user dropped.
    const target = insertIndex > srcIndex ? insertIndex - 1 : insertIndex;
    if (target === srcIndex) return;
    try {
      await patchVaultKanbanLane(path, laneId, { position: target });
      reload();
    } catch { /* ignore reorder errors */ }
  };

  const handleOpenInChat = async (card: KanbanCard) => {
    if (!card.session_id) {
      handleNewDispatch(card);
      return;
    }
    try {
      const sessions = await fetchCardSessions(path, card.id);
      if (sessions.length === 0) {
        handleNewDispatch(card);
        return;
      }
      if (sessions.length === 1) {
        onNavigateToSession?.(sessions[0].id);
        return;
      }
      setSessionPicker({ card, sessions });
    } catch {
      handleNewDispatch(card);
    }
  };

  const handleNewDispatch = async (card: KanbanCard) => {
    try {
      const res = await dispatchFromVault({ path, card_id: card.id, mode: "chat-hidden" });
      if (res.seed_message) {
        onOpenInChat?.(res.session_id, res.seed_message, card.title, res.model ?? undefined);
        reload();
      }
    } catch { /* ignore */ }
  };

  const handleAddCard = (laneId: string) => {
    // Open the same detail modal used for editing, in create mode. Lane prompts
    // often require context in the description, so we want users to fill the
    // body (and any metadata) before the card lands on the board.
    setNewCardLane(laneId);
  };

  const handleAddLane = () => {
    setModal({
      kind: "prompt",
      title: t("kanban:lane.newLaneTitle"),
      placeholder: t("kanban:lane.newLanePlaceholder"),
      confirmLabel: t("kanban:lane.newLaneConfirm"),
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
      title: t("kanban:card.deleteTitle"),
      message: t("kanban:card.deleteMessage"),
      confirmLabel: t("kanban:card.deleteConfirm"),
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
      title: t("kanban:lane.deleteLaneTitle"),
      message: count > 0
        ? t("kanban:lane.deleteLaneWithCards", { title: lane?.title, count })
        : t("kanban:lane.deleteLaneEmpty", { title: lane?.title }),
      confirmLabel: t("kanban:lane.deleteLaneConfirm"),
      danger: true,
      onCancel: () => setModal(null),
      onSubmit: async () => {
        setModal(null);
        try { await deleteVaultKanbanLane(path, laneId); reload(); }
        catch { /* ignore */ }
      },
    });
  };

  const handleCancelCard = async (cardId: string) => {
    try {
      await cancelVaultKanbanCard(path, cardId);
      reload();
    } catch { /* ignore */ }
  };

  const handleRetryCard = async (cardId: string) => {
    try {
      await retryVaultKanbanCard(path, cardId);
      reload();
    } catch { /* ignore */ }
  };

  const hasActiveFilters = !!(filters.text || filters.label || filters.priority || filters.assignee);

  return (
    <div className="kanban-board">
      <div className="kanban-board-header">
        <div className="kanban-board-title">
          {board.title}
          {board.board_prompt && (
            <button
              className="kanban-icon-btn"
              title={t("kanban:board.boardPromptIndicator")}
              onClick={() => setEditBoard(true)}
              style={{
                background: "none",
                border: "none",
                color: "var(--accent)",
                cursor: "pointer",
                fontSize: 14,
                marginLeft: 6,
                padding: "0 2px",
                verticalAlign: "middle",
              }}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="8" cy="8" r="3" />
                <path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.05 3.05l1.41 1.41M11.54 11.54l1.41 1.41M3.05 12.95l1.41-1.41M11.54 4.46l1.41-1.41" />
              </svg>
            </button>
          )}
        </div>
        <div className="kanban-board-header-actions">
          <button
            className="kanban-pill"
            onClick={() => setShowFilters((v) => !v)}
            title={t("kanban:board.filterToggle")}
          >
            {showFilters ? t("kanban:board.hideFilters") : t("kanban:board.filters")}
            {hasActiveFilters && t("kanban:board.activeFilterIndicator")}
          </button>
          <button className="kanban-pill" onClick={() => void handleAddLane()}>{t("kanban:board.addLane")}</button>
        </div>
      </div>
      {showFilters && <KanbanFilterBar filters={filters} onFiltersChange={setFilters} />}
      <div className="kanban-lanes">
        {board.lanes.map((lane, idx) => (
          <KanbanLaneColumn
            key={lane.id}
            lane={lane}
            laneIndex={idx}
            isLastLane={idx === board.lanes.length - 1}
            dragCard={dragCard}
            dragLane={dragLane}
            dragOver={dragOver}
            laneDragOver={laneDragOver}
            filters={filters}
            matchesFilters={matchesFilters}
            onSetDragCard={setDragCard}
            onSetDragOver={setDragOver}
            onDrop={handleDrop}
            onLaneDragStart={(id) => setDragLane(id)}
            onLaneDragEnd={() => { setDragLane(null); setLaneDragOver(null); }}
            onLaneDragOver={(insertIdx) => setLaneDragOver(insertIdx)}
            onLaneDrop={(insertIdx) => void handleLaneDrop(insertIdx)}
            onEditLane={(l) => setEditLane(l)}
            onDeleteLane={handleDeleteLane}
            onAddCard={handleAddCard}
            onOpenCardDetail={(card) => setDetailCard(card)}
            onOpenCardActivity={(card) => setActivityCard(card)}
            onOpenCardInChat={(card) => void handleOpenInChat(card)}
            onDeleteCard={handleDeleteCard}
            onCancelCard={handleCancelCard}
            onRetryCard={handleRetryCard}
          />
        ))}
      </div>
      {modal && <Modal {...modal} />}
      {editLane && (
        <LanePromptDialog
          lane={editLane}
          boardPath={path}
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
          onOpenInVault={onOpenInVault}
        />
      )}
      {detailCard && (
        <CardDetailModal
          card={detailCard}
          boardPath={path}
          onClose={() => setDetailCard(null)}
          onSaved={() => { setDetailCard(null); reload(); }}
          onOpenInVault={onOpenInVault}
        />
      )}
      {newCardLane && (
        <CardDetailModal
          lane={newCardLane}
          boardPath={path}
          onClose={() => setNewCardLane(null)}
          onSaved={() => { setNewCardLane(null); reload(); }}
          onOpenInVault={onOpenInVault}
        />
      )}
      {sessionPicker && (
        <SessionPickerModal
          sessions={sessionPicker.sessions}
          onSelect={(sid) => { setSessionPicker(null); onNavigateToSession?.(sid); }}
          onNewSession={() => { const card = sessionPicker.card; setSessionPicker(null); void handleNewDispatch(card); }}
          onCancel={() => setSessionPicker(null)}
        />
      )}
      {editBoard && board && (
        <BoardPromptDialog
          board={board}
          onCancel={() => setEditBoard(false)}
          onSubmit={async (patch) => {
            try {
              await patchVaultKanbanBoard(path, patch);
              setEditBoard(false);
              reload();
            } catch {
              setEditBoard(false);
            }
          }}
        />
      )}
    </div>
  );
}
