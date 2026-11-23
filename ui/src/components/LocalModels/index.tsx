/**
 * LocalModels — on-device models, one list, no engine choices.
 *
 * MLX (Apple Silicon, in-process) and llama.cpp (GGUF) models live in the
 * same list; each row's Activate/Start dispatches to the right engine. The
 * user never picks an engine: on MLX-capable machines search defaults to
 * the recommended (snapshot) source, with GGUF browsing one click away.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  activateMlx,
  cancelDownload,
  cancelMlxDownload,
  deactivateMlx,
  deleteInstalled,
  deleteMlxInstalled,
  fmtBytes,
  getBinaryStatus,
  getHardware,
  getMlxAvailable,
  getMlxStatus,
  importMlxFolder,
  listDownloads,
  listInstalled,
  listMlxDownloads,
  listMlxInstalled,
  startModel,
  stopAllModels,
  stopModel,
  unloadMlx,
  updateBinary,
  type BinaryStatus,
  type DownloadTask,
  type HardwareProbe,
  type InstalledModel,
  type MlxDownloadTask,
  type MlxInstalled,
  type MlxStatus,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import SearchModal from "./SearchModal";
import SearchMlxModal from "../MlxModels/SearchMlxModal";
import Modal, { type ModalProps } from "../Modal";
import "./LocalModels.css";

interface Props {
  onRefresh: () => void;
}

const LAST_IMPORT_PATH_KEY = "nexus.mlx.lastImportPath";

export default function LocalModels({ onRefresh }: Props) {
  const toast = useToast();
  const [hw, setHw] = useState<HardwareProbe | null>(null);
  const [mlxAvailable, setMlxAvailable] = useState(false);
  const [mlxInstalled, setMlxInstalled] = useState<MlxInstalled[]>([]);
  const [mlxDownloads, setMlxDownloads] = useState<MlxDownloadTask[]>([]);
  const [mlxStatus, setMlxStatus] = useState<MlxStatus | null>(null);
  const [ggufInstalled, setGgufInstalled] = useState<InstalledModel[]>([]);
  const [ggufDownloads, setGgufDownloads] = useState<DownloadTask[]>([]);
  const [binStatus, setBinStatus] = useState<BinaryStatus | null>(null);
  const [binUpdating, setBinUpdating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchSource, setSearchSource] = useState<"mlx" | "gguf" | null>(null);
  const [confirmModal, setConfirmModal] = useState<ModalProps | null>(null);
  const [importing, setImporting] = useState(false);
  const [cancellingGguf, setCancellingGguf] = useState<Set<string>>(new Set());
  const [cancellingMlx, setCancellingMlx] = useState<Set<string>>(new Set());

  const refreshAll = useCallback(async () => {
    try {
      const [inst, dls, mInst, mDls, mStatus] = await Promise.all([
        listInstalled(),
        listDownloads(),
        listMlxInstalled().catch(() => [] as MlxInstalled[]),
        listMlxDownloads().catch(() => [] as MlxDownloadTask[]),
        getMlxStatus().catch(() => null),
      ]);
      setGgufInstalled(inst);
      setGgufDownloads(dls);
      setMlxInstalled(mInst);
      setMlxDownloads(mDls);
      setMlxStatus(mStatus);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load installed models");
    }
  }, []);

  useEffect(() => {
    getHardware().then(setHw).catch(() => {});
    getMlxAvailable()
      .then((a) => setMlxAvailable(a.available))
      .catch(() => {});
    refreshAll();
    getBinaryStatus().then(setBinStatus).catch(() => {});
  }, [refreshAll]);

  // Poll while any download (either engine) is in flight.
  useEffect(() => {
    const inFlight =
      ggufDownloads.some((t) => t.status === "downloading" || t.status === "pending") ||
      mlxDownloads.some((t) => t.status === "downloading" || t.status === "pending");
    if (!inFlight) return;
    const id = setInterval(refreshAll, 1000);
    return () => clearInterval(id);
  }, [ggufDownloads, mlxDownloads, refreshAll]);

  // Auto-start a freshly-installed GGUF model only when no other local model
  // is running (memory control). Subsequent installs are user-driven.
  const autoStartedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (ggufInstalled.length === 0) return;
    const visible = ggufInstalled.filter((m) => !m.is_mmproj);
    if (visible.some((m) => m.is_running)) return;
    const candidate = visible.find((m) => !autoStartedRef.current.has(m.filename));
    if (!candidate) return;
    autoStartedRef.current.add(candidate.filename);
    onStart(candidate.filename);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ggufInstalled]);

  // -- GGUF actions --------------------------------------------------------

  const onStart = useCallback(
    async (filename: string) => {
      setBusy(filename);
      try {
        await startModel(filename);
        toast.success(`Started ${filename}`);
        await refreshAll();
        onRefresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Start failed");
      } finally {
        setBusy(null);
      }
    },
    [refreshAll, onRefresh, toast],
  );

  const onStop = useCallback(
    async (filename: string) => {
      setBusy(filename);
      try {
        await stopModel(filename);
        toast.success(`Stopped ${filename}`);
        await refreshAll();
        onRefresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Stop failed");
      } finally {
        setBusy(null);
      }
    },
    [refreshAll, onRefresh, toast],
  );

  const onStopAll = useCallback(async () => {
    setBusy("__stop_all__");
    try {
      const stopped = await stopAllModels();
      if (stopped.length === 0) {
        toast.info("No models running");
      } else {
        toast.success(`Stopped ${stopped.length} model${stopped.length > 1 ? "s" : ""}`);
      }
      await refreshAll();
      onRefresh();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Stop-all failed");
    } finally {
      setBusy(null);
    }
  }, [refreshAll, onRefresh, toast]);

  const onDeleteGguf = useCallback(
    (filename: string, isRunning: boolean, partCount = 1) => {
      const partNote = partCount > 1 ? ` All ${partCount} parts will be deleted.` : "";
      setConfirmModal({
        kind: "confirm",
        title: "Remove model",
        message: isRunning
          ? `Stop and remove ${filename}? The model will be stopped and its file(s) deleted from disk.${partNote}`
          : `Remove ${filename}? The file(s) will be deleted from disk.${partNote}`,
        confirmLabel: isRunning ? "Stop & Remove" : "Remove",
        danger: true,
        onCancel: () => setConfirmModal(null),
        onSubmit: async () => {
          setConfirmModal(null);
          setBusy(filename);
          try {
            await deleteInstalled(filename);
            toast.success(`Removed ${filename}`);
            refreshAll();
            onRefresh();
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Delete failed");
          } finally {
            setBusy(null);
          }
        },
      });
    },
    [refreshAll, onRefresh, toast],
  );

  const onCancelGgufDownload = useCallback(
    async (taskId: string, filename: string) => {
      setCancellingGguf((prev) => new Set(prev).add(taskId));
      try {
        await cancelDownload(taskId);
        toast.info(`Cancelling ${filename}…`);
      } catch {
        setCancellingGguf((prev) => {
          const next = new Set(prev);
          next.delete(taskId);
          return next;
        });
        toast.error("Cancel failed");
        refreshAll();
      }
    },
    [refreshAll, toast],
  );

  // -- MLX actions ---------------------------------------------------------

  const onActivate = useCallback(
    async (slug: string, force = false) => {
      setBusy(`mlx:${slug}`);
      try {
        const res = await activateMlx(slug, { setDefault: null, force });
        toast.success(`Activated ${res.model_id}`);
        await refreshAll();
        onRefresh();
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Activate failed";
        if (/memory|fit/i.test(msg) && !force) {
          setConfirmModal({
            kind: "confirm",
            title: "Might not fit in memory",
            message: `${msg}\n\nActivate anyway? You can unload it later.`,
            confirmLabel: "Activate anyway",
            onCancel: () => setConfirmModal(null),
            onSubmit: () => {
              setConfirmModal(null);
              onActivate(slug, true);
            },
          });
        } else {
          toast.error(msg);
        }
      } finally {
        setBusy(null);
      }
    },
    [refreshAll, onRefresh, toast],
  );

  const onDeactivate = useCallback(
    async (slug: string) => {
      setBusy(`mlx:${slug}`);
      try {
        await deactivateMlx(slug);
        toast.success(`Deactivated ${slug}`);
        await refreshAll();
        onRefresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Deactivate failed");
      } finally {
        setBusy(null);
      }
    },
    [refreshAll, onRefresh, toast],
  );

  const onUnload = useCallback(async () => {
    setBusy("__unload__");
    try {
      await unloadMlx();
      toast.success("Model unloaded — memory freed");
      await refreshAll();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unload failed");
    } finally {
      setBusy(null);
    }
  }, [refreshAll, toast]);

  const onDeleteMlx = useCallback(
    (m: MlxInstalled) => {
      setConfirmModal({
        kind: "confirm",
        title: "Remove model",
        message: m.imported
          ? `Remove ${m.slug}? Only the link is removed — the original files outside Nexus stay untouched.`
          : `Remove ${m.slug}? The snapshot will be deleted from disk (${m.total_gb} GB).`,
        confirmLabel: "Remove",
        danger: true,
        onCancel: () => setConfirmModal(null),
        onSubmit: async () => {
          setConfirmModal(null);
          setBusy(`mlx:${m.slug}`);
          try {
            await deleteMlxInstalled(m.slug);
            toast.success(`Removed ${m.slug}`);
            refreshAll();
            onRefresh();
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Delete failed");
          } finally {
            setBusy(null);
          }
        },
      });
    },
    [refreshAll, onRefresh, toast],
  );

  const onCancelMlxDownload = useCallback(
    async (taskId: string, slug: string) => {
      setCancellingMlx((prev) => new Set(prev).add(taskId));
      try {
        await cancelMlxDownload(taskId);
        toast.info(`Cancelling ${slug}…`);
      } catch {
        setCancellingMlx((prev) => {
          const next = new Set(prev);
          next.delete(taskId);
          return next;
        });
        toast.error("Cancel failed");
        refreshAll();
      }
    },
    [refreshAll, toast],
  );

  useEffect(() => {
    setCancellingMlx((prev) => {
      const live = new Set(mlxDownloads.map((t) => t.task_id));
      const next = new Set([...prev].filter((id) => live.has(id)));
      return next.size === prev.size ? prev : next;
    });
    setCancellingGguf((prev) => {
      const live = new Set(ggufDownloads.map((t) => t.task_id));
      const next = new Set([...prev].filter((id) => live.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [mlxDownloads, ggufDownloads]);

  const onImportFolder = useCallback(() => {
    const last = localStorage.getItem(LAST_IMPORT_PATH_KEY) ?? "~/.lmstudio/models";
    setConfirmModal({
      kind: "prompt",
      title: "Import models in place",
      message:
        "Scan a folder for models you already downloaded (e.g. an LM Studio library) and register them without re-downloading. Nexus links to the files — it never copies or moves them.",
      defaultValue: last,
      placeholder: "/path/to/models",
      confirmLabel: "Import",
      onCancel: () => setConfirmModal(null),
      onSubmit: async (value: string) => {
        setConfirmModal(null);
        const path = value.trim();
        if (!path) return;
        setImporting(true);
        try {
          localStorage.setItem(LAST_IMPORT_PATH_KEY, path);
          const res = await importMlxFolder(path);
          const n = res.imported.length;
          const a = res.already.length;
          const s = res.skipped.length;
          if (n) toast.success(`Imported ${n} model${n > 1 ? "s" : ""} in place`);
          if (a) toast.info(`${a} model${a > 1 ? "s" : ""} already imported`);
          if (s) {
            const why = res.skipped.find((r) => r.reason)?.reason ?? "";
            toast.error(`${s} skipped${why ? ` — ${why}` : ""}`);
          }
          if (!n && !a && !s) toast.info("No models found in that folder");
          await refreshAll();
          onRefresh();
        } catch (e) {
          toast.error(e instanceof Error ? e.message : "Import failed");
        } finally {
          setImporting(false);
        }
      },
    });
  }, [refreshAll, onRefresh, toast]);

  const onBinaryUpdate = useCallback(async () => {
    setBinUpdating(true);
    try {
      const res = await updateBinary();
      if (res.status === "up_to_date") {
        toast.info("Already up to date");
      } else {
        toast.info(`Updating local engine to ${res.tag}…`);
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

  // -- render --------------------------------------------------------------

  const hasRunning = ggufInstalled.some((m) => m.is_running && !m.is_mmproj);
  const loadedSlug = mlxStatus?.loaded && mlxStatus.model_path
    ? (mlxStatus.model_path.split("/").pop() ?? "")
    : "";
  const openSearch = (source: "mlx" | "gguf") => setSearchSource(source);
  const closeSearch = () => setSearchSource(null);
  const searchOpen = searchSource !== null;

  return (
    <div className="local-models-section">
      <p className="local-intro">
        Models that run on this machine — private, offline after install, no
        per-use cost. Recommended models are tuned for this Mac when possible.
      </p>

      {hw && (
        <div className="local-hw-card">
          <div className="local-hw-row">
            <span className="local-hw-chip">{hw.chip}</span>
            <span className="local-hw-stat">{hw.ram_gb.toFixed(1)} GB RAM</span>
            <span className="local-hw-stat">{hw.free_disk_gb.toFixed(0)} GB free disk</span>
            {mlxAvailable && <span className="local-hw-tag">In-process engine available</span>}
          </div>
        </div>
      )}

      {binStatus && binStatus.update_available && !binStatus.downloading && (
        <div className="local-update-banner">
          <span className="local-update-text">
            Local engine update available: b{binStatus.current_version} → b{binStatus.latest_version}
          </span>
          <button type="button" className="settings-btn" disabled={binUpdating} onClick={onBinaryUpdate}>
            {binUpdating ? "Updating…" : "Update"}
          </button>
        </div>
      )}
      {binStatus && binStatus.downloading && (
        <div className="local-update-banner local-update-banner--active">
          <span className="local-update-text">Updating local engine to b{binStatus.latest_version}…</span>
        </div>
      )}

      {mlxStatus?.loaded && (
        <div className="local-update-banner local-update-banner--active">
          <span className="local-update-text">
            Loaded: {loadedSlug}
            {mlxStatus.resident_seconds != null && mlxStatus.resident_seconds > 0 && (
              <> · resident for {Math.round(mlxStatus.resident_seconds / 60)} min</>
            )}
            {mlxStatus.queue_depth ? ` · ${mlxStatus.queue_depth} queued` : ""}
          </span>
          <button
            type="button"
            className="settings-btn"
            disabled={busy === "__unload__"}
            onClick={onUnload}
            title="Free memory now (the model reloads automatically on next use)"
          >
            {busy === "__unload__" ? "Unloading…" : "Unload"}
          </button>
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
        {mlxInstalled.length === 0 && ggufInstalled.filter((m) => !m.is_mmproj).length === 0 && (
          <p className="local-empty">No models installed yet — search below.</p>
        )}

        {/* In-process rows (Apple Silicon) */}
        {mlxInstalled.map((m) => (
          <div key={`mlx:${m.slug}`} className={`local-row${m.is_active ? " local-row--active" : ""}`}>
            <div className="local-row-main">
              <span className="local-row-name">{m.slug}</span>
              <span className="local-row-meta">
                {m.total_gb > 0 ? `${m.total_gb} GB` : fmtBytes(m.total_bytes)}
                {m.quantization_bits > 0 && ` · ${m.quantization_bits}-bit`}
                {m.context_window > 0 && ` · ${m.context_window.toLocaleString()} ctx`}
                {m.is_vlm && " · vision"}
                {m.is_active ? " · active" : " · not registered"}
              </span>
              <span className={`mlx-badge${m.supports_tools && !m.incomplete ? "" : " mlx-badge--warn"}`}>
                {m.incomplete ? "incomplete" : m.supports_tools ? "tools" : "no tools"}
              </span>
              {m.incomplete && (
                <span className="local-row-warn">
                  Sharded download is incomplete — shard files are missing.
                  Finish the download in the source tool, or Remove and re-download.
                </span>
              )}
            </div>
            <div className="local-row-actions">
              {m.is_active ? (
                <button
                  className="settings-btn"
                  disabled={busy === `mlx:${m.slug}`}
                  onClick={() => onDeactivate(m.slug)}
                  title="Unregister from the model list (unloads it if resident)"
                >
                  Deactivate
                </button>
              ) : (
                <button
                  className="settings-btn"
                  disabled={busy === `mlx:${m.slug}` || !!m.incomplete}
                  onClick={() => onActivate(m.slug)}
                  title={
                    m.incomplete
                      ? "Shard files are missing (interrupted download) — finish the download in the source tool, then Remove and re-import"
                      : "Register for chat. Becomes the default model if none is set yet."
                  }
                >
                  {m.incomplete ? "Incomplete" : "Activate"}
                </button>
              )}
              <button
                className="settings-btn settings-btn--danger"
                disabled={busy === `mlx:${m.slug}`}
                onClick={() => onDeleteMlx(m)}
              >
                Remove
              </button>
            </div>
          </div>
        ))}

        {/* GGUF rows */}
        {ggufInstalled.filter((m) => !m.is_mmproj).map((m) => (
          <div key={m.filename} className={`local-row${m.is_running ? " local-row--active" : ""}`}>
            <div className="local-row-main">
              <span className="local-row-name">{m.filename}</span>
              <span className="local-row-meta">
                {fmtBytes(m.size_bytes)}
                {m.is_shard_group ? ` · ${m.parts?.length ?? "?"} parts` : ""}
                {m.is_running ? ` · running on :${m.port}` : " · stopped"}
              </span>
              {m.has_mamba_layers && !m.is_running && (
                <span className="local-row-warn">
                  Hybrid Mamba model — not supported by this local engine build.
                  Use a standard (non-UD) variant.
                </span>
              )}
              {m.is_shard_group && m.complete === false && (
                <span className="local-row-warn">
                  Incomplete sharded model — some parts are missing.
                  Re-download the full set from Hugging Face.
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
                  disabled={busy === m.filename || !!m.has_mamba_layers || m.complete === false}
                  onClick={() => onStart(m.filename)}
                  title={
                    m.has_mamba_layers
                      ? "Not compatible with this local engine"
                      : m.complete === false
                        ? "Some parts of this sharded model are missing"
                        : "Start and register for chat / extraction"
                  }
                >
                  Start
                </button>
              )}
              <button
                className="settings-btn settings-btn--danger"
                disabled={busy === m.filename || busy === "__stop_all__"}
                onClick={() => onDeleteGguf(m.filename, m.is_running, m.parts?.length ?? 1)}
              >
                Remove
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Downloads — both engines, one section */}
      {(() => {
        const ggufActive = ggufDownloads.filter(
          (t) => t.status === "downloading" || t.status === "pending" || cancellingGguf.has(t.task_id),
        );
        const mlxActive = mlxDownloads.filter(
          (t) => t.status === "downloading" || t.status === "pending" || cancellingMlx.has(t.task_id),
        );
        if (ggufActive.length === 0 && mlxActive.length === 0) return null;
        return (
          <div className="local-downloads">
            <div className="local-subhead">Downloading</div>
            {ggufActive.map((t) => {
              const isCancelling = cancellingGguf.has(t.task_id);
              const pct = t.total_bytes > 0 ? Math.min(100, (t.downloaded_bytes / t.total_bytes) * 100) : 0;
              return (
                <div key={`gguf:${t.task_id}`} className="local-dl">
                  <div className="local-dl-name">{t.filename}</div>
                  <div className="local-dl-bar">
                    <div className="local-dl-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="local-dl-meta">
                    {isCancelling
                      ? "Cancelling…"
                      : `${fmtBytes(t.downloaded_bytes)} / ${fmtBytes(t.total_bytes)} (${pct.toFixed(0)}%)`}
                    <button
                      type="button"
                      className="settings-btn settings-btn--danger local-dl-cancel"
                      disabled={isCancelling}
                      onClick={() => onCancelGgufDownload(t.task_id, t.filename)}
                    >
                      {isCancelling ? "Cancelling…" : "Cancel"}
                    </button>
                  </div>
                </div>
              );
            })}
            {mlxActive.map((t) => {
              const isCancelling = cancellingMlx.has(t.task_id);
              const pct = t.total_bytes > 0 ? Math.min(100, (t.downloaded_bytes / t.total_bytes) * 100) : 0;
              return (
                <div key={`mlx:${t.task_id}`} className="local-dl">
                  <div className="local-dl-name">{t.slug}</div>
                  <div className="local-dl-bar">
                    <div className="local-dl-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="local-dl-meta">
                    {isCancelling
                      ? "Cancelling…"
                      : `${fmtBytes(t.downloaded_bytes)} / ${fmtBytes(t.total_bytes)} (${pct.toFixed(0)}%)`}
                    <button
                      type="button"
                      className="settings-btn settings-btn--danger local-dl-cancel"
                      disabled={isCancelling}
                      onClick={() => onCancelMlxDownload(t.task_id, t.slug)}
                    >
                      {isCancelling ? "Cancelling…" : "Cancel"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        );
      })()}

      <div className="local-more">
        <button
          type="button"
          className="settings-btn local-more-btn"
          onClick={() => openSearch(mlxAvailable ? "mlx" : "gguf")}
        >
          Search models…
        </button>
        <span className="local-more-hint">
          {mlxAvailable ? "Recommended for this Mac" : "Browse Hugging Face"}
        </span>
        {mlxAvailable && (
          <button
            type="button"
            className="settings-btn local-more-btn"
            onClick={() => openSearch("gguf")}
            title="Browse the GGUF catalog — models that run on any machine"
          >
            More models (GGUF)…
          </button>
        )}
        {mlxAvailable && (
          <button
            type="button"
            className="settings-btn local-more-btn"
            onClick={onImportFolder}
            disabled={importing}
          >
            {importing ? "Importing…" : "Import folder…"}
          </button>
        )}
      </div>

      <SearchMlxModal
        open={searchOpen && searchSource === "mlx"}
        onClose={closeSearch}
        onDownloadStarted={refreshAll}
        onBrowseGguf={() => openSearch("gguf")}
      />
      <SearchModal
        open={searchOpen && searchSource === "gguf"}
        onClose={closeSearch}
        onDownloadStarted={refreshAll}
      />

      {confirmModal && <Modal {...confirmModal} />}
    </div>
  );
}
