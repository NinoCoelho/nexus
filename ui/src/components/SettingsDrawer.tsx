import { useCallback, useEffect, useState } from "react";
import {
  getHitlSettings,
  getModels,
  getProviders,
  getRouting,
  setHitlSettings,
  type HitlSettings,
  type Model,
  type Provider,
  type RoutingConfig,
} from "../api";
import ProvidersSection from "./ProvidersSection";
import ModelsSection from "./ModelsSection";
import RoutingSection from "./RoutingSection";
import "./SettingsDrawer.css";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function SettingsDrawer({ open, onClose }: Props) {
  const [routing, setRouting] = useState<RoutingConfig | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [hitl, setHitl] = useState<HitlSettings | null>(null);
  const [hitlSaving, setHitlSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [r, p, m, h] = await Promise.all([
        getRouting(),
        getProviders(),
        getModels(),
        // HITL settings may 404 on an older backend — guard so the
        // rest of the drawer still loads. The catch returns a
        // default-off snapshot so the toggle stays visible.
        getHitlSettings().catch(() => ({ yolo_mode: false })),
      ]);
      setRouting(r);
      setProviders(p);
      setModels(m);
      setHitl(h);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleYolo = useCallback(async () => {
    if (!hitl) return;
    setHitlSaving(true);
    const next = !hitl.yolo_mode;
    // Optimistic update — revert on error so the user isn't left
    // staring at a toggle that doesn't match the backend.
    setHitl({ ...hitl, yolo_mode: next });
    try {
      const updated = await setHitlSettings({ yolo_mode: next });
      setHitl(updated);
    } catch (e) {
      setHitl({ ...hitl, yolo_mode: !next });
      setError(e instanceof Error ? e.message : "Failed to update YOLO mode");
    } finally {
      setHitlSaving(false);
    }
  }, [hitl]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="settings-drawer">
        <div className="drawer-header">
          <span className="drawer-title">Settings</span>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="drawer-body settings-drawer-body">
          {loading && !routing && (
            <p className="settings-loading">Loading…</p>
          )}
          {error && <p className="settings-error">{error}</p>}
          {routing && (
            <RoutingSection routing={routing} models={models} onRefresh={refresh} />
          )}
          <ProvidersSection providers={providers} onRefresh={refresh} />
          <ModelsSection models={models} providers={providers} onRefresh={refresh} />
          {hitl && (
            <section className="hitl-section">
              <h3 className="hitl-section-title">Human-in-the-loop</h3>
              <div className="hitl-row">
                <div className="hitl-row-text">
                  <label className="hitl-row-label">YOLO mode</label>
                  <p className="hitl-row-desc">
                    Auto-approve confirm-style prompts without showing the dialog.
                    Does not affect choice or text prompts.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={hitl.yolo_mode}
                  className={`hitl-switch ${hitl.yolo_mode ? "on" : "off"}`}
                  disabled={hitlSaving}
                  onClick={toggleYolo}
                >
                  <span className="hitl-switch-knob" />
                </button>
              </div>
            </section>
          )}
        </div>
      </div>
    </>
  );
}
