import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeName = "mermaidcore" | "neutrals" | "neon" | "biophilic";

interface ThemeContextValue {
  theme: ThemeName;
  brightness: number;
  setTheme: (t: ThemeName) => void;
  setBrightness: (b: number) => void;
}

const LS_THEME = "nexus-theme";
const LS_BRIGHTNESS = "nexus-brightness";

const DEFAULT_THEME: ThemeName = "neutrals";
const DEFAULT_BRIGHTNESS = 0;

const VALID_THEMES: ThemeName[] = ["mermaidcore", "neutrals", "neon", "biophilic"];

function readThemeLS(): ThemeName {
  try {
    const raw = localStorage.getItem(LS_THEME);
    if (raw && (VALID_THEMES as string[]).includes(raw)) return raw as ThemeName;
  } catch {}
  return DEFAULT_THEME;
}

function readBrightnessLS(): number {
  try {
    const raw = localStorage.getItem(LS_BRIGHTNESS);
    if (raw != null) {
      const n = parseFloat(raw);
      if (!isNaN(n) && n >= 0 && n <= 1) return n;
    }
  } catch {}
  return DEFAULT_BRIGHTNESS;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>(readThemeLS);
  const [brightness, setBrightnessState] = useState<number>(readBrightnessLS);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.style.setProperty("--brightness", String(brightness));
  }, [brightness]);

  const setTheme = useCallback((t: ThemeName) => {
    const el = document.documentElement;
    el.classList.add("theme-transitioning");
    setThemeState(t);
    try { localStorage.setItem(LS_THEME, t); } catch {}
    setTimeout(() => el.classList.remove("theme-transitioning"), 220);
  }, []);

  const setBrightness = useCallback((b: number) => {
    const clamped = Math.max(0, Math.min(1, Math.round(b * 100) / 100));
    setBrightnessState(clamped);
    try { localStorage.setItem(LS_BRIGHTNESS, String(clamped)); } catch {}
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, brightness, setTheme, setBrightness }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
