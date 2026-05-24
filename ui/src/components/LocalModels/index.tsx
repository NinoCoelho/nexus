/**
 * LocalModels — a friendly, non-technical view for installing free models
 * that run on the user's own machine.
 *
 * Sections:
 *   1. Hardware card (RAM / disk / chip) so the user knows what fits.
 *   2. Installed list with Start / Stop / Stop All / Remove.
 *   3. HuggingFace search for discovering and downloading models.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelDownload,
  deleteInstalled,
  fmtBytes,
  getBinaryStatus,
  getHardware,
  listDownloads,
  listInstalled,
  startModel,
  stopAllModels,
  stopModel,
  updateBinary,
  type BinaryStatus,
  type DownloadTask,
  type HardwareProbe,
  type InstalledModel,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import SearchModal from "./SearchModal";
import Modal, { type ModalProps } from "../Modal";
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
  const [confirmModal, setConfirmModal] = useState<ModalProps | null>(null);
  const [binStatus, setBinStatus] = useState<BinaryStatus | null>(null);
  const [binUpdating, setBinUpdating] = useState(false);

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
    getBinaryStatus().then(setBinStatus).catch(() => {});
  }, [refreshLocal]);

  // Poll downloads while any task is in flight.
  useEffect(() => {
    const inFlight = downloads.some((t) => t.status === "downloading" || t.status === "pending");
    if (!inFlight) return;
    const id = setInterval(refreshLocal, 1000);
    return () => clearInterval(id);
  }, [downloads, refreshLocal]);

  // Auto-start a freshly-installed model only when no other local model is
  // running. Subsequent installs are user-driven (Start button) so the user
  // controls how much memory gets pinned.
  const autoStartedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (installed.length === 0) return;
    const visible = installed.filter((m) => !m.is_mmproj);
    if (visible.some((m) => m.is_running)) return;
    const candidate = visible.find((m) => !autoStartedRef.current.has(m.filename));
    if (!candidate) return;
    autoStartedRef.current.add(candidate.filename);
    onStart(candidate.filename);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [installed]);

  const onStart = useCallback(async (filename: string) => {
    setBusy(filename);
    try {
      await startModel(filename);
      toast.success(`Started ${filename}`);
      await refreshLocal();
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Start failed");
    } finally {
      setBusy(null);
    }
  }, [refreshLocal, onRefresh, toast]);

  const onStop = useCallback(async (filename: string) => {
    setBusy(filename);
    try {
      await stopModel(filename);
      toast.success(`Stopped ${filename}`);
      await refreshLocal();
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Stop failed");
    } finally {
      setBusy(null);
    }
  }, [refreshLocal, onRefresh, toast]);

  const onCancelDownload = useCallback(async (taskId: string, filename: string) => {
    setDownloads((prev) => prev.filter((t) => t.task_id !== taskId));
    try {
      await cancelDownload(taskId);
      toast.info(`Cancelled ${filename}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Cancel failed");
      refreshLocal();
    }
  }, [refreshLocal, toast]);

  const onStopAll = useCallback(async () => {
    setBusy("__stop_all__");
    try {
      const stopped = await stopAllModels();
      if (stopped.length === 0) {
        toast.info("No models running");
      } else {
        toast.success(`Stopped ${stopped.length} model${stopped.length > 1 ? "s" : ""}`);
      }
      await refreshLocal();
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Stop-all failed");
    } finally {
      setBusy(null);
    }
  }, [refreshLocal, onRefresh, toast]);

  const onDelete = useCallback((filename: string, isRunning: boolean) => {
    const message = isRunning
      ? `Stop and remove ${filename}? The model will be stopped and the file deleted from disk.`
      : `Remove ${filename}? The file will be deleted from disk.`;
    setConfirmModal({
      kind: "confirm",
      title: "Remove model",
      message,
      confirmLabel: isRunning ? "Stop & Remove" : "Remove",
      danger: true,
      onCancel: () => setConfirmModal(null),
      onSubmit: async () => {
        setConfirmModal(null);
        setBusy(filename);
        try {
          await deleteInstalled(filename);
          toast.success(`Removed ${filename}`);
          refreshLocal();
          onRefresh();
        } catch (e) {
          toast.error(e instanceof Error ? e.message : "Delete failed");
        } finally {
          setBusy(null);
        }
      },
    });
  }, [refreshLocal, onRefresh, toast]);

  const onBinaryUpdate = useCallback(async () => {
    setBinUpdating(true);
    try {
      const res = await updateBinary();
      if (res.status === "up_to_date") {
        toast.info("Already up to date");
      } else {
        toast.info(`Updating llama-server to ${res.tag}…`);
        const poll = setInterval(async () => {
          try {
            const s = await getBinaryStatus();
            setBinStatus(s);
            if (!s.downloading) {
              clearInterval(poll);
              setBinUpdating(false);
              if (s.current_version === s.latest_version) {
                toast.success(`Updated to b${s.latest_version}`);
              }
            }
          } catch { /* keep polling */ }
        }, 2000);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Update failed");
      setBinUpdating(false);
    }
  }, [toast]);

  const hasRunning = installed.some((m) => m.is_running && !m.is_mmproj);

  return (
    <div className="local-models-section">
      <p className="local-intro">
        Rode modelos gratuitos diretamente no seu computador — sem conta,
        sem custo, funciona offline depois de instalado.
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

      {binStatus && binStatus.update_available && !binStatus.downloading && (
        <div className="local-update-banner">
          <span className="local-update-text">
            llama-server update available: b{binStatus.current_version} → b{binStatus.latest_version}
          </span>
          <button
            type="button"
            className="settings-btn"
            disabled={binUpdating}
            onClick={onBinaryUpdate}
          >
            {binUpdating ? "Updating…" : "Update"}
          </button>
        </div>
      )}

      {binStatus && binStatus.downloading && (
        <div className="local-update-banner local-update-banner--active">
          <span className="local-update-text">
            Updating llama-server to b{binStatus.latest_version}…
          </span>
        </div>
      )}

      {error && <p className="settings-error">{error}</p>}

      <div className="local-installed">
        <div className="local-subhead-row">
          <div className="local-subhead">Installed</div>
          {hasRunning && (
            <button
              className="settings-btn settings-btn--danger"
              disabled={busy !== null}
              onClick={onStopAll}
              title="Stop all running local models"
            >
              Stop All
            </button>
          )}
        </div>
        {installed.length === 0 && (
          <p className="local-empty">No models installed yet — search below.</p>
        )}
        {installed.map((m) => (
          <div key={m.filename} className={`local-row${m.is_running ? " local-row--active" : ""}`}>
            <div className="local-row-main">
              <span className="local-row-name">{m.filename}</span>
              <span className="local-row-meta">
                {fmtBytes(m.size_bytes)}
                {m.is_running ? ` · running on :${m.port}` : " · stopped"}
              </span>
              {m.has_mamba_layers && !m.is_running && (
                <span className="local-row-warn">
                  Hybrid Mamba model — not supported by this llama.cpp build.
                  Use a standard (non-UD) variant.
                </span>
              )}
            </div>
            <div className="local-row-actions">
              {m.is_running ? (
                <button
                  className="settings-btn"
                  disabled={busy === m.filename || busy === "__stop_all__"}
                  onClick={() => onStop(m.filename)}
                  title="Free memory and unregister from chat picker"
                >
                  Stop
                </button>
              ) : (
                <button
                  className="settings-btn"
                  disabled={busy === m.filename || !!m.has_mamba_layers}
                  onClick={() => onStart(m.filename)}
                  title={m.has_mamba_layers ? "Not compatible with llama.cpp" : "Spin up llama-server and register for chat / extraction"}
                >
                  Start
                </button>
              )}
              <button
                className="settings-btn settings-btn--danger"
                disabled={busy === m.filename || busy === "__stop_all__"}
                onClick={() => onDelete(m.filename, m.is_running)}
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
                    <button
                      type="button"
                      className="settings-btn settings-btn--danger local-dl-cancel"
                      onClick={() => onCancelDownload(t.task_id, t.filename)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              );
            })}
        </div>
      )}

      <div className="local-more">
        <button
          type="button"
          className="settings-btn local-more-btn"
          onClick={() => setSearchOpen(true)}
        >
          Search models…
        </button>
        <span className="local-more-hint">Browse Hugging Face</span>
      </div>

      <SearchModal
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onDownloadStarted={refreshLocal}
      />

      {confirmModal && <Modal {...confirmModal} />}

    </div>
  );
}
