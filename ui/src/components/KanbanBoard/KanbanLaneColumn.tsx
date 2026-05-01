import { useTranslation } from "react-i18next";
import type { KanbanCard, KanbanLane } from "../../api";
import KanbanCardItem from "./KanbanCardItem";
import type { BoardFilters } from "./utils";

interface Props {
  lane: KanbanLane;
  laneIndex: number;
  isLastLane: boolean;
  dragCard: string | null;
  dragLane: string | null;
  dragOver: { lane: string; index: number } | null;
  laneDragOver: number | null;
  filters: BoardFilters;
  matchesFilters: (card: KanbanCard) => boolean;
  onSetDragCard: (id: string | null) => void;
  onSetDragOver: (v: { lane: string; index: number } | null) => void;
  onDrop: (laneId: string, index: number) => void;
  onLaneDragStart: (laneId: string) => void;
  onLaneDragEnd: () => void;
  onLaneDragOver: (insertIndex: number) => void;
  onLaneDrop: (insertIndex: number) => void;
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
  laneIndex,
  isLastLane,
  dragCard,
  dragLane,
  dragOver,
  laneDragOver,
  matchesFilters,
  onSetDragCard,
  onSetDragOver,
  onDrop,
  onLaneDragStart,
  onLaneDragEnd,
  onLaneDragOver,
  onLaneDrop,
  onEditLane,
  onDeleteLane,
  onAddCard,
  onOpenCardDetail,
  onOpenCardActivity,
  onOpenCardInChat,
  onDeleteCard,
}: Props) {
  const { t } = useTranslation("kanban");
  const isLaneDragging = dragLane === lane.id;
  // Lane dragOver indicator: highlight the left edge if the drop target is
  // this column's index, or the right edge if it's the next index *and*
  // this is the rightmost column (so the trailing slot still renders a hint).
  const showLeftIndicator = dragLane !== null && laneDragOver === laneIndex;
  const showRightIndicator = dragLane !== null && isLastLane && laneDragOver === laneIndex + 1;
  return (
    <div
      className={`kanban-lane${isLaneDragging ? " kanban-lane--dragging" : ""}${showLeftIndicator ? " kanban-lane--drop-before" : ""}${showRightIndicator ? " kanban-lane--drop-after" : ""}`}
      onDragOver={(e) => {
        if (!dragLane) return;
        e.preventDefault();
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const mid = rect.left + rect.width / 2;
        const insertIdx = e.clientX < mid ? laneIndex : laneIndex + 1;
        onLaneDragOver(insertIdx);
      }}
      onDrop={(e) => {
        if (!dragLane) return;
        e.preventDefault();
        const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
        const mid = rect.left + rect.width / 2;
        const insertIdx = e.clientX < mid ? laneIndex : laneIndex + 1;
        onLaneDrop(insertIdx);
      }}
    >
      <div
        className="kanban-lane-header"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = "move";
          onLaneDragStart(lane.id);
        }}
        onDragEnd={onLaneDragEnd}
        title={t("kanban:lane.dragHandle")}
      >
        <span className="kanban-lane-title">
          {lane.title}
          {lane.prompt && (
            <span className="kanban-lane-prompt-indicator" title={t("kanban:lane.promptIndicator")}>⚡</span>
          )}
        </span>
        <span className="kanban-lane-count">{lane.cards.length}</span>
        <button
          className="kanban-icon-btn"
          title={lane.prompt ? t("kanban:lane.editPrompt") : t("kanban:lane.setPrompt")}
          onClick={() => onEditLane(lane)}
        >⚙</button>
        <button
          className="kanban-icon-btn"
          title={t("kanban:lane.deleteLane")}
          onClick={() => onDeleteLane(lane.id)}
        >×</button>
      </div>
      <div
        className="kanban-lane-cards"
        onDragOver={(e) => {
          if (!dragCard) return;
          e.preventDefault();
          onSetDragOver({ lane: lane.id, index: lane.cards.length });
        }}
        onDrop={(e) => {
          if (!dragCard) return;
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
          aria-label={t("kanban:lane.addCardAria")}
        >{t("kanban:lane.addCard")}</button>
      </div>
    </div>
  );
}
