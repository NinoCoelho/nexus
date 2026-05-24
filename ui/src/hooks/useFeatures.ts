import { useState, useEffect, useCallback } from "react";
import { getConfig } from "../api/config";
import { getHitlSettings, setHitlSettings } from "../api/settings";

const FEATURE_VIEW_MAP: Record<string, string> = {
  kanban: "kanban",
  calendar: "calendar",
  workflow: "workflows",
  knowledge: "graph",
  dream: "dream",
  heartbeat: "heartbeat",
  database: "data",
};

const NORMAL_HIDDEN = new Set(["graph", "heartbeat", "dream"]);

export function useFeatures(revision?: number) {
  const [active, setActive] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"normal" | "advanced">("normal");
  const [allFeatures, setAllFeatures] = useState<string[]>([]);

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
      if (requiredFeature && !active.has(requiredFeature)) return false;
      if (mode === "normal" && NORMAL_HIDDEN.has(viewId)) return false;
      return true;
    },
    [active, mode],
  );

  const hasFeature = useCallback(
    (feature: string) => active.has(feature),
    [active],
  );

  return { active, allFeatures, mode, toggleMode, isViewVisible, hasFeature };
}
