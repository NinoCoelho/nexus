import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { KanbanBoard } from "../api";
import "./Modal.css";

interface Props {
  board: KanbanBoard;
  onCancel: () => void;
  onSubmit: (patch: { board_prompt: string | null }) => void | Promise<void>;
}

export default function BoardPromptDialog({ board, onCancel, onSubmit }: Props) {
  const { t } = useTranslation("kanban");
  const [prompt, setPrompt] = useState(board.board_prompt ?? "");
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      await onSubmit({
        board_prompt: prompt.trim() ? prompt.trim() : null,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()} style={{ minWidth: 480 }}>
        <div className="modal-title">{t("board.boardSettingsTitle", { title: board.title })}</div>
        <p className="modal-message">
          {t("board.boardPromptDescription")}
        </p>

        <label className="modal-field-label">{t("board.boardPromptLabel")}</label>
        <textarea
          className="modal-input"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("board.boardPromptPlaceholder")}
          rows={6}
          autoFocus
        />

        <div className="modal-actions">
          <button className="modal-btn" onClick={onCancel} disabled={saving}>
            {t("board.cancel", "Cancel")}
          </button>
          <button
            className="modal-btn modal-btn--primary"
            onClick={() => void handleSave()}
            disabled={saving}
          >
            {saving ? t("board.saving", "Saving...") : t("board.save", "Save")}
          </button>
        </div>
      </div>
    </div>
  );
}
