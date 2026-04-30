import { useRef, useState } from "react";
import MarkdownView from "./MarkdownView";
import MarkdownEditor, { type MarkdownEditorHandle } from "./MarkdownEditor";
import { useVaultLinkPreview } from "./vaultLink";
import {
  addVaultKanbanCard,
  patchVaultKanbanCard,
  type KanbanCard,
  type KanbanCardPriority,
} from "../api";
import "./CardDetailModal.css";
import "./Modal.css";

interface Props {
  /** Existing card to edit. Omit for create mode (pair with `lane`). */
  card?: KanbanCard;
  /** Required for create mode — destination lane id for the new card. */
  lane?: string;
  boardPath: string;
  onClose: () => void;
  onSaved: (card: KanbanCard) => void;
}

const TOOLBAR_ACTIONS = [
  { label: "B",    title: "Bold",          action: "wrap",  args: ["**", "**"],  cls: "tb-bold" },
  { label: "I",    title: "Italic",        action: "wrap",  args: ["_", "_"],    cls: "tb-italic" },
  { label: "S̶",   title: "Strikethrough", action: "wrap",  args: ["~~", "~~"],  cls: "tb-strike" },
  { label: "`",    title: "Inline code",   action: "wrap",  args: ["`", "`"],    cls: "tb-code" },
  { label: "—",   title: "Separator",      action: "sep",   args: [],            cls: "" },
  { label: "•",    title: "Bullet list",   action: "line",  args: ["- "],        cls: "" },
  { label: "1.",   title: "Numbered list", action: "line",  args: ["1. "],       cls: "" },
  { label: "[ ]", title: "Task",          action: "line",  args: ["- [ ] "],    cls: "" },
  { label: "—",   title: "Separator",      action: "sep",   args: [],            cls: "" },
  { label: "🔗",  title: "Link",          action: "link",  args: [],            cls: "" },
] as const;

export default function CardDetailModal({ card, lane, boardPath, onClose, onSaved }: Props) {
  const isCreate = !card;
  // Create mode skips the read-only view and lands directly on the form.
  const [mode, setMode] = useState<"view" | "edit">(isCreate ? "edit" : "view");
  const [editTitle, setEditTitle] = useState(card?.title ?? "");
  const [editBody, setEditBody] = useState(card?.body ?? "");
  const [editDue, setEditDue] = useState(card?.due ?? "");
  const [editPriority, setEditPriority] = useState<KanbanCardPriority | "">(
    (card?.priority as KanbanCardPriority) ?? "",
  );
  const [editLabels, setEditLabels] = useState((card?.labels ?? []).join(", "));
  const [editAssignees, setEditAssignees] = useState((card?.assignees ?? []).join(", "));
  const [saving, setSaving] = useState(false);
  const editorRef = useRef<MarkdownEditorHandle>(null);
  const { onPreview, modal } = useVaultLinkPreview();

  const splitCSV = (s: string): string[] =>
    s.split(",").map((x) => x.trim()).filter(Boolean);

  const hasMetadata =
    !!editDue || editPriority !== "" || !!editLabels.trim() || !!editAssignees.trim();
  const trimmedTitle = editTitle.trim();
  const canSave = isCreate ? trimmedTitle.length > 0 : true;

  const handleSave = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      if (isCreate) {
        if (!lane) {
          // Programmer error — lane is required for create mode.
          setSaving(false);
          return;
        }
        const created = await addVaultKanbanCard(boardPath, {
          lane,
          title: trimmedTitle,
          body: editBody,
        });
        // POST endpoint only accepts {lane, title, body}; apply the rest via PATCH.
        const final = hasMetadata
          ? await patchVaultKanbanCard(boardPath, created.id, {
              due: editDue || null,
              priority: editPriority === "" ? "" : editPriority,
              labels: splitCSV(editLabels),
              assignees: splitCSV(editAssignees),
            })
          : created;
        onSaved(final);
        return;
      }
      const updated = await patchVaultKanbanCard(boardPath, card!.id, {
        title: trimmedTitle || card!.title,
        body: editBody,
        due: editDue || null,
        priority: editPriority === "" ? "" : editPriority,
        labels: splitCSV(editLabels),
        assignees: splitCSV(editAssignees),
      });
      onSaved(updated);
    } catch {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    if (isCreate) {
      onClose();
      return;
    }
    setEditTitle(card!.title);
    setEditBody(card!.body ?? "");
    setEditDue(card!.due ?? "");
    setEditPriority((card!.priority as KanbanCardPriority) ?? "");
    setEditLabels((card!.labels ?? []).join(", "));
    setEditAssignees((card!.assignees ?? []).join(", "));
    setMode("view");
  };

  const handleToolbar = (action: string, args: readonly string[]) => {
    const ed = editorRef.current;
    if (!ed) return;
    if (action === "wrap") {
      ed.wrapSelection(args[0], args[1]);
    } else if (action === "line") {
      ed.insertAtLineStart(args[0]);
    } else if (action === "link") {
      ed.wrapSelection("[", "](url)");
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-dialog card-detail-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {mode === "view" && card ? (
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
            {(card.due || card.priority || (card.labels?.length ?? 0) > 0 || (card.assignees?.length ?? 0) > 0) && (
              <div className="card-detail-meta">
                {card.priority && <span className="card-detail-meta-pill">priority: {card.priority}</span>}
                {card.due && <span className="card-detail-meta-pill">due: {card.due}</span>}
                {(card.labels ?? []).map((l) => (
                  <span key={l} className="card-detail-meta-pill">#{l}</span>
                ))}
                {(card.assignees ?? []).map((a) => (
                  <span key={a} className="card-detail-meta-pill">@{a}</span>
                ))}
              </div>
            )}
            <div className="card-detail-body">
              {card.body ? (
                <MarkdownView onVaultLinkPreview={onPreview}>{card.body}</MarkdownView>
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
            <div className="card-detail-meta-edit">
              <label>
                Due
                <input
                  type="date"
                  value={editDue}
                  onChange={(e) => setEditDue(e.target.value)}
                />
              </label>
              <label>
                Priority
                <select
                  value={editPriority}
                  onChange={(e) => setEditPriority(e.target.value as KanbanCardPriority | "")}
                >
                  <option value="">—</option>
                  <option value="low">Low</option>
                  <option value="med">Medium</option>
                  <option value="high">High</option>
                  <option value="urgent">Urgent</option>
                </select>
              </label>
              <label>
                Labels
                <input
                  type="text"
                  value={editLabels}
                  onChange={(e) => setEditLabels(e.target.value)}
                  placeholder="comma,separated"
                />
              </label>
              <label>
                Assignees
                <input
                  type="text"
                  value={editAssignees}
                  onChange={(e) => setEditAssignees(e.target.value)}
                  placeholder="comma,separated"
                />
              </label>
            </div>
            <div className="card-detail-toolbar">
              {TOOLBAR_ACTIONS.map((btn, i) =>
                btn.action === "sep" ? (
                  <span key={i} className="tb-sep" />
                ) : (
                  <button
                    key={i}
                    className={`tb-btn${btn.cls ? ` ${btn.cls}` : ""}`}
                    title={btn.title}
                    onMouseDown={(e) => {
                      e.preventDefault(); // keep editor focus
                      handleToolbar(btn.action, btn.args);
                    }}
                  >
                    {btn.label}
                  </button>
                )
              )}
            </div>
            <div className="card-detail-editor">
              <MarkdownEditor
                ref={editorRef}
                value={editBody}
                onChange={setEditBody}
                wordWrap
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
                disabled={saving || !canSave}
              >
                {saving ? "Saving…" : isCreate ? "Create" : "Save"}
              </button>
            </div>
          </>
        )}
      </div>
      {modal}
    </div>
  );
}
