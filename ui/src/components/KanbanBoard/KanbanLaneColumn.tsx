import type { KanbanCard, KanbanLane } from "../../api";
import KanbanCardItem from "./KanbanCardItem";
import type { BoardFilters } from "./utils";

interface Props {
  lane: KanbanLane;
  dragCard: string | null;
  dragOver: { lane: string; index: number } | null;
  filters: BoardFilters;
  matchesFilters: (card: KanbanCard) => boolean;
  onSetDragCard: (id: string | null) => void;
  onSetDragOver: (v: { lane: string; index: number } | null) => void;
  onDrop: (laneId: string, index: number) => void;
  onEditLane: (lane: KanbanLane) => void;
  onDeleteLane: (laneId: string) => void;
  onAddCard: (laneId: string) => void;
  onOpenCardDetail: (card: KanbanCard) => void;
  onOpenCardActivity: (card: KanbanCard) => void;
  onOpenCardInChat: (card: KanbanCard) => void;
  onDeleteCard: (cardId: string) => void;
}

export default function KanbanLaneColumn({
  lane,
  dragCard,
  dragOver,
  matchesFilters,
  onSetDragCard,
  onSetDragOver,
  onDrop,
  onEditLane,
  onDeleteLane,
  onAddCard,
  onOpenCardDetail,
  onOpenCardActivity,
  onOpenCardInChat,
  onDeleteCard,
}: Props) {
  return (
    <div className="kanban-lane">
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
          onClick={() => onEditLane(lane)}
        >⚙</button>
        <button
          className="kanban-icon-btn"
          title="Delete lane"
          onClick={() => onDeleteLane(lane.id)}
        >×</button>
      </div>
      <div
        className="kanban-lane-cards"
        onDragOver={(e) => {
          e.preventDefault();
          if (dragCard) onSetDragOver({ lane: lane.id, index: lane.cards.length });
        }}
        onDrop={(e) => {
          e.preventDefault();
          const idx = dragOver?.lane === lane.id ? dragOver.index : lane.cards.length;
          onDrop(lane.id, idx);
        }}
      >
        {lane.cards.filter(matchesFilters).map((card, i) => (
          <KanbanCardItem
            key={card.id}
            card={card}
            index={i}
            dragCardId={dragCard}
            dragOver={dragOver}
            laneId={lane.id}
            onDragStart={() => onSetDragCard(card.id)}
            onDragEnd={() => { onSetDragCard(null); onSetDragOver(null); }}
            onDragOver={(_e, insertIdx) => onSetDragOver({ lane: lane.id, index: insertIdx })}
            onDrop={(_e, idx) => onDrop(lane.id, idx)}
            onClick={() => onOpenCardDetail(card)}
            onOpenInChat={() => onOpenCardInChat(card)}
            onDelete={() => onDeleteCard(card.id)}
            onViewActivity={() => onOpenCardActivity(card)}
          />
        ))}
        <button
          className="kanban-add-card"
          onClick={() => onAddCard(lane.id)}
        >+ Add card</button>
      </div>
    </div>
  );
}
