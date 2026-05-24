import { useCallback, useState } from "react";

export type UIMode = "normal" | "advanced";

const STORAGE_KEY = "nexus-ui-mode";

function loadMode(): UIMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "normal" || stored === "advanced") return stored;
  } catch { /* ignore */ }
  return "normal";
}

export function useUIMode(): { mode: UIMode; setMode: (m: UIMode) => void } {
  const [mode, setModeState] = useState<UIMode>(loadMode);

  const setMode = useCallback((m: UIMode) => {
    try { localStorage.setItem(STORAGE_KEY, m); } catch { /* ignore */ }
    setModeState(m);
  }, []);

  return { mode, setMode };
}
