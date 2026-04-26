/**
 * SettingsDrawer — slide-out drawer for all Nexus configuration.
 *
 * Layout:
 *   - Header (title + close).
 *   - DefaultModelStrip — pinned, always visible. Lets users change the default
 *     model from anywhere without hunting for it.
 *   - SettingsTabs — Início / Modelos / Recursos / Avançado.
 *   - Active tab body.
 *
 * State (routing/providers/models/hitl/graphStats) lives here and is fetched
 * once on open, then passed down. Tabs are mounted/unmounted by switching but
 * keep their internal form state where it matters (ModelsSection's editingId).
 */

import { useCallback, useEffect, useState } from "react";
import {
  getHitlSettings,
  getKnowledgeStats,
  getModels,
  getProviders,
  getRouting,
  type HitlSettings,
  type KnowledgeStats,
  type Model,
  type Provider,
  type RoutingConfig,
} from "../api";
import AdvancedTab from "./settings/AdvancedTab";
import DefaultModelStrip from "./settings/DefaultModelStrip";
import FeaturesTab from "./settings/FeaturesTab";
import ModelsTab from "./settings/ModelsTab";
import QuickStartTab from "./settings/QuickStartTab";
import SettingsTabs from "./settings/SettingsTabs";
import "./SettingsDrawer.css";
import "./settings/settings.css";

interface Props {
  open: boolean;
  onClose: () => void;
}

type TabId = "quick" | "models" | "features" | "advanced";

const TABS: { id: TabId; label: string }[] = [
  { id: "quick", label: "Início" },
  { id: "models", label: "Modelos" },
  { id: "features", label: "Recursos" },
  { id: "advanced", label: "Avançado" },
];

export default function SettingsDrawer({ open, onClose }: Props) {
  const [routing, setRouting] = useState<RoutingConfig | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [hitl, setHitl] = useState<HitlSettings | null>(null);
  const [graphStats, setGraphStats] = useState<KnowledgeStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<TabId>("quick");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [r, p, m, h, ks] = await Promise.all([
        getRouting(),
        getProviders(),
        getModels(),
        getHitlSettings().catch(() => ({ yolo_mode: false })),
        getKnowledgeStats().catch(() => null),
      ]);
      setRouting(r);
      setProviders(p);
      setModels(m);
      setHitl(h);
      setGraphStats(ks);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Falha ao carregar configurações");
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
          <span className="drawer-title">Configurações</span>
          <button className="drawer-close" onClick={onClose} aria-label="Fechar">
            ✕
          </button>
        </div>

        <div className="settings-drawer-pinned">
          <DefaultModelStrip
            routing={routing}
            models={models}
            onChanged={refresh}
          />
          <SettingsTabs
            tabs={TABS}
            active={active}
            onChange={(id) => setActive(id as TabId)}
          />
        </div>

        <div className="drawer-body settings-drawer-body">
          {loading && !routing && <p className="settings-loading">Carregando…</p>}
          {error && <p className="settings-error">{error}</p>}

          {active === "quick" && (
            <QuickStartTab
              routing={routing}
              models={models}
              providers={providers}
              onChanged={refresh}
            />
          )}
          {active === "models" && (
            <ModelsTab
              routing={routing}
              providers={providers}
              models={models}
              onRefresh={refresh}
            />
          )}
          {active === "features" && <FeaturesTab graphStats={graphStats} />}
          {active === "advanced" && (
            <AdvancedTab hitl={hitl} onHitlChanged={setHitl} />
          )}
        </div>
      </div>
    </>
  );
}
