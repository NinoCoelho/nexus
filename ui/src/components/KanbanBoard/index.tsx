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
import Modal, { type ModalProps } from "../Modal";
import CardDetailModal from "../CardDetailModal";
import CardActivityModal from "../CardActivityModal";
import LanePromptDialog from "../LanePromptDialog";
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
} from "../../api";
import { useVaultEvents } from "../../hooks/useVaultEvents";
import "../KanbanBoard.css";
import KanbanLaneColumn from "./KanbanLaneColumn";
import KanbanFilterBar from "./KanbanFilterBar";
import { type BoardFilters } from "./utils";

interface Props {
  path: string;
  /**
   * Called when the user explicitly opens a card in chat (icon button).
   * The chat view should POST the seed_message to /chat/stream — it
   * contains a hidden-seed marker the chat bubble renderer filters out.
   */
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string) => void;
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

  // Live-refresh on writes to this board's file. A single agent turn often
  // fires several mutations (add_card, then update_card, then move_card),
  // so debounce 200ms to coalesce them into a single reload. Skip while a
  // drag is in flight — handleDrop will reload on its own.
  const reloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useVaultEvents((ev) => {
    if (ev.path !== path) return;
    if (ev.type === "vault.removed") {
      setError("Board file was deleted");
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

  const hasActiveFilters = !!(filters.text || filters.label || filters.priority || filters.assignee);

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
            {hasActiveFilters && " •"}
          </button>
          <button className="kanban-pill" onClick={() => void handleAddLane()}>+ Lane</button>
        </div>
      </div>
      {showFilters && <KanbanFilterBar filters={filters} onFiltersChange={setFilters} />}
      <div className="kanban-lanes">
        {board.lanes.map((lane) => (
          <KanbanLaneColumn
            key={lane.id}
            lane={lane}
            dragCard={dragCard}
            dragOver={dragOver}
            filters={filters}
            matchesFilters={matchesFilters}
            onSetDragCard={setDragCard}
            onSetDragOver={setDragOver}
            onDrop={handleDrop}
            onEditLane={(l) => setEditLane(l)}
            onDeleteLane={handleDeleteLane}
            onAddCard={handleAddCard}
            onOpenCardDetail={(card) => setDetailCard(card)}
            onOpenCardActivity={(card) => setActivityCard(card)}
            onOpenCardInChat={(card) => void handleOpenInChat(card)}
            onDeleteCard={handleDeleteCard}
          />
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
