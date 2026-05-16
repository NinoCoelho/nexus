import { useTranslation } from "react-i18next";
import { useTheme, type ThemeName } from "../theme/ThemeContext";
import { patchConfig } from "../api";
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from "../i18n";
import { useToast } from "../toast/ToastProvider";
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
  const { theme, setTheme } = useTheme();
  const { t, i18n } = useTranslation(["settings", "common"]);
  const toast = useToast();
  const currentLang = (SUPPORTED_LANGUAGES as readonly string[]).includes(i18n.language)
    ? (i18n.language as SupportedLanguage)
    : "en";

  async function changeLanguage(next: SupportedLanguage) {
    if (next === currentLang) return;
    try {
      await patchConfig({ ui: { language: next } });
      (window as any).__nexusLanguage = next;
      try { localStorage.setItem("nexus-language", next); } catch { /* private mode */ }
      await i18n.changeLanguage(next);
      toast.success(t("common:toast.saved"));
    } catch (e) {
      toast.error(t("common:toast.savingFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  return (
    <div className="appearance-section">
      <div className="appearance-language-row" role="radiogroup" aria-label={t("settings:appearance.language")}>
        <span className="appearance-language-label">{t("settings:appearance.language")}</span>
        <div className="appearance-language-options">
          {SUPPORTED_LANGUAGES.map((lng) => (
            <button
              key={lng}
              type="button"
              role="radio"
              aria-checked={currentLang === lng}
              className={`appearance-language-btn ${currentLang === lng ? "active" : ""}`}
              onClick={() => void changeLanguage(lng)}
            >
              {t(`settings:languages.${lng}`)}
            </button>
          ))}
        </div>
        <span className="appearance-language-hint">{t("settings:appearance.languageHint")}</span>
      </div>

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
    </div>
  );
}
