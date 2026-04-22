import { useCallback, useEffect, useRef, useState } from "react";
import MarkdownView from "./MarkdownView";
import KanbanBoard from "./KanbanBoard";
import { getVaultFile, putVaultFile } from "../api";
import "./VaultView.css";

function isKanbanContent(content: string): boolean {
  if (!content.startsWith("---")) return false;
  const end = content.indexOf("\n---", 3);
  if (end === -1) return false;
  const fm = content.slice(3, end);
  return /^\s*kanban-plugin\s*:/m.test(fm);
}

interface VaultEditorPanelProps {
  selectedPath: string | null;
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  onViewEntityGraph?: (path: string) => void;
}

export default function VaultEditorPanel({ selectedPath, onDispatchToChat, onViewEntityGraph }: VaultEditorPanelProps) {
  const [content, setContent] = useState("");
  const [editMode, setEditMode] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [fileError, setFileError] = useState<string | null>(null);

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!selectedPath) return;
    setFileError(null);
    setEditMode(false);
    getVaultFile(selectedPath)
      .then((f) => setContent(f.content))
      .catch(() => setFileError("Couldn't load file — is the server running?"));
  }, [selectedPath]);

  const save = useCallback(async () => {
    if (!selectedPath) return;
    setSaveStatus("saving");
    try {
      await putVaultFile(selectedPath, content);
      setSaveStatus("saved");
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => setSaveStatus("idle"), 1200);
    } catch {
      setSaveStatus("idle");
    }
  }, [selectedPath, content]);

  // Cmd+S
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s" && editMode) {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [editMode, save]);

  const isKanban = !!selectedPath && selectedPath.endsWith(".md") && isKanbanContent(content);

  const breadcrumb = selectedPath
    ? selectedPath.split("/").map((part, i, arr) => (
        <span key={i} className="vault-breadcrumb-part">
          {i > 0 && <span className="vault-breadcrumb-sep">/</span>}
          <span className={i === arr.length - 1 ? "vault-breadcrumb-current" : "vault-breadcrumb-seg"}>{part}</span>
        </span>
      ))
    : null;

  return (
    <div className="vault-editor-panel">
      {!selectedPath ? (
        <div className="vault-empty">
          <svg width="32" height="32" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" style={{ color: "var(--fg-faint)" }}>
            <path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
            <polyline points="12 2 12 6 16 6" />
          </svg>
          <p>Pick a file, or create one&nbsp;<span className="vault-empty-plus">+</span></p>
        </div>
      ) : (
        <>
          <div className="vault-editor-topbar">
            <div className="vault-breadcrumb">{breadcrumb}</div>
            <div className="vault-editor-actions">
              {saveStatus === "saved" && <span className="vault-saved-indicator">saved ✓</span>}
              {onViewEntityGraph && selectedPath && (
                <button
                  className="vault-pill"
                  onClick={() => onViewEntityGraph(selectedPath!)}
                  title="View entity graph for this file"
                >
                  Graph
                </button>
              )}
              {editMode && (
                <button className="vault-pill" onClick={() => void save()} disabled={saveStatus === "saving"}>
                  Save
                </button>
              )}
              <button
                className={`vault-pill${editMode ? " vault-pill--active" : ""}`}
                onClick={() => setEditMode((m) => !m)}
              >
                {editMode ? "View" : "Edit"}
              </button>
            </div>
          </div>
          {fileError ? (
            <div className="vault-file-error">{fileError}</div>
          ) : isKanban && !editMode ? (
            <KanbanBoard path={selectedPath!} onDispatchToChat={onDispatchToChat} />
          ) : editMode ? (
            <textarea
              className="vault-textarea"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              spellCheck={false}
            />
          ) : (
            <div className="vault-preview">
              <MarkdownView>{content}</MarkdownView>
            </div>
          )}
        </>
      )}
    </div>
  );
}
