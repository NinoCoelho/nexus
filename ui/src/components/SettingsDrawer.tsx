import { useCallback, useEffect, useState } from "react";
import {
  getModels,
  getProviders,
  getRouting,
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [r, p, m] = await Promise.all([getRouting(), getProviders(), getModels()]);
      setRouting(r);
      setProviders(p);
      setModels(m);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }, []);

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
        </div>
      </div>
    </>
  );
}
