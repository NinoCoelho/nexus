import { useCallback, useEffect, useState } from "react";
import Modal, { type ModalProps } from "./Modal";
import {
  addVaultKanbanCard,
  addVaultKanbanLane,
  deleteVaultKanbanCard,
  deleteVaultKanbanLane,
  dispatchFromVault,
  getVaultKanban,
  patchVaultKanbanCard,
  type KanbanBoard as BoardT,
  type KanbanCard,
} from "../api";
import "./KanbanBoard.css";

interface Props {
  path: string;
  /** Invoked when a card is dispatched to chat. Host switches views/prefills input. */
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
}

export default function KanbanBoard({ path, onDispatchToChat }: Props) {
  const [board, setBoard] = useState<BoardT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragCard, setDragCard] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState<{ lane: string; index: number } | null>(null);
  const [modal, setModal] = useState<ModalProps | null>(null);

  const reload = useCallback(() => {
    setError(null);
    getVaultKanban(path)
      .then(setBoard)
      .catch((e) => setError(e instanceof Error ? e.message : "Load failed"));
  }, [path]);

  useEffect(() => { reload(); }, [reload]);

  if (error) return <div className="kanban-error">Couldn't load board: {error}</div>;
  if (!board) return <div className="kanban-loading">Loading…</div>;

  const findCard = (id: string): { lane: string; card: KanbanCard } | null => {
    for (const l of board.lanes) {
      const c = l.cards.find((x) => x.id === id);
      if (c) return { lane: l.id, card: c };
    }
    return null;
  };

  const handleDrop = async (laneId: string, index: number) => {
    if (!dragCard) return;
    const found = findCard(dragCard);
    setDragCard(null);
    setDragOver(null);
    if (!found) return;
    try {
      await patchVaultKanbanCard(path, dragCard, { lane: laneId, position: index });
      reload();
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

  const handleDispatch = async (card: KanbanCard) => {
    try {
      const res = await dispatchFromVault({ path, card_id: card.id });
      onDispatchToChat?.(res.session_id, res.seed_message);
    } catch { /* ignore */ }
  };

  return (
    <div className="kanban-board">
      <div className="kanban-board-header">
        <div className="kanban-board-title">{board.title}</div>
        <button className="kanban-pill" onClick={() => void handleAddLane()}>+ Lane</button>
      </div>
      <div className="kanban-lanes">
        {board.lanes.map((lane) => (
          <div key={lane.id} className="kanban-lane">
            <div className="kanban-lane-header">
              <span className="kanban-lane-title">{lane.title}</span>
              <span className="kanban-lane-count">{lane.cards.length}</span>
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
              {lane.cards.map((card, i) => (
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
                >
                  <div className="kanban-card-title">{card.title}</div>
                  {card.body && <div className="kanban-card-body">{card.body}</div>}
                  <div className="kanban-card-actions">
                    <button
                      className="kanban-card-btn"
                      onClick={() => void handleDispatch(card)}
                      title="Start chat with this card"
                    >→ Chat</button>
                    {card.session_id && (
                      <span className="kanban-card-session" title={`Session ${card.session_id}`}>●</span>
                    )}
                    <button
                      className="kanban-card-btn kanban-card-btn--danger"
                      onClick={() => void handleDeleteCard(card.id)}
                      title="Delete card"
                    >×</button>
                  </div>
                </div>
              ))}
              <button
                className="kanban-add-card"
                onClick={() => void handleAddCard(lane.id)}
              >+ Add card</button>
            </div>
          </div>
        ))}
      </div>
      {modal && <Modal {...modal} />}
    </div>
  );
}
