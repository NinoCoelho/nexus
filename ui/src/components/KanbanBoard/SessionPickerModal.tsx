import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { CardSession } from "../../api/dispatch";
import "../Modal.css";

interface Props {
  sessions: CardSession[];
  onSelect: (sessionId: string) => void;
  onNewSession: () => void;
  onCancel: () => void;
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    if (sameDay) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export default function SessionPickerModal({ sessions, onSelect, onNewSession, onCancel }: Props) {
  const { t } = useTranslation("kanban");
  const ref = useRef<HTMLDivElement>(null);

  const handleKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    },
    [onCancel],
  );

  useEffect(() => {
    ref.current?.focus();
  }, []);

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal-dialog session-picker-dialog"
        ref={ref}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKey}
      >
        <div className="modal-title">{t("sessionPicker.title", "Select session")}</div>
        <ul className="session-picker-list">
          {sessions.map((s) => (
            <li key={s.id}>
              <button className="session-picker-item" onClick={() => onSelect(s.id)}>
                <span className="session-picker-item-title">{s.title || "New session"}</span>
                <span className="session-picker-item-date">{fmtDate(s.updated_at)}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="modal-actions">
          <button className="modal-btn" onClick={onCancel}>
            {t("common:buttons.cancel", "Cancel")}
          </button>
          <button className="modal-btn modal-btn--primary" onClick={onNewSession}>
            {t("sessionPicker.newSession", "New session")}
          </button>
        </div>
      </div>
    </div>
  );
}
