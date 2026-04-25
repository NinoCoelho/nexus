import type { KanbanCard } from "../../api";
import { cardPreview, dueBadge, PRIORITY_CLASS } from "./utils";

interface Props {
  card: KanbanCard;
  index: number;
  dragCardId: string | null;
  dragOver: { lane: string; index: number } | null;
  laneId: string;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (e: React.DragEvent, i: number) => void;
  onDrop: (e: React.DragEvent, i: number) => void;
  onClick: () => void;
  onOpenInChat: () => void;
  onDelete: () => void;
  onViewActivity: () => void;
}

export default function KanbanCardItem({
  card,
  index,
  dragCardId,
  dragOver,
  laneId,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  onClick,
  onOpenInChat,
  onDelete,
  onViewActivity,
}: Props) {
  const preview = cardPreview(card.body);
  const due = dueBadge(card.due);
  const isDragging = dragCardId === card.id;

  return (
    <div
      className={`kanban-card${isDragging ? " kanban-card--dragging" : ""}`}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!dragCardId) return;
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const mid = rect.top + rect.height / 2;
        const insertIdx = e.clientY < mid ? index : index + 1;
        onDragOver(e, insertIdx);
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        const idx = dragOver?.lane === laneId ? dragOver.index : index;
        onDrop(e, idx);
      }}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest(".kanban-card-actions")) return;
        onClick();
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
            onClick={(e) => { e.stopPropagation(); onViewActivity(); }}
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
          onClick={(e) => { e.stopPropagation(); onOpenInChat(); }}
          title="Open in chat"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v6A1.5 1.5 0 0 1 12.5 11H6l-3 3V3.5z" />
          </svg>
        </button>
        <button
          className="kanban-card-btn kanban-card-btn--danger"
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          title="Delete card"
        >×</button>
      </div>
    </div>
  );
}
