/**
 * SearchModal — full-screen overlay for browsing Hugging Face GGUF repos.
 *
 * Replaces the cramped inline disclosure in the Settings drawer. Users see
 * one repo per row with breathing room, and clicking a repo expands its
 * GGUF file list inline with quant + size + Download button.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  fmtBytes,
  listRepoFiles,
  searchHf,
  startDownload,
  type DownloadTask,
  type HfFile,
  type HfRepo,
  type InstalledModel,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  open: boolean;
  onClose: () => void;
  onDownloadStarted: () => void;
  installedByFilename: Map<string, InstalledModel>;
  downloadByKey: Map<string, DownloadTask>;
}

export default function SearchModal({
  open,
  onClose,
  onDownloadStarted,
  installedByFilename,
  downloadByKey,
}: Props) {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<HfRepo[]>([]);
  const [expanded, setExpanded] = useState<Record<string, HfFile[]>>({});
  const [loadingFiles, setLoadingFiles] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset state when reopened.
  useEffect(() => {
    if (open) {
      setQuery("");
      setResults([]);
      setExpanded({});
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // ESC closes.
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [open, onClose]);

  // Debounced search.
  useEffect(() => {
    if (!open) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) {
      setResults([]);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const repos = await searchHf(query.trim(), 25);
        setResults(repos);
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Search failed");
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, open, toast]);

  const expandRepo = useCallback(async (repoId: string) => {
    if (expanded[repoId]) {
      setExpanded((prev) => {
        const next = { ...prev };
        delete next[repoId];
        return next;
      });
      return;
    }
    setLoadingFiles(repoId);
    try {
      const files = await listRepoFiles(repoId);
      setExpanded((prev) => ({ ...prev, [repoId]: files }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Couldn't list files for ${repoId}`);
    } finally {
      setLoadingFiles(null);
    }
  }, [expanded, toast]);

  const onDownload = useCallback(async (repoId: string, filename: string) => {
    const key = `${repoId}/${filename}`;
    setBusy(key);
    try {
      await startDownload(repoId, filename);
      toast.info(`Downloading ${filename}…`);
      onDownloadStarted();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy(null);
    }
  }, [onDownloadStarted, toast]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="search-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="search-modal-head">
          <h3 className="search-modal-title">Browse Hugging Face</h3>
          <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <input
          ref={inputRef}
          type="text"
          className="settings-input search-modal-input"
          placeholder="Search GGUF models — e.g. mistral, deepseek-coder, llama-3.1-8b…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />

        <div className="search-modal-body">
          {searching && <p className="local-empty">Searching…</p>}
          {!searching && query.trim() && results.length === 0 && (
            <p className="local-empty">No results.</p>
          )}
          {!query.trim() && (
            <p className="local-empty">
              Type to find any GGUF model on Hugging Face. Pick a quantization variant —
              smaller files use less RAM but are slightly less accurate.
            </p>
          )}

          {results.map((r) => {
            const files = expanded[r.id];
            const isLoadingFiles = loadingFiles === r.id;
            const isOpen = !!files;
            const topTags = r.tags
              .filter((t) => !["gguf", "GGUF", "region:us"].includes(t) && !t.startsWith("base_model:"))
              .slice(0, 4);

            return (
              <div key={r.id} className={`search-repo${isOpen ? " search-repo--open" : ""}`}>
                <button
                  type="button"
                  className="search-repo-head"
                  onClick={() => expandRepo(r.id)}
                >
                  <div className="search-repo-main">
                    <span className="search-repo-id">{r.id}</span>
                    <div className="search-repo-tags">
                      {topTags.map((t) => (
                        <span key={t} className="search-repo-tag">{t}</span>
                      ))}
                    </div>
                  </div>
                  <div className="search-repo-stats">
                    <span title="downloads">↓ {r.downloads.toLocaleString()}</span>
                    <span title="likes">♥ {r.likes}</span>
                    <span className="search-repo-chev">{isOpen ? "▾" : "▸"}</span>
                  </div>
                </button>

                {isLoadingFiles && (
                  <div className="search-repo-files"><p className="local-empty">Loading files…</p></div>
                )}
                {files && (
                  <div className="search-repo-files">
                    {files.length === 0 && (
                      <p className="local-empty">No GGUF files in this repo.</p>
                    )}
                    {files.map((f) => {
                      const dlKey = `${r.id}/${f.filename}`;
                      const dl = downloadByKey.get(dlKey);
                      const inProgress = dl && (dl.status === "downloading" || dl.status === "pending");
                      const alreadyInstalled = installedByFilename.has(f.filename);
                      return (
                        <div key={f.filename} className="search-file">
                          <div className="search-file-info">
                            <span className="search-file-name">{f.filename}</span>
                            <span className="search-file-meta">
                              {fmtBytes(f.size_bytes)} · {f.quant_label}
                              {!f.fits_in_ram && (
                                <span className="search-file-warn"> · won't fit RAM</span>
                              )}
                            </span>
                          </div>
                          <button
                            type="button"
                            className="settings-btn"
                            disabled={busy === dlKey || !!inProgress || alreadyInstalled}
                            onClick={() => onDownload(r.id, f.filename)}
                          >
                            {alreadyInstalled
                              ? "Installed"
                              : inProgress
                                ? "Downloading…"
                                : "Download"}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
