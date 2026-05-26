import { useState, useEffect, useCallback, useRef } from "react";
import { getConfig } from "../api/config";
import { getHitlSettings, setHitlSettings } from "../api/settings";
import { subscribeGlobalNotifications } from "../api/chat";

const FEATURE_VIEW_MAP: Record<string, string> = {
  kanban: "kanban",
  calendar: "calendar",
  workflow: "workflows",
  knowledge: "graph",
  dream: "dream",
  heartbeat: "heartbeat",
  database: "data",
  projects: "projects",
};

const NORMAL_HIDDEN = new Set(["graph", "heartbeat", "dream"]);

export function useFeatures(revision?: number) {
  const [active, setActive] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"normal" | "advanced">("normal");
  const [allFeatures, setAllFeatures] = useState<string[]>([]);
  const featuresRef = useRef(active);
  featuresRef.current = active;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [cfg, settings] = await Promise.all([getConfig(), getHitlSettings()]);
        if (cancelled) return;
        setActive(new Set(cfg.features?.active ?? []));
        setAllFeatures(cfg.features?.all ?? []);
        setMode(settings.ui_mode === "advanced" ? "advanced" : "normal");
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [revision]);

  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sid, event) => {
      if (event.kind === "features_changed" && event.data?.to) {
        setActive(new Set(event.data.to));
      } else if (event.kind === "nexus_tier_changed") {
        void getConfig().then((cfg) => {
          if (cfg.features?.active) {
            setActive(new Set(cfg.features.active));
            if (cfg.features.all) setAllFeatures(cfg.features.all);
          }
        }).catch(() => {});
      }
    });
    return () => sub.close();
  }, []);

  const toggleMode = useCallback(async () => {
    const next = mode === "normal" ? "advanced" : "normal";
    setMode(next);
    await setHitlSettings({ ui_mode: next });
  }, [mode]);

  const isViewVisible = useCallback(
    (viewId: string) => {
      const requiredFeature = Object.entries(FEATURE_VIEW_MAP).find(
        ([, v]) => v === viewId,
      )?.[0];
      if (requiredFeature && !featuresRef.current.has(requiredFeature)) return false;
      if (mode === "normal" && NORMAL_HIDDEN.has(viewId)) return false;
      return true;
    },
    [mode],
  );

  const hasFeature = useCallback(
    (feature: string) => featuresRef.current.has(feature),
    [],
  );

  return { active, allFeatures, mode, toggleMode, isViewVisible, hasFeature };
}
