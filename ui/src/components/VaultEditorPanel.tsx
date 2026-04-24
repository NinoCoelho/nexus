/**
 * VaultEditorPanel — the main file editor/preview for a single vault file.
 *
 * Rendering strategy:
 *   - Markdown files with `kanban-plugin: basic` frontmatter → <KanbanBoard>
 *   - All other files in edit mode → raw <textarea>
 *   - All other files in view mode → <MarkdownView> (rendered markdown)
 *
 * Saves are debounced via Cmd+S; the panel does NOT auto-save on every
 * keystroke (intentional — vault files may be large and the backend writes
 * are atomic, so frequent saves create churn in the FTS/tag indexes).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import MarkdownEditor from "./MarkdownEditor";
import KanbanBoard from "./KanbanBoard";
import DataTableView from "./DataTableView";
import FilePreview from "./FilePreview";
import { getVaultFile, putVaultFile, vaultRawUrl } from "../api";
import { classify } from "../fileTypes";
import "./VaultView.css";

/** Check if a markdown file declares itself as a kanban board via frontmatter. */
function isKanbanContent(content: string): boolean {
  if (!content.startsWith("---")) return false;
  const end = content.indexOf("\n---", 3);
  if (end === -1) return false;
  const fm = content.slice(3, end);
  return /^\s*kanban-plugin\s*:/m.test(fm);
}

/** Check if a markdown file declares itself as a data-table via frontmatter. */
function isDataTableContent(content: string): boolean {
  if (!content.startsWith("---")) return false;
  const end = content.indexOf("\n---", 3);
  if (end === -1) return false;
  const fm = content.slice(3, end);
  return /^\s*data-table-plugin\s*:/m.test(fm);
}

interface VaultEditorPanelProps {
  selectedPath: string | null;
  /** Kept for sidebar plumbing symmetry; not consumed in the editor itself. */
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string) => void;
  onViewEntityGraph?: (path: string) => void;
}

export default function VaultEditorPanel({ selectedPath, onOpenInChat, onViewEntityGraph }: VaultEditorPanelProps) {
  const [content, setContent] = useState("");
  const [fileSize, setFileSize] = useState<number | undefined>(undefined);
  const [isBinary, setIsBinary] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [fileError, setFileError] = useState<string | null>(null);

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fileKind = selectedPath ? classify(selectedPath).kind : null;
  const canEdit = !isBinary && (fileKind === "markdown" || fileKind === "text" || fileKind === "code" || fileKind === "csv" || fileKind === "json");

  useEffect(() => {
    if (!selectedPath) return;
    setFileError(null);
    setEditMode(false);
    setIsBinary(false);
    setFileSize(undefined);
    getVaultFile(selectedPath)
      .then((f) => {
        setContent(f.content ?? "");
        setFileSize(f.size);
        setIsBinary(!!f.binary);
      })
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
  const isDataTable = !!selectedPath && selectedPath.endsWith(".md") && !isKanban && isDataTableContent(content);

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
              {editMode && canEdit && (
                <button className="vault-pill" onClick={() => void save()} disabled={saveStatus === "saving"}>
                  Save
                </button>
              )}
              {!canEdit && selectedPath && (
                <a
                  href={vaultRawUrl(selectedPath)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="vault-pill"
                  title="Open raw file in a new tab"
                >
                  Open raw
                </a>
              )}
              {canEdit && (
                <button
                  className={`vault-pill${editMode ? " vault-pill--active" : ""}`}
                  onClick={() => setEditMode((m) => !m)}
                >
                  {editMode ? "View" : "Edit"}
                </button>
              )}
            </div>
          </div>
          {fileError ? (
            <div className="vault-file-error">{fileError}</div>
          ) : isKanban && !editMode ? (
            <KanbanBoard path={selectedPath!} onOpenInChat={onOpenInChat} />
          ) : isDataTable && !editMode ? (
            <DataTableView path={selectedPath!} />
          ) : editMode && canEdit ? (
            <MarkdownEditor
              value={content}
              onChange={setContent}
              className="vault-markdown-editor"
            />
          ) : (
            <div className="vault-preview">
              <FilePreview
                path={selectedPath!}
                content={content}
                size={fileSize}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
