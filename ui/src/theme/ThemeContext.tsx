import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeName = "mermaidcore" | "neutrals" | "neon" | "biophilic";

interface ThemeContextValue {
  theme: ThemeName;
  darkMode: boolean;
  setTheme: (t: ThemeName) => void;
  toggleDarkMode: () => void;
}

const LS_THEME = "nexus-theme";
const LS_DARK = "nexus-dark-mode";

const DEFAULT_THEME: ThemeName = "neutrals";

const VALID_THEMES: ThemeName[] = ["mermaidcore", "neutrals", "neon", "biophilic"];

function readThemeLS(): ThemeName {
  try {
    const raw = localStorage.getItem(LS_THEME);
    if (raw && (VALID_THEMES as string[]).includes(raw)) return raw as ThemeName;
  } catch {}
  return DEFAULT_THEME;
}

function readDarkLS(): boolean {
  try {
    const raw = localStorage.getItem(LS_DARK);
    if (raw != null) return raw === "true";
  } catch {}
  try {
    const old = localStorage.getItem("nexus-brightness");
    if (old != null) {
      const n = parseFloat(old);
      if (!isNaN(n)) return n < 0.5;
    }
  } catch {}
  return true;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>(readThemeLS);
  const [darkMode, setDarkModeState] = useState<boolean>(readDarkLS);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.style.setProperty("--brightness", darkMode ? "0" : "1");
  }, [darkMode]);

  const setTheme = useCallback((t: ThemeName) => {
    const el = document.documentElement;
    el.classList.add("theme-transitioning");
    setThemeState(t);
    try { localStorage.setItem(LS_THEME, t); } catch {}
    setTimeout(() => el.classList.remove("theme-transitioning"), 220);
  }, []);

  const toggleDarkMode = useCallback(() => {
    setDarkModeState((prev) => {
      const next = !prev;
      const el = document.documentElement;
      el.classList.add("theme-transitioning");
      try { localStorage.setItem(LS_DARK, String(next)); } catch {}
      setTimeout(() => el.classList.remove("theme-transitioning"), 220);
      return next;
    });
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, darkMode, setTheme, toggleDarkMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
