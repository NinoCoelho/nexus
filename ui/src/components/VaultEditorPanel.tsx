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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MarkdownEditor, { type MarkdownEditorHandle } from "./MarkdownEditor";
import MarkdownView from "./MarkdownView";
import MermaidSnippets from "./MermaidSnippets";
import KanbanBoard from "./KanbanBoard";
import DataTableView from "./DataTableView";
import CsvEditorView from "./CsvEditorView";
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
  const [splitMode, setSplitMode] = useState(false);
  const [previewContent, setPreviewContent] = useState("");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [fileError, setFileError] = useState<string | null>(null);

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const editorRef = useRef<MarkdownEditorHandle>(null);
  const fileKind = selectedPath ? classify(selectedPath).kind : null;
  const canEdit = !isBinary && (fileKind === "markdown" || fileKind === "text" || fileKind === "code" || fileKind === "csv" || fileKind === "json");
  const isMarkdown = fileKind === "markdown";

  // Debounce preview updates so re-renders (esp. mermaid) don't fight the typing.
  useEffect(() => {
    if (!splitMode) return;
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => setPreviewContent(content), 220);
    return () => { if (previewTimerRef.current) clearTimeout(previewTimerRef.current); };
  }, [content, splitMode]);

  // When entering split mode, seed the preview immediately.
  useEffect(() => {
    if (splitMode) setPreviewContent(content);
  }, [splitMode]); // eslint-disable-line react-hooks/exhaustive-deps

  const insertSnippet = useCallback((body: string) => {
    setContent((prev) => prev + (prev.endsWith("\n") ? "" : "\n") + body + "\n");
  }, []);

  const previewBody = useMemo(() => previewContent || content, [previewContent, content]);

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
  const isCsv = fileKind === "csv";

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
              {editMode && canEdit && isMarkdown && (
                <MermaidSnippets onInsert={insertSnippet} />
              )}
              {editMode && canEdit && isMarkdown && (
                <button
                  className={`vault-pill${splitMode ? " vault-pill--active" : ""}`}
                  onClick={() => setSplitMode((s) => !s)}
                  title="Toggle live preview pane"
                >
                  Split
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
              {canEdit && !isCsv && (
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
          ) : isCsv ? (
            <CsvEditorView path={selectedPath!} />
          ) : editMode && canEdit && splitMode && isMarkdown ? (
            <div className="vault-split-pane">
              <MarkdownEditor
                ref={editorRef}
                value={content}
                onChange={setContent}
                className="vault-markdown-editor vault-split-editor"
              />
              <div className="vault-split-preview">
                <MarkdownView>{previewBody}</MarkdownView>
              </div>
            </div>
          ) : editMode && canEdit ? (
            <MarkdownEditor
              ref={editorRef}
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
