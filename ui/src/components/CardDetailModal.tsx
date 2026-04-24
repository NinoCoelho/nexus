import { useState } from "react";
import MarkdownView from "./MarkdownView";
import MarkdownEditor from "./MarkdownEditor";
import { patchVaultKanbanCard, type KanbanCard } from "../api";
import "./CardDetailModal.css";
import "./Modal.css";

interface Props {
  card: KanbanCard;
  boardPath: string;
  onClose: () => void;
  onSaved: (card: KanbanCard) => void;
}

export default function CardDetailModal({ card, boardPath, onClose, onSaved }: Props) {
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [editTitle, setEditTitle] = useState(card.title);
  const [editBody, setEditBody] = useState(card.body ?? "");
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const updated = await patchVaultKanbanCard(boardPath, card.id, {
        title: editTitle.trim() || card.title,
        body: editBody,
      });
      onSaved(updated);
    } catch {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditTitle(card.title);
    setEditBody(card.body ?? "");
    setMode("view");
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-dialog card-detail-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {mode === "view" ? (
          <>
            <div className="card-detail-header">
              <div className="card-detail-title">{card.title}</div>
              <div className="card-detail-header-actions">
                <button className="modal-btn modal-btn--primary" onClick={() => setMode("edit")}>
                  Edit
                </button>
                <button className="modal-btn" onClick={onClose}>
                  Close
                </button>
              </div>
            </div>
            <div className="card-detail-body">
              {card.body ? (
                <MarkdownView>{card.body}</MarkdownView>
              ) : (
                <p className="card-detail-empty">No description yet. Click Edit to add one.</p>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="card-detail-header">
              <input
                className="card-detail-title-input"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="Card title"
                autoFocus
              />
            </div>
            <div className="card-detail-editor">
              <MarkdownEditor
                value={editBody}
                onChange={setEditBody}
                blockHeadings
                className="card-detail-cm"
              />
            </div>
            <div className="card-detail-actions">
              <button className="modal-btn" onClick={handleCancel} disabled={saving}>
                Cancel
              </button>
              <button
                className="modal-btn modal-btn--primary"
                onClick={() => void handleSave()}
                disabled={saving}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
