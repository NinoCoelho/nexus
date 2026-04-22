import { useTheme, type ThemeName } from "../theme/ThemeContext";
import "./AppearanceSection.css";

const THEMES: { id: ThemeName; label: string; desc: string; colors: [string, string, string] }[] = [
  {
    id: "mermaidcore",
    label: "Mermaidcore",
    desc: "Iridescent aquas & teals",
    colors: ["#9DF9EF", "#8458B3", "#152130"],
  },
  {
    id: "neutrals",
    label: "Elevated Neutrals",
    desc: "Warm sands & stone",
    colors: ["#DCC7AA", "#F8F5F2", "#1e1c1a"],
  },
  {
    id: "neon",
    label: "Neon Minimal",
    desc: "OLED black + electric lime",
    colors: ["#B6FF3B", "#000000", "#1a1a1a"],
  },
  {
    id: "biophilic",
    label: "Biophilic",
    desc: "Forest green & clay",
    colors: ["#C89666", "#577267", "#1a1e1b"],
  },
];

export default function AppearanceSection() {
  const { theme, brightness, setTheme, setBrightness } = useTheme();

  return (
    <section className="appearance-section">
      <h3 className="appearance-title">Appearance</h3>

      <div className="appearance-theme-grid">
        {THEMES.map((t) => (
          <button
            key={t.id}
            className={`appearance-theme-card ${theme === t.id ? "active" : ""}`}
            onClick={() => setTheme(t.id)}
            aria-label={t.label}
          >
            <div className="appearance-swatch">
              <span className="appearance-swatch-dot" style={{ background: t.colors[0] }} />
              <span className="appearance-swatch-dot" style={{ background: t.colors[1] }} />
              <span
                className="appearance-swatch-bg"
                style={{ background: t.colors[2] }}
              />
            </div>
            <span className="appearance-theme-label">{t.label}</span>
            <span className="appearance-theme-desc">{t.desc}</span>
          </button>
        ))}
      </div>

      <div className="appearance-brightness-row">
        <svg className="appearance-brightness-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M6 0a1 1 0 0 0-.89.55 8 8 0 1 0 10.34 10.34A1 1 0 0 0 14.56 9A6.5 6.5 0 0 1 6 0z"/>
        </svg>
        <input
          type="range"
          className="appearance-brightness-slider"
          min="0"
          max="1"
          step="0.01"
          value={brightness}
          onChange={(e) => setBrightness(parseFloat(e.target.value))}
          aria-label="Brightness"
        />
        <svg className="appearance-brightness-icon" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM8 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0V.75A.75.75 0 0 1 8 0zm0 13a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 8 13zM2.34 2.34a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 0 1-1.06 1.06L2.34 3.4a.75.75 0 0 1 0-1.06zm9.2 9.2a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 1 1-1.06 1.06l-1.06-1.06a.75.75 0 0 1 0-1.06zM0 8a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5H.75A.75.75 0 0 1 0 8zm13 0a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5h-1.5A.75.75 0 0 1 13 8zM2.34 13.66a.75.75 0 0 1 0-1.06l1.06-1.06a.75.75 0 1 1 1.06 1.06L3.4 13.66a.75.75 0 0 1-1.06 0zm9.2-9.2a.75.75 0 0 1 0-1.06l1.06-1.06a.75.75 0 1 1 1.06 1.06l-1.06 1.06a.75.75 0 0 1-1.06 0z"/>
        </svg>
      </div>
    </section>
  );
}
