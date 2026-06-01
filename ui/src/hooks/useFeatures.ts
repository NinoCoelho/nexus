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

const LS_FEATURES_KEY = "nexus-features-active";
const LS_MODE_KEY = "nexus-ui-mode";

function loadCachedFeatures(): Set<string> {
  try {
    const raw = localStorage.getItem(LS_FEATURES_KEY);
    if (raw) {
      const arr = JSON.parse(raw);
      if (Array.isArray(arr)) return new Set(arr);
    }
  } catch { /* ignore */ }
  return new Set();
}

function persistFeatures(features: Set<string>) {
  try {
    localStorage.setItem(LS_FEATURES_KEY, JSON.stringify([...features].sort()));
  } catch { /* ignore */ }
}

function loadCachedMode(): "normal" | "advanced" {
  try {
    const stored = localStorage.getItem(LS_MODE_KEY);
    if (stored === "normal" || stored === "advanced") return stored;
  } catch { /* ignore */ }
  return "normal";
}

function persistMode(mode: "normal" | "advanced") {
  try {
    localStorage.setItem(LS_MODE_KEY, mode);
  } catch { /* ignore */ }
}

export function useFeatures(revision?: number) {
  const [active, setActive] = useState<Set<string>>(loadCachedFeatures);
  const [mode, setMode] = useState<"normal" | "advanced">(loadCachedMode);
  const [allFeatures, setAllFeatures] = useState<string[]>([]);
  const featuresRef = useRef(active);
  featuresRef.current = active;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [cfg, settings] = await Promise.all([getConfig(), getHitlSettings()]);
        if (cancelled) return;
        const fresh = new Set(cfg.features?.active ?? []);
        setActive(fresh);
        persistFeatures(fresh);
        setAllFeatures(cfg.features?.all ?? []);
        const freshMode = settings.ui_mode === "advanced" ? "advanced" : "normal";
        setMode(freshMode);
        persistMode(freshMode);
      } catch { /* will use cached values */ }
    })();
    return () => { cancelled = true; };
  }, [revision]);

  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sid, event) => {
      if (event.kind === "features_changed" && event.data?.to) {
        const fresh = new Set(event.data.to);
        setActive(fresh);
        persistFeatures(fresh);
      } else if (event.kind === "nexus_tier_changed") {
        void getConfig().then((cfg) => {
          if (cfg.features?.active) {
            const fresh = new Set(cfg.features.active);
            setActive(fresh);
            persistFeatures(fresh);
            if (cfg.features.all) setAllFeatures(cfg.features.all);
          }
        }).catch(() => {});
      } else if (event.kind === "settings_changed" && event.data) {
        if (event.data.ui_mode === "advanced" || event.data.ui_mode === "normal") {
          setMode(event.data.ui_mode);
          persistMode(event.data.ui_mode);
        }
      }
    });
    return () => sub.close();
  }, []);

  const toggleMode = useCallback(async () => {
    const next = mode === "normal" ? "advanced" : "normal";
    setMode(next);
    persistMode(next);
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
