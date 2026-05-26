import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createProject } from "../../api/projects";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const COLORS = [
  "",
  "#ef4444",
  "#f97316",
  "#eab308",
  "#22c55e",
  "#06b6d4",
  "#3b82f6",
  "#8b5cf6",
  "#ec4899",
];

export default function ProjectCreateModal({ open, onClose, onCreated }: Props) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [color, setColor] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setInstructions("");
      setColor("");
      setSubmitting(false);
      setTimeout(() => nameRef.current?.focus(), 50);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const handleSubmit = useCallback(async () => {
    if (!name.trim()) return;
    setSubmitting(true);
    try {
      await createProject({
        name: name.trim(),
        description: description.trim(),
        instructions: instructions.trim(),
        color,
      });
      toast.success(`Project "${name.trim()}" created`);
      onCreated();
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to create project");
    } finally {
      setSubmitting(false);
    }
  }, [name, description, instructions, color, toast, onCreated, onClose]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="reindex-dialog project-create-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="reindex-header">
          <span className="reindex-title">New Project</span>
          <button className="reindex-close" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>

        <div className="project-create-fields">
          <label className="project-create-label">
            Name
            <input
              ref={nameRef}
              className="modal-input"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Marketing Site"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSubmit();
              }}
            />
          </label>

          <label className="project-create-label">
            Description
            <input
              className="modal-input"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What is this project about?"
            />
          </label>

          <label className="project-create-label">
            Instructions
            <textarea
              className="project-create-textarea"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="Custom instructions for the agent when working in this project..."
              rows={3}
            />
          </label>

          <div className="project-create-label">
            Color
            <div className="project-color-picker">
              {COLORS.map((c) => (
                <button
                  key={c || "none"}
                  className={`project-color-swatch${color === c ? " project-color-swatch--active" : ""}`}
                  style={c ? { background: c } : { background: "var(--bg-alt)", border: "1px dashed var(--border)" }}
                  onClick={() => setColor(c)}
                  title={c || "None"}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="reindex-actions">
          <button className="modal-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn--primary"
            onClick={handleSubmit}
            disabled={!name.trim() || submitting}
          >
            {submitting ? "Creating..." : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
