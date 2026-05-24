/**
 * i18next initialization for Nexus.
 *
 * Catalogs are bundled directly (small enough that lazy loading per locale
 * is not worth a network round-trip on this local-first single-user app).
 * The active language is sourced from ~/.nexus/config.toml `[ui] language`,
 * fetched via /config in App.tsx and applied with `i18n.changeLanguage()`.
 * Browser-detected locale is the first-load fallback when /config hasn't
 * resolved yet.
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import enCommon from "./locales/en/common.json";
import enSettings from "./locales/en/settings.json";
import enSidebar from "./locales/en/sidebar.json";
import enChat from "./locales/en/chat.json";
import enVault from "./locales/en/vault.json";
import enForms from "./locales/en/forms.json";
import enTunnel from "./locales/en/tunnel.json";
import enModels from "./locales/en/models.json";
import enProviders from "./locales/en/providers.json";
import enKanban from "./locales/en/kanban.json";
import enCalendar from "./locales/en/calendar.json";
import enGraph from "./locales/en/graph.json";
import enDatatable from "./locales/en/datatable.json";
import enNotifications from "./locales/en/notifications.json";
import enSkillWizard from "./locales/en/skillWizard.json";
import ptCommon from "./locales/pt-BR/common.json";
import ptSettings from "./locales/pt-BR/settings.json";
import ptSidebar from "./locales/pt-BR/sidebar.json";
import ptChat from "./locales/pt-BR/chat.json";
import ptVault from "./locales/pt-BR/vault.json";
import ptForms from "./locales/pt-BR/forms.json";
import ptTunnel from "./locales/pt-BR/tunnel.json";
import ptModels from "./locales/pt-BR/models.json";
import ptProviders from "./locales/pt-BR/providers.json";
import ptKanban from "./locales/pt-BR/kanban.json";
import ptCalendar from "./locales/pt-BR/calendar.json";
import ptGraph from "./locales/pt-BR/graph.json";
import ptDatatable from "./locales/pt-BR/datatable.json";
import ptNotifications from "./locales/pt-BR/notifications.json";
import ptSkillWizard from "./locales/pt-BR/skillWizard.json";

export const SUPPORTED_LANGUAGES = ["en", "pt-BR"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: {
        common: enCommon,
        settings: enSettings,
        sidebar: enSidebar,
        chat: enChat,
        vault: enVault,
        forms: enForms,
        tunnel: enTunnel,
        models: enModels,
        providers: enProviders,
        kanban: enKanban,
        calendar: enCalendar,
        graph: enGraph,
        datatable: enDatatable,
        notifications: enNotifications,
        skillWizard: enSkillWizard,
      },
      "pt-BR": {
        common: ptCommon,
        settings: ptSettings,
        sidebar: ptSidebar,
        chat: ptChat,
        vault: ptVault,
        forms: ptForms,
        tunnel: ptTunnel,
        models: ptModels,
        providers: ptProviders,
        kanban: ptKanban,
        calendar: ptCalendar,
        graph: ptGraph,
        datatable: ptDatatable,
        notifications: ptNotifications,
        skillWizard: ptSkillWizard,
      },
    },
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGUAGES as readonly string[] as string[],
    nonExplicitSupportedLngs: true, // "pt" → "pt-BR"
    ns: [
      "common", "settings", "sidebar",
      "chat", "vault", "forms", "tunnel",
      "models", "providers", "kanban", "calendar",
      "graph", "datatable", "notifications",
      "skillWizard",
    ],
    defaultNS: "common",
    interpolation: { escapeValue: false }, // React already escapes
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "nexus-language",
      caches: ["localStorage"],
    },
  });

export default i18n;

/** Coerce an arbitrary string to a supported language, defaulting to "en". */
export function normalizeLanguage(lang: string | null | undefined): SupportedLanguage {
  if (!lang) return "en";
  if ((SUPPORTED_LANGUAGES as readonly string[]).includes(lang)) return lang as SupportedLanguage;
  const primary = lang.split("-", 1)[0]?.toLowerCase() ?? "";
  for (const cand of SUPPORTED_LANGUAGES) {
    if (cand.split("-", 1)[0]?.toLowerCase() === primary) return cand;
  }
  return "en";
}
