import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { KanbanCard } from "../../api";
import MarkdownView from "../MarkdownView";
import { dueBadge, PRIORITY_CLASS } from "./utils";

interface LaneOption {
  id: string;
  title: string;
}

interface Props {
  card: KanbanCard;
  index: number;
  dragCardId: string | null;
  dragOver: { lane: string; index: number } | null;
  laneId: string;
  allLanes: LaneOption[];
  onDragStart: () => void;
  onDragEnd: () => void;
  onDragOver: (e: React.DragEvent, i: number) => void;
  onDrop: (e: React.DragEvent, i: number) => void;
  onClick: () => void;
  onOpenInChat: () => void;
  onDelete: () => void;
  onViewActivity: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onMoveCard: (targetLaneId: string) => void;
}

type StatusVariant = "idle" | "confirm-stop" | "confirm-retry";

export default function KanbanCardItem({
  card,
  index,
  dragCardId,
  dragOver,
  laneId,
  allLanes,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  onClick,
  onOpenInChat,
  onDelete,
  onViewActivity,
  onCancel,
  onRetry,
  onMoveCard,
}: Props) {
  const { t } = useTranslation("kanban");
  const due = dueBadge(card.due);
  const isDragging = dragCardId === card.id;
  const [variant, setVariant] = useState<StatusVariant>("idle");
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number } | null>(null);
  const [moveSubOpen, setMoveSubOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const resetVariant = useCallback(() => {
    setVariant("idle");
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  useEffect(() => {
    setVariant("idle");
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, [card.status]);

  const showConfirm = (v: StatusVariant) => {
    resetVariant();
    setVariant(v);
    timerRef.current = setTimeout(() => {
      setVariant("idle");
      timerRef.current = null;
    }, 5000);
  };

  const handleStatusClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (card.status === "running") {
      if (variant === "confirm-stop") {
        resetVariant();
        onCancel();
      } else {
        showConfirm("confirm-stop");
      }
    } else if (card.status === "failed") {
      if (variant === "confirm-retry") {
        resetVariant();
        onRetry();
      } else {
        showConfirm("confirm-retry");
      }
    } else if (card.status === "done") {
      if (variant === "confirm-retry") {
        resetVariant();
        onRetry();
      } else {
        showConfirm("confirm-retry");
      }
    } else {
      onViewActivity();
    }
  };

  const statusIcon = () => {
    if (card.status === "running") {
      if (variant === "confirm-stop") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
            <rect x="3" y="3" width="10" height="10" rx="1" />
          </svg>
        );
      }
      return <span className="kanban-card-spin" />;
    }
    if (card.status === "failed") {
      if (variant === "confirm-retry") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="2 8 6 12 14 4" />
            <path d="M14 8A6 6 0 1 1 8 2" />
          </svg>
        );
      }
      return (
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="4" y1="4" x2="12" y2="12" />
          <line x1="12" y1="4" x2="4" y2="12" />
        </svg>
      );
    }
    if (card.status === "done") {
      if (variant === "confirm-retry") {
        return (
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="2 8 6 12 14 4" />
            <path d="M14 8A6 6 0 1 1 8 2" />
          </svg>
        );
      }
      return (
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="3 8 7 12 13 4" />
        </svg>
      );
    }
    return null;
  };

  const statusTitle = () => {
    if (card.status === "running") {
      return variant === "confirm-stop"
        ? "Click to stop"
        : "Agent is working — click to stop";
    }
    if (card.status === "failed") {
      return variant === "confirm-retry"
        ? "Click to retry"
        : "Agent failed — click to retry";
    }
    if (card.status === "done") {
      return variant === "confirm-retry"
        ? "Click to re-run"
        : "Agent finished — click to re-run or view activity";
    }
    return "";
  };

  const statusClass = () => {
    if (card.status === "running") {
      return variant === "confirm-stop"
        ? "kanban-card-status--stop"
        : "kanban-card-status--running";
    }
    if (card.status === "failed") {
      return variant === "confirm-retry"
        ? "kanban-card-status--retry"
        : "kanban-card-status--failed";
    }
    if (card.status === "done") {
      return variant === "confirm-retry"
        ? "kanban-card-status--retry"
        : "kanban-card-status--done";
    }
    return "";
  };

  const closeCtx = useCallback(() => {
    setCtxMenu(null);
    setMoveSubOpen(false);
  }, []);

  useEffect(() => {
    if (!ctxMenu) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeCtx(); };
    const onClick = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest(".kanban-card-ctx")) closeCtx();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
    };
  }, [ctxMenu, closeCtx]);

  const otherLanes = allLanes.filter((l) => l.id !== laneId);

  const menuWidth = 190;
  const subWidth = 160;
  const menuHeight = 260;
  const ctxLeft = Math.min(ctxMenu?.x ?? 0, window.innerWidth - menuWidth - 8);
  const ctxTop = Math.min(ctxMenu?.y ?? 0, window.innerHeight - menuHeight);
  const subLeft = ctxLeft + menuWidth + 2 + subWidth > window.innerWidth
    ? ctxLeft - subWidth - 2
    : ctxLeft + menuWidth + 2;

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
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setCtxMenu({ x: e.clientX, y: e.clientY });
        setMoveSubOpen(false);
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
      {card.body && (
        <div
          className="kanban-card-body"
          onWheel={(e) => e.stopPropagation()}
        >
          <MarkdownView>{card.body}</MarkdownView>
        </div>
      )}
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
            className={`kanban-card-status ${statusClass()}`}
            onClick={handleStatusClick}
            title={statusTitle()}
          >
            {statusIcon()}
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
      {ctxMenu && (
        <div className="kanban-card-ctx" style={{ top: ctxTop, left: ctxLeft, width: menuWidth }}>
          <button className="kanban-ctx-item" onClick={() => { closeCtx(); onOpenInChat(); }}>
            {t("card.ctxOpenChat")}
          </button>
          {(card.status === "done" || card.status === "failed") && (
            <button className="kanban-ctx-item" onClick={() => { closeCtx(); onViewActivity(); }}>
              {t("card.ctxViewActivity")}
            </button>
          )}
          {card.status === "running" && (
            <button className="kanban-ctx-item" onClick={() => { closeCtx(); onCancel(); }}>
              {t("card.ctxCancel")}
            </button>
          )}
          {(card.status === "failed" || card.status === "done") && (
            <button className="kanban-ctx-item" onClick={() => { closeCtx(); onRetry(); }}>
              {card.status === "done" ? t("card.ctxReRun") : t("card.ctxRetry")}
            </button>
          )}
          {otherLanes.length > 0 && (
            <>
              <div className="kanban-ctx-divider" />
              <div
                className="kanban-ctx-item kanban-ctx-move-trigger"
                onMouseEnter={() => setMoveSubOpen(true)}
                onMouseLeave={() => setMoveSubOpen(false)}
              >
                <span>{t("card.ctxMoveTo")}</span>
                <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor" style={{ marginLeft: "auto" }}>
                  <path d="M6 4l6 4-6 4z" />
                </svg>
                {moveSubOpen && (
                  <div className="kanban-ctx-sub" style={{ top: ctxTop, left: subLeft }}>
                    {otherLanes.map((lane) => (
                      <button
                        key={lane.id}
                        className="kanban-ctx-item"
                        onClick={(e) => { e.stopPropagation(); closeCtx(); onMoveCard(lane.id); }}
                      >
                        {lane.title}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
          <div className="kanban-ctx-divider" />
          <button className="kanban-ctx-item kanban-ctx-item--danger" onClick={() => { closeCtx(); onDelete(); }}>
            {t("card.ctxDelete")}
          </button>
        </div>
      )}
    </div>
  );
}
