/**
 * SearchMlxModal — full-screen overlay for browsing mlx-community HF repos.
 *
 * Mirrors LocalModels/SearchModal, but a repo expands to a **details card**
 * (quantization, context window, estimated resident size, fits-in-memory
 * verdict) instead of a file list — MLX downloads are whole-snapshot, so
 * there is exactly one thing to download per repo. The details come from the
 * same estimator the activate route enforces, so "fits" here means "fits"
 * later.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ChevronDown, ChevronRight, Heart, X } from "lucide-react";
import {
  listMlxDownloads,
  listMlxInstalled,
  mlxRepoDetails,
  mlxSlugFor,
  searchMlxHf,
  startMlxDownload,
  type MlxDownloadTask,
  type MlxHfRepo,
  type MlxInstalled,
  type MlxRepoDetails,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  open: boolean;
  onClose: () => void;
  onDownloadStarted: () => void;
  /** Shown when a search finds nothing — jumps to the GGUF catalog. */
  onBrowseGguf?: () => void;
}

export default function SearchMlxModal({ open, onClose, onDownloadStarted, onBrowseGguf }: Props) {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<MlxHfRepo[]>([]);
  const [expanded, setExpanded] = useState<Record<string, MlxRepoDetails>>({});
  const [loadingDetails, setLoadingDetails] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const [installed, setInstalled] = useState<MlxInstalled[]>([]);
  const [downloads, setDownloads] = useState<MlxDownloadTask[]>([]);

  const refreshData = useCallback(async () => {
    try {
      const [inst, dls] = await Promise.all([listMlxInstalled(), listMlxDownloads()]);
      setInstalled(inst);
      setDownloads(dls);
    } catch {
      // silent — stale data is fine for download-status badges
    }
  }, []);

  useEffect(() => {
    if (open) refreshData();
  }, [open, refreshData]);

  const installedSlugs = useMemo(() => new Set(installed.map((i) => i.slug)), [installed]);

  const downloadByRepo = useMemo(() => {
    const m = new Map<string, MlxDownloadTask>();
    for (const t of downloads) m.set(t.repo_id, t);
    return m;
  }, [downloads]);

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
        const repos = await searchMlxHf(query.trim(), 25);
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
    setLoadingDetails(repoId);
    try {
      const details = await mlxRepoDetails(repoId);
      setExpanded((prev) => ({ ...prev, [repoId]: details }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Couldn't inspect ${repoId}`);
    } finally {
      setLoadingDetails(null);
    }
  }, [expanded, toast]);

  const onDownload = useCallback(async (repoId: string) => {
    setBusy(repoId);
    try {
      await startMlxDownload(repoId);
      toast.info(`Downloading ${repoId.split("/").pop()}…`);
      onDownloadStarted();
      refreshData();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy(null);
    }
  }, [onDownloadStarted, refreshData, toast]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="search-modal-head">
          <h3 className="search-modal-title">Recommended models</h3>
          <button className="drawer-close" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </div>

        <input
          ref={inputRef}
          type="text"
          className="settings-input search-modal-input"
          placeholder="Search models tuned for this Mac — e.g. qwen, llama, gemma…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />

        <div className="search-modal-body">
          {searching && <p className="local-empty">Searching…</p>}
          {!searching && query.trim() && results.length === 0 && (
            <div>
              <p className="local-empty">No results here.</p>
              {onBrowseGguf && (
                <p className="local-empty">
                  <button type="button" className="settings-btn" onClick={onBrowseGguf}>
                    Search GGUF models instead
                  </button>
                </p>
              )}
            </div>
          )}
          {!query.trim() && (
            <p className="local-empty">
              Whole models tuned for this Mac — they download once and run
              fully in-process, fast and private. Pick a bit-size variant:
              smaller uses less memory, larger is more accurate.
            </p>
          )}

          {results.map((r) => {
            const details = expanded[r.id];
            const isLoading = loadingDetails === r.id;
            const isOpen = !!details;
            const topTags = r.tags
              .filter((t) => !["mlx", "MLX", "region:us", "text-generation"].includes(t) && !t.startsWith("base_model:"))
              .slice(0, 4);
            const slug = mlxSlugFor(r.id);
            const dl = downloadByRepo.get(r.id);
            const inProgress = dl && (dl.status === "downloading" || dl.status === "pending");
            const alreadyInstalled = installedSlugs.has(slug);

            return (
              <div key={r.id} className={`search-repo${isOpen ? " search-repo--open" : ""}`}>
                <button type="button" className="search-repo-head" onClick={() => expandRepo(r.id)}>
                  <div className="search-repo-main">
                    <span className="search-repo-id">{r.id}</span>
                    <div className="search-repo-tags">
                      {topTags.map((t) => (
                        <span key={t} className="search-repo-tag">{t}</span>
                      ))}
                    </div>
                  </div>
                  <div className="search-repo-stats">
                    <span title="downloads"><ArrowDown size={12} /> {r.downloads.toLocaleString()}</span>
                    <span title="likes"><Heart size={12} /> {r.likes}</span>
                    <span className="search-repo-chev">{isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}</span>
                  </div>
                </button>

                {isLoading && (
                  <div className="search-repo-files"><p className="local-empty">Inspecting repo…</p></div>
                )}
                {details && (
                  <div className="search-repo-files">
                    <div className="search-file">
                      <div className="search-file-info">
                        <span className="search-file-name">
                          {details.quantization_bits > 0 ? `${details.quantization_bits}-bit` : "MLX snapshot"}
                          {details.is_vlm ? " · vision" : ""}
                        </span>
                        <span className="search-file-meta">
                          {details.size_gb > 0 ? `${details.size_gb} GB download` : "size unknown"}
                          {details.context_window > 0 && ` · ${details.context_window.toLocaleString()} ctx`}
                          {details.estimated_resident_gb > 0 &&
                            ` · ~${details.estimated_resident_gb} GB in memory`}
                          {details.fits.fits === false && (
                            <span className="search-file-warn"> · won't fit memory</span>
                          )}
                          {details.fits.fits === true && <span className="search-file-ok"> · fits</span>}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="settings-btn"
                        disabled={busy === r.id || !!inProgress || alreadyInstalled}
                        onClick={() => onDownload(r.id)}
                      >
                        {alreadyInstalled
                          ? "Installed"
                          : inProgress
                            ? "Downloading…"
                            : "Download"}
                      </button>
                    </div>
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
