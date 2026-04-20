import { useEffect, useRef, useState } from "react";
import {
  deleteKanbanBoard,
  deleteKanbanCard,
  getKanban,
  getKanbanBoards,
  patchKanbanCard,
  postKanbanBoard,
  postKanbanCard,
  postKanbanColumn,
  type Board,
  type KanbanCard,
} from "../api";
import { useToast } from "../toast/ToastProvider";
import "./KanbanView.css";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

const BOARD_NAME_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

// ── Card Inspector ────────────────────────────────────────────────────────────

interface InspectorProps {
  card: KanbanCard;
  onClose: () => void;
  onSave: (patch: Partial<KanbanCard>) => void;
  onDelete: () => void;
}

function CardInspector({ card, onClose, onSave, onDelete }: InspectorProps) {
  const [title, setTitle] = useState(card.title);
  const [notes, setNotes] = useState(card.notes ?? "");
  const [tags, setTags] = useState((card.tags ?? []).join(", "));

  const save = () => {
    const parsedTags = tags.split(",").map((t) => t.trim()).filter(Boolean);
    onSave({ title: title.trim() || "Untitled", notes, tags: parsedTags });
  };

  return (
    <div className="kanban-inspector">
      <div className="kanban-inspector-header">
        <span className="kanban-inspector-title">Card</span>
        <button className="kanban-inspector-close" onClick={onClose} aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="4" y1="4" x2="16" y2="16" /><line x1="16" y1="4" x2="4" y2="16" />
          </svg>
        </button>
      </div>
      <div className="kanban-inspector-body">
        <label className="kanban-field-label">Title</label>
        <input
          className="kanban-field-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <label className="kanban-field-label">Notes</label>
        <textarea
          className="kanban-field-textarea"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={6}
        />
        <label className="kanban-field-label">Tags (comma-separated)</label>
        <input
          className="kanban-field-input"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="bug, urgent, frontend"
        />
      </div>
      <div className="kanban-inspector-footer">
        <button className="kanban-btn kanban-btn--danger" onClick={onDelete}>Delete</button>
        <div style={{ flex: 1 }} />
        <button className="kanban-btn" onClick={onClose}>Cancel</button>
        <button className="kanban-btn kanban-btn--primary" onClick={save}>Save</button>
      </div>
    </div>
  );
}

// ── KanbanView ────────────────────────────────────────────────────────────────

export default function KanbanView() {
  const toast = useToast();
  const [boards, setBoards] = useState<Board[]>([]);
  const [activeBoard, setActiveBoard] = useState("default");
  const [columns, setColumns] = useState<string[]>([]);
  const [cards, setCards] = useState<KanbanCard[]>([]);
  const [error, setError] = useState(false);
  const [inspecting, setInspecting] = useState<KanbanCard | null>(null);
  const [dragCardId, setDragCardId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  // Per-tab delete confirm: stores the board name pending second click
  const [deletePending, setDeletePending] = useState<string | null>(null);
  const loadedRef = useRef(false);

  const loadBoards = () => {
    getKanbanBoards()
      .then((bs) => setBoards(bs))
      .catch(() => { /* non-fatal */ });
  };

  const loadBoard = (board: string) => {
    setError(false);
    getKanban(board)
      .then((b) => {
        setColumns(b.columns.length ? b.columns : ["Backlog", "In Progress", "Done"]);
        setCards(b.cards);
      })
      .catch(() => {
        setError(true);
        setColumns(["Backlog", "In Progress", "Done"]);
      });
  };

  useEffect(() => {
    if (!loadedRef.current) {
      loadedRef.current = true;
      loadBoards();
      loadBoard(activeBoard);
    }
  }, []);

  const switchBoard = (name: string) => {
    setDeletePending(null);
    setActiveBoard(name);
    loadBoard(name);
  };

  const addBoard = async () => {
    const name = prompt("New board name (lowercase, letters/digits/hyphens):");
    if (!name) return;
    const trimmed = name.trim();
    if (!BOARD_NAME_RE.test(trimmed)) {
      toast.warning("Invalid board name", {
        detail: "Use lowercase letters, digits and hyphens (e.g. my-board).",
      });
      return;
    }
    try {
      await postKanbanBoard(trimmed);
      loadBoards();
      switchBoard(trimmed);
      toast.success(`Board "${trimmed}" created`);
    } catch (e: unknown) {
      toast.error("Couldn't create board", {
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleDeleteBoard = async (name: string, cardCount: number) => {
    if (cardCount > 0) return; // button is disabled; guard anyway
    if (deletePending !== name) {
      setDeletePending(name);
      return;
    }
    // Second click — confirmed
    setDeletePending(null);
    try {
      await deleteKanbanBoard(name);
      const remaining = boards.filter((b) => b.name !== name);
      setBoards(remaining);
      if (activeBoard === name && remaining.length > 0) {
        switchBoard(remaining[0].name);
      }
      toast.success(`Deleted board "${name}"`);
    } catch (e: unknown) {
      toast.error("Couldn't delete board", {
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  };

  // Drag handlers
  const onDragStart = (cardId: string) => setDragCardId(cardId);
  const onDragEnd = () => { setDragCardId(null); setDropTarget(null); };
  const onDragOver = (e: React.DragEvent, col: string) => {
    e.preventDefault();
    setDropTarget(col);
  };
  const onDrop = async (col: string) => {
    setDropTarget(null);
    if (!dragCardId) return;
    const card = cards.find((c) => c.id === dragCardId);
    if (!card || card.column === col) return;
    setCards((prev) => prev.map((c) => c.id === dragCardId ? { ...c, column: col } : c));
    try {
      await patchKanbanCard(dragCardId, { column: col }, activeBoard);
    } catch (e) {
      // Optimistic update rollback + surface the failure so the user
      // knows their drag didn't stick.
      setCards((prev) => prev.map((c) => c.id === dragCardId ? { ...c, column: card.column } : c));
      toast.error("Couldn't move card", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
    setDragCardId(null);
  };

  const addCard = async (col: string) => {
    const title = prompt("Card title:");
    if (!title) return;
    try {
      const card = await postKanbanCard({ title: title.trim(), column: col }, activeBoard);
      setCards((prev) => [...prev, card]);
      setBoards((prev) => prev.map((b) => b.name === activeBoard ? { ...b, card_count: b.card_count + 1 } : b));
    } catch (e) {
      toast.error("Couldn't create card", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  };

  const addColumn = async () => {
    const name = prompt("Column name:");
    if (!name) return;
    try {
      await postKanbanColumn(name.trim(), activeBoard);
      setColumns((prev) => [...prev, name.trim()]);
    } catch (e) {
      toast.error("Couldn't add column", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  };

  const handleSave = async (patch: Partial<KanbanCard>) => {
    if (!inspecting) return;
    try {
      const updated = await patchKanbanCard(inspecting.id, patch, activeBoard);
      setCards((prev) => prev.map((c) => c.id === updated.id ? updated : c));
      setInspecting(updated);
    } catch (e) {
      toast.error("Couldn't save card", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
    setInspecting(null);
  };

  const handleDelete = async () => {
    if (!inspecting) return;
    try {
      await deleteKanbanCard(inspecting.id, activeBoard);
      setCards((prev) => prev.filter((c) => c.id !== inspecting.id));
      setBoards((prev) => prev.map((b) => b.name === activeBoard ? { ...b, card_count: Math.max(0, b.card_count - 1) } : b));
    } catch (e) {
      toast.error("Couldn't delete card", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
    setInspecting(null);
  };

  return (
    <div className="kanban-view">
      {/* Board tab bar */}
      <div className="kanban-tabs">
        {boards.map((b) => {
          const isActive = b.name === activeBoard;
          const isDeletable = b.card_count === 0 && boards.length > 1;
          const isPending = deletePending === b.name;
          return (
            <div
              key={b.name}
              className={`kanban-tab${isActive ? " kanban-tab--active" : ""}`}
              onClick={() => switchBoard(b.name)}
            >
              <span className="kanban-tab-name">{b.name}</span>
              <span className="kanban-tab-count">{b.card_count}</span>
              {isDeletable && (
                <button
                  className={`kanban-tab-delete${isPending ? " kanban-tab-delete--confirm" : ""}`}
                  title={isPending ? "Click again to confirm" : "Delete board"}
                  onClick={(e) => { e.stopPropagation(); void handleDeleteBoard(b.name, b.card_count); }}
                  aria-label={`Delete board ${b.name}`}
                >
                  {isPending ? "!" : "×"}
                </button>
              )}
            </div>
          );
        })}
        <button className="kanban-tab-add" onClick={() => void addBoard()}>
          + New board
        </button>
      </div>

      {error && (
        <div className="kanban-error">Couldn&apos;t load board — is the server running?</div>
      )}

      <div className="kanban-board">
        {columns.map((col) => {
          const colCards = cards.filter((c) => c.column === col);
          const isDropTarget = dropTarget === col && dragCardId !== null;
          return (
            <div
              key={col}
              className={`kanban-column${isDropTarget ? " kanban-column--drop-target" : ""}`}
              onDragOver={(e) => onDragOver(e, col)}
              onDrop={() => void onDrop(col)}
              onDragLeave={() => setDropTarget(null)}
            >
              <div className="kanban-col-header">
                <span className="kanban-col-name">{col}</span>
                <span className="kanban-col-count">{colCards.length}</span>
                <button className="kanban-col-add" onClick={() => void addCard(col)} title="Add card">
                  <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <line x1="10" y1="4" x2="10" y2="16" /><line x1="4" y1="10" x2="16" y2="10" />
                  </svg>
                </button>
              </div>
              <div className="kanban-cards">
                {colCards.map((card) => (
                  <div
                    key={card.id}
                    className={`kanban-card${dragCardId === card.id ? " kanban-card--dragging" : ""}`}
                    draggable
                    onDragStart={() => onDragStart(card.id)}
                    onDragEnd={onDragEnd}
                    onClick={() => setInspecting(card)}
                  >
                    <span className="kanban-card-title">{card.title}</span>
                    {card.tags && card.tags.length > 0 && (
                      <div className="kanban-card-tags">
                        {card.tags.map((tag) => (
                          <span key={tag} className="kanban-tag">{tag}</span>
                        ))}
                      </div>
                    )}
                    <span className="kanban-card-time">{fmtDate(card.updated_at)}</span>
                  </div>
                ))}
                {isDropTarget && (
                  <div className="kanban-card-placeholder" />
                )}
              </div>
            </div>
          );
        })}

        {/* Add column */}
        <button className="kanban-add-column" onClick={() => void addColumn()}>
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="10" y1="4" x2="10" y2="16" /><line x1="4" y1="10" x2="16" y2="10" />
          </svg>
          Add column
        </button>
      </div>

      {/* Inspector panel */}
      {inspecting && (
        <CardInspector
          card={inspecting}
          onClose={() => setInspecting(null)}
          onSave={(patch) => void handleSave(patch)}
          onDelete={() => void handleDelete()}
        />
      )}
    </div>
  );
}
