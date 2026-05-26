import { X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getProject,
  updateProject,
  deleteProject,
  type Project,
} from "../../api/projects";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  open: boolean;
  projectId: string | null;
  onClose: () => void;
  onSaved: () => void;
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

export default function ProjectEditModal({ open, projectId, onClose, onSaved }: Props) {
  const toast = useToast();
  const [project, setProject] = useState<Project | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [color, setColor] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open && projectId) {
      getProject(projectId)
        .then((p) => {
          setProject(p);
          setName(p.name);
          setDescription(p.description);
          setInstructions(p.instructions);
          setColor(p.color);
        })
        .catch(() => toast.error("Failed to load project"));
      setTimeout(() => nameRef.current?.focus(), 50);
    }
    if (!open) {
      setProject(null);
      setSubmitting(false);
    }
  }, [open, projectId, toast]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  const handleSave = useCallback(async () => {
    if (!projectId || !name.trim()) return;
    setSubmitting(true);
    try {
      await updateProject(projectId, {
        name: name.trim(),
        description: description.trim(),
        instructions: instructions.trim(),
        color,
      });
      toast.success("Project updated");
      onSaved();
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to update project");
    } finally {
      setSubmitting(false);
    }
  }, [projectId, name, description, instructions, color, toast, onSaved, onClose]);

  const handleDelete = useCallback(async () => {
    if (!projectId) return;
    setSubmitting(true);
    try {
      await deleteProject(projectId);
      toast.success("Project deleted");
      onSaved();
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to delete project");
    } finally {
      setSubmitting(false);
    }
  }, [projectId, toast, onSaved, onClose]);

  if (!open || !project) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="reindex-dialog project-create-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="reindex-header">
          <span className="reindex-title">Edit Project</span>
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
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSave();
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
          <button
            className="modal-btn modal-btn--danger"
            onClick={handleDelete}
            disabled={submitting}
          >
            Delete
          </button>
          <div style={{ flex: 1 }} />
          <button className="modal-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn--primary"
            onClick={handleSave}
            disabled={!name.trim() || submitting}
          >
            {submitting ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
