import { useState, useEffect, useCallback } from "react";
import { getHitlSettings, setHitlSettings } from "../api/settings";
import { subscribeGlobalNotifications } from "../api/chat";

const NORMAL_HIDDEN = new Set(["graph", "heartbeat", "dream"]);

const LS_MODE_KEY = "nexus-ui-mode";

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
  const [mode, setMode] = useState<"normal" | "advanced">(loadCachedMode);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const settings = await getHitlSettings();
        if (cancelled) return;
        const freshMode = settings.ui_mode === "advanced" ? "advanced" : "normal";
        setMode(freshMode);
        persistMode(freshMode);
      } catch { /* will use cached values */ }
    })();
    return () => { cancelled = true; };
  }, [revision]);

  useEffect(() => {
    const sub = subscribeGlobalNotifications((_sid, event) => {
      if (event.kind === "settings_changed" && event.data) {
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
      if (mode === "normal" && NORMAL_HIDDEN.has(viewId)) return false;
      return true;
    },
    [mode],
  );

  return { mode, toggleMode, isViewVisible };
}
