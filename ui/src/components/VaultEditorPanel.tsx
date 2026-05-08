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
import { useTranslation } from "react-i18next";
import MarkdownEditor, { type MarkdownEditorHandle } from "./MarkdownEditor";
import MarkdownView from "./MarkdownView";
import { useVaultLinkPreview, VaultLinkPreviewProvider } from "./vaultLink";
import MermaidSnippets from "./MermaidSnippets";
import KanbanBoard from "./KanbanBoard";
import DataTableView from "./DataTableView";
import CsvEditorView from "./CsvEditorView";
import FilePreview from "./FilePreview";
import VaultHistoryPanel from "./VaultHistoryPanel";
import { getVaultFile, getVaultHistoryStatus, putVaultFile, vaultExportPdfUrl, vaultRawUrl } from "../api";
import { useVaultEvents } from "../hooks/useVaultEvents";
import { useTTS } from "../hooks/useTTS";
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

/** Check if a markdown file declares itself as a calendar via frontmatter. */
function isCalendarContent(content: string): boolean {
  if (!content.startsWith("---")) return false;
  const end = content.indexOf("\n---", 3);
  if (end === -1) return false;
  const fm = content.slice(3, end);
  return /^\s*calendar-plugin\s*:/m.test(fm);
}

interface VaultEditorPanelProps {
  selectedPath: string | null;
  /** Kept for sidebar plumbing symmetry, not consumed in the editor itself. */
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string, model?: string) => void;
  onNavigateToSession?: (sessionId: string) => void;
  onViewEntityGraph?: (path: string) => void;
  /** Called when the user opens a `.md` file with `calendar-plugin:` frontmatter. */
  onOpenCalendar?: (path: string) => void;
  /** Navigate the host app to open `path` in the Vault view — passed to the
   *  embedded vault-link preview modal so its header "Open in Vault" button
   *  can route through the App-level navigator instead of being hidden. */
  onOpenInVault?: (path: string) => void;
  /** Open another data-table (drill-down from related-rows panel). */
  onOpenTable?: (path: string) => void;
}

export default function VaultEditorPanel({ selectedPath, onOpenInChat, onNavigateToSession, onViewEntityGraph, onOpenCalendar, onOpenInVault, onOpenTable }: VaultEditorPanelProps) {
  const { t } = useTranslation("vault");
  const [content, setContent] = useState("");
  const [fileSize, setFileSize] = useState<number | undefined>(undefined);
  const [isBinary, setIsBinary] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [splitMode, setSplitMode] = useState(false);
  const [previewContent, setPreviewContent] = useState("");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [fileError, setFileError] = useState<string | null>(null);
  const [historyEnabled, setHistoryEnabled] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const { onPreview: onVaultPreview, modal: vaultPreviewModal } = useVaultLinkPreview(onOpenInVault);
  const tts = useTTS();

  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const editorRef = useRef<MarkdownEditorHandle>(null);
  // Tracks when the user's most recent save committed so the SSE echo
  // (`vault.indexed` for our own write) doesn't cause an immediate refetch.
  const lastSavedAtRef = useRef(0);
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

  const loadFile = useCallback(() => {
    if (!selectedPath) return;
    getVaultFile(selectedPath)
      .then((f) => {
        setContent(f.content ?? "");
        setFileSize(f.size);
        setIsBinary(!!f.binary);
        setFileError(null);
      })
      .catch(() => setFileError(t("vault:editor.loadError")));
  }, [selectedPath]);

  useEffect(() => {
    if (!selectedPath) return;
    setFileError(null);
    setEditMode(false);
    setIsBinary(false);
    setFileSize(undefined);
    loadFile();
  }, [selectedPath, loadFile]);

  // Poll history status when a path is selected so the History button can
  // hide itself when the feature is disabled. Status is cheap and rarely
  // changes; refresh once per opened file is plenty.
  useEffect(() => {
    if (!selectedPath) return;
    let cancelled = false;
    getVaultHistoryStatus()
      .then((s) => { if (!cancelled) setHistoryEnabled(s.enabled); })
      .catch(() => { if (!cancelled) setHistoryEnabled(false); });
    return () => { cancelled = true; };
  }, [selectedPath]);

  // Auto-refresh when the file changes on disk (e.g. the agent wrote it). We
  // skip while the user is editing so we don't clobber their unsaved buffer,
  // and skip briefly after our own save so the SSE echo doesn't cause a
  // redundant refetch right as the user toggles back to View mode.
  useVaultEvents((ev) => {
    if (!selectedPath || ev.path !== selectedPath) return;
    if (ev.type === "vault.removed") {
      setContent("");
      setEditMode(false);
      setFileError(t("vault:editor.removedError"));
      return;
    }
    if (ev.type === "vault.indexed") {
      if (editMode) return;
      if (saveStatus === "saving") return;
      if (Date.now() - lastSavedAtRef.current < 750) return;
      loadFile();
    }
  });

  const save = useCallback(async () => {
    if (!selectedPath) return;
    setSaveStatus("saving");
    try {
      await putVaultFile(selectedPath, content);
      lastSavedAtRef.current = Date.now();
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
  const isCalendar = !!selectedPath && selectedPath.endsWith(".md") && !isKanban && isCalendarContent(content);
  const isDataTable = !!selectedPath && selectedPath.endsWith(".md") && !isKanban && !isCalendar && isDataTableContent(content);
  const isCsv = fileKind === "csv";

  // Calendars are owned by the Calendar view (it has the dropdown of all
  // calendars in the vault). Hand off the path and bail out of inline render.
  useEffect(() => {
    if (isCalendar && selectedPath && onOpenCalendar) {
      onOpenCalendar(selectedPath);
    }
  }, [isCalendar, selectedPath, onOpenCalendar]);

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
          <p>{t("vault:empty")}&nbsp;<span className="vault-empty-plus">+</span></p>
        </div>
      ) : (
        <>
          <div className="vault-editor-topbar">
            <div className="vault-breadcrumb">{breadcrumb}</div>
            <div className="vault-editor-actions">
              {saveStatus === "saved" && <span className="vault-saved-indicator">{t("vault:editor.saved")}</span>}
              {onViewEntityGraph && selectedPath && (
                <button
                  className="vault-pill"
                  onClick={() => onViewEntityGraph(selectedPath!)}
                  title={t("vault:editor.graphTitle")}
                >
                  {t("vault:editor.graph")}
                </button>
              )}
              {historyEnabled && selectedPath && (
                <button
                  className="vault-pill"
                  onClick={() => setHistoryOpen(true)}
                  title={t("vault:editor.historyTitle")}
                >
                  {t("vault:editor.history")}
                </button>
              )}
              {selectedPath && (isMarkdown || fileKind === "text") && (
                <a
                  href={vaultExportPdfUrl(selectedPath)}
                  download
                  className="vault-pill"
                  title={t("vault:editor.exportPdfTitle")}
                >
                  <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z"/><polyline points="12 2 12 6 16 6"/><text x="6" y="14.5" fontSize="6" fontFamily="sans-serif" fill="currentColor" stroke="none" fontWeight="600">PDF</text></svg>
                </a>
              )}
              {tts.available && content && !editMode && (isMarkdown || fileKind === "text") && (
                <button
                  className={`vault-pill${tts.state === "playing" ? " vault-pill--active" : ""}`}
                  onClick={() => {
                    if (tts.state === "idle") void tts.speak(content);
                    else tts.stop();
                  }}
                  title={tts.state === "playing" ? "Stop reading" : "Read aloud"}
                  aria-pressed={tts.state === "playing"}
                  disabled={tts.state === "loading"}
                >
                  {tts.state === "playing" ? "Stop" : "Read aloud"}
                </button>
              )}
              {editMode && canEdit && isMarkdown && (
                <MermaidSnippets onInsert={insertSnippet} />
              )}
              {editMode && canEdit && isMarkdown && (
                <button
                  className={`vault-pill${splitMode ? " vault-pill--active" : ""}`}
                  onClick={() => setSplitMode((s) => !s)}
                  title={t("vault:editor.splitTitle")}
                >
                  {t("vault:editor.split")}
                </button>
              )}
              {editMode && canEdit && (
                <button className="vault-pill" onClick={() => void save()} disabled={saveStatus === "saving"}>
                  {t("vault:editor.save")}
                </button>
              )}
              {!canEdit && selectedPath && (
                <a
                  href={vaultRawUrl(selectedPath)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="vault-pill"
                  title={t("vault:editor.openRawTitle")}
                >
                  {t("vault:editor.openRaw")}
                </a>
              )}
              {canEdit && !isCsv && (
                <button
                  className={`vault-pill${editMode ? " vault-pill--active" : ""}`}
                  onClick={() => setEditMode((m) => !m)}
                >
                  {editMode ? t("vault:editor.view") : t("vault:editor.edit")}
                </button>
              )}
            </div>
          </div>
          {fileError ? (
            <div className="vault-file-error">{fileError}</div>
          ) : isKanban && !editMode ? (
            <VaultLinkPreviewProvider onPreview={onVaultPreview}>
              <KanbanBoard
                path={selectedPath!}
                onOpenInChat={onOpenInChat}
                onNavigateToSession={onNavigateToSession}
                onOpenInVault={onOpenInVault}
              />
            </VaultLinkPreviewProvider>
          ) : isDataTable && !editMode ? (
            <DataTableView path={selectedPath!} onOpenTable={onOpenTable} />
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
                <MarkdownView onVaultLinkPreview={onVaultPreview}>{previewBody}</MarkdownView>
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
      {historyOpen && selectedPath && (
        <VaultHistoryPanel
          path={selectedPath}
          onClose={() => setHistoryOpen(false)}
          onUndone={() => loadFile()}
        />
      )}
      {vaultPreviewModal}
    </div>
  );
}
