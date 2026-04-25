/**
 * LocalModels — a friendly, non-technical view for installing free models
 * that run on the user's own machine.
 *
 * Sections:
 *   1. Hardware card (RAM / disk / chip) so the user knows what fits.
 *   2. Installed list with Activate / Delete.
 *   3. Recommended catalog (curated picks from `catalog.ts`) with one-click
 *      Install. No search box, no jargon.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  activateModel,
  deleteInstalled,
  fmtBytes,
  getHardware,
  listDownloads,
  listInstalled,
  startDownload,
  type DownloadTask,
  type HardwareProbe,
  type InstalledModel,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import { CATALOG, type CatalogEntry } from "./catalog";
import SearchModal from "./SearchModal";
import "./LocalModels.css";

interface Props {
  onRefresh: () => void;
}

export default function LocalModels({ onRefresh }: Props) {
  const toast = useToast();
  const [hw, setHw] = useState<HardwareProbe | null>(null);
  const [installed, setInstalled] = useState<InstalledModel[]>([]);
  const [downloads, setDownloads] = useState<DownloadTask[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);

  const refreshLocal = useCallback(async () => {
    try {
      const [inst, dls] = await Promise.all([listInstalled(), listDownloads()]);
      setInstalled(inst);
      setDownloads(dls);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load installed models");
    }
  }, []);

  useEffect(() => {
    getHardware().then(setHw).catch((e) => setError(e.message));
    refreshLocal();
  }, [refreshLocal]);

  // Poll downloads while any task is in flight.
  useEffect(() => {
    const inFlight = downloads.some((t) => t.status === "downloading" || t.status === "pending");
    if (!inFlight) return;
    const id = setInterval(refreshLocal, 1000);
    return () => clearInterval(id);
  }, [downloads, refreshLocal]);

  // Auto-activate a freshly-installed model when nothing is active yet —
  // matches the user's expectation that "Install" makes the model usable
  // in the chat picker without an extra step.
  const autoActivatedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (installed.length === 0) return;
    if (installed.some((m) => m.is_active)) return;
    const candidate = installed.find((m) => !autoActivatedRef.current.has(m.filename));
    if (!candidate) return;
    autoActivatedRef.current.add(candidate.filename);
    onActivate(candidate.filename);
    // onActivate is intentionally omitted from deps: we want this to trigger
    // exactly once per (filename, no-active-model) transition.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [installed]);

  const onInstall = useCallback(async (entry: CatalogEntry) => {
    setBusy(entry.id);
    try {
      await startDownload(entry.repo_id, entry.filename);
      toast.info(`Downloading ${entry.title}…`);
      refreshLocal();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusy(null);
    }
  }, [refreshLocal, toast]);

  const onActivate = useCallback(async (filename: string) => {
    setBusy(filename);
    try {
      await activateModel(filename);
      toast.success(`Activated ${filename}`);
      await refreshLocal();
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Activate failed");
    } finally {
      setBusy(null);
    }
  }, [refreshLocal, onRefresh, toast]);

  const onDelete = useCallback(async (filename: string) => {
    if (!window.confirm(`Remove ${filename}? The file will be deleted from disk.`)) return;
    setBusy(filename);
    try {
      await deleteInstalled(filename);
      toast.success(`Removed ${filename}`);
      refreshLocal();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(null);
    }
  }, [refreshLocal, toast]);

  // Map repo_id+filename → in-flight download for catalog state.
  const downloadByKey = useMemo(() => {
    const m = new Map<string, DownloadTask>();
    for (const t of downloads) m.set(`${t.repo_id}/${t.filename}`, t);
    return m;
  }, [downloads]);

  // Quick lookup of what's already installed.
  const installedByFilename = useMemo(() => {
    const m = new Map<string, InstalledModel>();
    for (const i of installed) m.set(i.filename, i);
    return m;
  }, [installed]);

  return (
    <section className="settings-section local-models-section">
      <h3 className="graphrag-section-title">On-device models</h3>
      <p className="local-intro">
        Run free models directly on your computer — no account, no cost,
        works offline once installed.
      </p>

      {hw && (
        <div className="local-hw-card">
          <div className="local-hw-row">
            <span className="local-hw-chip">{hw.chip}</span>
            <span className="local-hw-stat">{hw.ram_gb.toFixed(1)} GB RAM</span>
            <span className="local-hw-stat">{hw.free_disk_gb.toFixed(0)} GB free disk</span>
            {hw.is_apple_silicon && <span className="local-hw-tag">Metal</span>}
          </div>
        </div>
      )}

      {error && <p className="settings-error">{error}</p>}

      <div className="local-installed">
        <div className="local-subhead">Installed</div>
        {installed.length === 0 && (
          <p className="local-empty">No models installed yet — pick one below.</p>
        )}
        {installed.map((m) => (
          <div key={m.filename} className={`local-row${m.is_active ? " local-row--active" : ""}`}>
            <div className="local-row-main">
              <span className="local-row-name">{m.filename}</span>
              <span className="local-row-meta">
                {fmtBytes(m.size_bytes)}{m.is_active ? " · in use" : ""}
              </span>
            </div>
            <div className="local-row-actions">
              {!m.is_active && (
                <button
                  className="settings-btn"
                  disabled={busy === m.filename}
                  onClick={() => onActivate(m.filename)}
                >
                  Use this
                </button>
              )}
              <button
                className="settings-btn settings-btn--danger"
                disabled={busy === m.filename || m.is_active}
                onClick={() => onDelete(m.filename)}
                title={m.is_active ? "Switch to another model first" : ""}
              >
                Remove
              </button>
            </div>
          </div>
        ))}
      </div>

      {downloads.filter((t) => t.status === "downloading" || t.status === "pending").length > 0 && (
        <div className="local-downloads">
          <div className="local-subhead">Downloading</div>
          {downloads
            .filter((t) => t.status === "downloading" || t.status === "pending")
            .map((t) => {
              const pct = t.total_bytes > 0
                ? Math.min(100, (t.downloaded_bytes / t.total_bytes) * 100)
                : 0;
              return (
                <div key={t.task_id} className="local-dl">
                  <div className="local-dl-name">{t.filename}</div>
                  <div className="local-dl-bar">
                    <div className="local-dl-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="local-dl-meta">
                    {fmtBytes(t.downloaded_bytes)} / {fmtBytes(t.total_bytes)} ({pct.toFixed(0)}%)
                  </div>
                </div>
              );
            })}
        </div>
      )}

      <div className="local-catalog">
        <div className="local-subhead">Available to download</div>
        <div className="local-catalog-grid">
          {CATALOG.map((entry) => {
            const ramOK = !hw || hw.ram_gb >= entry.min_ram_gb;
            const installedHere = installedByFilename.has(entry.filename);
            const dl = downloadByKey.get(`${entry.repo_id}/${entry.filename}`);
            const inProgress = dl && (dl.status === "downloading" || dl.status === "pending");
            const isBusy = busy === entry.id;

            let label: string;
            let disabled = false;
            if (installedHere) {
              label = "Installed";
              disabled = true;
            } else if (inProgress) {
              label = "Downloading…";
              disabled = true;
            } else if (!ramOK) {
              label = "Needs more RAM";
              disabled = true;
            } else if (isBusy) {
              label = "Starting…";
              disabled = true;
            } else {
              label = "Install";
            }

            return (
              <div
                key={entry.id}
                className={`local-card${!ramOK ? " local-card--disabled" : ""}`}
              >
                <div className="local-card-head">
                  <span className="local-card-title">{entry.title}</span>
                  <span className="local-card-size">~{entry.approx_size_gb.toFixed(1)} GB</span>
                </div>
                <p className="local-card-desc">{entry.description}</p>
                <div className="local-card-badges">
                  <span className="local-card-badge local-card-badge--free">Free</span>
                  {entry.badges.map((b) => (
                    <span key={b} className="local-card-badge">{b}</span>
                  ))}
                  {!ramOK && (
                    <span className="local-card-badge local-card-badge--warn">
                      Needs {entry.min_ram_gb} GB RAM
                    </span>
                  )}
                </div>
                <button
                  className="settings-btn local-card-action"
                  disabled={disabled}
                  onClick={() => onInstall(entry)}
                >
                  {label}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div className="local-more">
        <button
          type="button"
          className="settings-btn local-more-btn"
          onClick={() => setSearchOpen(true)}
        >
          More models…
        </button>
        <span className="local-more-hint">Browse Hugging Face</span>
      </div>

      <SearchModal
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onDownloadStarted={refreshLocal}
        installedByFilename={installedByFilename}
        downloadByKey={downloadByKey}
      />

    </section>
  );
}
