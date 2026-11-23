/**
 * MlxModels — in-process MLX models on Apple Silicon.
 *
 * Mirrors LocalModels (llama.cpp) but the lifecycle is activate/unload rather
 * than start/stop: activating registers a provider in config and the model
 * becomes resident on first use; unloading (manual or idle-timeout) frees
 * unified memory without unregistering anything.
 *
 * "Import folder…" registers already-downloaded snapshots in place (symlinks,
 * e.g. an LM Studio library) so nothing is ever re-downloaded.
 */
import { useCallback, useEffect, useState } from "react";
import {
  activateMlx,
  cancelMlxDownload,
  deactivateMlx,
  deleteMlxInstalled,
  fmtBytes,
  getHardware,
  getMlxStatus,
  importMlxFolder,
  listMlxDownloads,
  listMlxInstalled,
  unloadMlx,
  type HardwareProbe,
  type MlxDownloadTask,
  type MlxInstalled,
  type MlxStatus,
} from "../../api";
import { useToast } from "../../toast/ToastProvider";
import Modal, { type ModalProps } from "../Modal";
import SearchMlxModal from "./SearchMlxModal";
import "../LocalModels/LocalModels.css";

interface Props {
  onRefresh: () => void;
}

const LAST_IMPORT_PATH_KEY = "nexus.mlx.lastImportPath";

export default function MlxModels({ onRefresh }: Props) {
  const toast = useToast();
  const [hw, setHw] = useState<HardwareProbe | null>(null);
  const [status, setStatus] = useState<MlxStatus | null>(null);
  const [installed, setInstalled] = useState<MlxInstalled[]>([]);
  const [downloads, setDownloads] = useState<MlxDownloadTask[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [confirmModal, setConfirmModal] = useState<ModalProps | null>(null);
  const [importing, setImporting] = useState(false);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());

  const refreshMlx = useCallback(async () => {
    try {
      const [inst, dls, st] = await Promise.all([
        listMlxInstalled(),
        listMlxDownloads(),
        getMlxStatus(),
      ]);
      setInstalled(inst);
      setDownloads(dls);
      setStatus(st);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load MLX models");
    }
  }, []);

  useEffect(() => {
    getHardware().then(setHw).catch(() => {});
    refreshMlx();
  }, [refreshMlx]);

  // Poll downloads while any task is in flight.
  useEffect(() => {
    const inFlight = downloads.some((t) => t.status === "downloading" || t.status === "pending");
    if (!inFlight) return;
    const id = setInterval(refreshMlx, 1000);
    return () => clearInterval(id);
  }, [downloads, refreshMlx]);

  const onActivate = useCallback(
    async (slug: string, force = false) => {
      setBusy(slug);
      try {
        const res = await activateMlx(slug, { setDefault: null, force });
        toast.success(`Activated ${res.model_id}`);
        await refreshMlx();
        onRefresh();
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Activate failed";
        // 409 = won't fit in unified memory — offer to override, the same
        // way the route's `force` flag intends.
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
    [refreshMlx, onRefresh, toast],
  );

  const onDeactivate = useCallback(
    async (slug: string) => {
      setBusy(slug);
      try {
        await deactivateMlx(slug);
        toast.success(`Deactivated ${slug}`);
        await refreshMlx();
        onRefresh();
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Deactivate failed");
      } finally {
        setBusy(null);
      }
    },
    [refreshMlx, onRefresh, toast],
  );

  const onUnload = useCallback(async () => {
    setBusy("__unload__");
    try {
      await unloadMlx();
      toast.success("Model unloaded — memory freed");
      await refreshMlx();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Unload failed");
    } finally {
      setBusy(null);
    }
  }, [refreshMlx, toast]);

  const onDelete = useCallback((m: MlxInstalled) => {
    const isLink = m.local_dir.includes("/.nexus/models/") === false;
    setConfirmModal({
      kind: "confirm",
      title: "Remove model",
      message: isLink
        ? `Remove ${m.slug}? Only the link is removed — the original files outside Nexus stay untouched.`
        : `Remove ${m.slug}? The snapshot will be deleted from disk (${m.total_gb} GB).`,
      confirmLabel: "Remove",
      danger: true,
      onCancel: () => setConfirmModal(null),
      onSubmit: async () => {
        setConfirmModal(null);
        setBusy(m.slug);
        try {
          await deleteMlxInstalled(m.slug);
          toast.success(`Removed ${m.slug}`);
          refreshMlx();
          onRefresh();
        } catch (e) {
          toast.error(e instanceof Error ? e.message : "Delete failed");
        } finally {
          setBusy(null);
        }
      },
    });
  }, [refreshMlx, onRefresh, toast]);

  const onCancelDownload = useCallback(
    async (taskId: string, slug: string) => {
      setCancelling((prev) => new Set(prev).add(taskId));
      try {
        await cancelMlxDownload(taskId);
        toast.info(`Cancelling ${slug}…`);
      } catch {
        setCancelling((prev) => {
          const next = new Set(prev);
          next.delete(taskId);
          return next;
        });
        toast.error("Cancel failed");
        refreshMlx();
      }
    },
    [refreshMlx, toast],
  );

  useEffect(() => {
    setCancelling((prev) => {
      const live = new Set(downloads.map((t) => t.task_id));
      const next = new Set([...prev].filter((id) => live.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [downloads]);

  const onImportFolder = useCallback(() => {
    const last = localStorage.getItem(LAST_IMPORT_PATH_KEY) ?? "~/.lmstudio/models";
    setConfirmModal({
      kind: "prompt",
      title: "Import models in place",
      message:
        "Scan a folder for MLX models you already downloaded (e.g. an LM Studio library) and register them without re-downloading. Nexus links to the files — it never copies or moves them.",
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
          if (!n && !a && !s) toast.info("No MLX models found in that folder");
          await refreshMlx();
          onRefresh();
        } catch (e) {
          toast.error(e instanceof Error ? e.message : "Import failed");
        } finally {
          setImporting(false);
        }
      },
    });
  }, [refreshMlx, onRefresh, toast]);

  const loadedSlug = status?.loaded && status.model_path
    ? (status.model_path.split("/").pop() ?? "")
    : "";

  return (
    <div className="local-models-section">
      <p className="local-intro">
        MLX models run fully in-process on Apple Silicon — one resident model,
        shared prefix cache, no server process. Already downloaded models
        elsewhere on disk can be imported without re-downloading.
      </p>

      {hw && hw.is_apple_silicon && (
        <div className="local-hw-card">
          <div className="local-hw-row">
            <span className="local-hw-chip">{hw.chip}</span>
            <span className="local-hw-stat">{hw.ram_gb.toFixed(1)} GB unified memory</span>
          </div>
        </div>
      )}

      {status?.loaded && (
        <div className="local-update-banner local-update-banner--active">
          <span className="local-update-text">
            Loaded: {loadedSlug}
            {status.resident_seconds != null && status.resident_seconds > 0 && (
              <> · resident for {Math.round(status.resident_seconds / 60)} min</>
            )}
            {status.queue_depth ? ` · ${status.queue_depth} queued` : ""}
          </span>
          <button
            type="button"
            className="settings-btn"
            disabled={busy === "__unload__"}
            onClick={onUnload}
            title="Free unified memory now (the model reloads automatically on next use)"
          >
            {busy === "__unload__" ? "Unloading…" : "Unload"}
          </button>
        </div>
      )}

      {error && <p className="settings-error">{error}</p>}

      <div className="local-installed">
        <div className="local-subhead">Installed</div>
        {installed.length === 0 && (
          <p className="local-empty">
            No MLX models yet — search Hugging Face below, or import a folder
            of models you already downloaded.
          </p>
        )}
        {installed.map((m) => (
          <div key={m.slug} className={`local-row${m.is_active ? " local-row--active" : ""}`}>
            <div className="local-row-main">
              <span className="local-row-name">{m.slug}</span>
              <span className="local-row-meta">
                {m.total_gb > 0 ? `${m.total_gb} GB` : fmtBytes(m.total_bytes)}
                {m.quantization_bits > 0 && ` · ${m.quantization_bits}-bit`}
                {m.context_window > 0 && ` · ${m.context_window.toLocaleString()} ctx`}
                {m.is_vlm && " · vision"}
                {m.is_active ? " · active" : " · not registered"}
              </span>
              <span className={`mlx-badge${m.supports_tools ? "" : " mlx-badge--warn"}`}>
                {m.supports_tools ? "tools" : "no tools"}
              </span>
            </div>
            <div className="local-row-actions">
              {m.is_active ? (
                <button
                  className="settings-btn"
                  disabled={busy === m.slug}
                  onClick={() => onDeactivate(m.slug)}
                  title="Unregister from the model list (unloads it if resident)"
                >
                  Deactivate
                </button>
              ) : (
                <button
                  className="settings-btn"
                  disabled={busy === m.slug}
                  onClick={() => onActivate(m.slug)}
                  title="Register for chat. Becomes the default model if none is set yet."
                >
                  Activate
                </button>
              )}
              <button
                className="settings-btn settings-btn--danger"
                disabled={busy === m.slug}
                onClick={() => onDelete(m)}
              >
                Remove
              </button>
            </div>
          </div>
        ))}
      </div>

      {(() => {
        const active = downloads.filter(
          (t) => t.status === "downloading" || t.status === "pending" || cancelling.has(t.task_id),
        );
        if (active.length === 0) return null;
        return (
          <div className="local-downloads">
            <div className="local-subhead">Downloading</div>
            {active.map((t) => {
              const isCancelling = cancelling.has(t.task_id);
              const pct = t.total_bytes > 0
                ? Math.min(100, (t.downloaded_bytes / t.total_bytes) * 100)
                : 0;
              return (
                <div key={t.task_id} className="local-dl">
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
                      onClick={() => onCancelDownload(t.task_id, t.slug)}
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
        <button type="button" className="settings-btn local-more-btn" onClick={onImportFolder}>
          {importing ? "Importing…" : "Import folder…"}
        </button>
        <span className="local-more-hint">Register models you already downloaded (e.g. LM Studio)</span>
      </div>

      <div className="local-more">
        <button type="button" className="settings-btn local-more-btn" onClick={() => setSearchOpen(true)}>
          Search models…
        </button>
        <span className="local-more-hint">Browse mlx-community on Hugging Face</span>
      </div>

      <SearchMlxModal
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onDownloadStarted={refreshMlx}
      />

      {confirmModal && <Modal {...confirmModal} />}
    </div>
  );
}
