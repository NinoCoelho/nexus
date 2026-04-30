import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { putRouting, type Model, type Provider, type RoutingConfig } from "../../api";
import { useToast } from "../../toast/ToastProvider";
import AppearanceSection from "../AppearanceSection";
import SettingsSection from "./SettingsSection";

interface Props {
  routing: RoutingConfig | null;
  models: Model[];
  providers: Provider[];
  onChanged: () => void;
}

export default function QuickStartTab({ routing, models, providers, onChanged }: Props) {
  const toast = useToast();
  const { t } = useTranslation(["settings", "common"]);
  const [saving, setSaving] = useState(false);

  const current = routing?.default_model ?? "";
  const currentModel = models.find((m) => m.id === current);

  async function setDefault(id: string) {
    setSaving(true);
    try {
      await putRouting({ default_model: id });
      toast.success(
        id
          ? t("settings:defaultModel.toast.set", { id })
          : t("settings:defaultModel.toast.cleared"),
      );
      onChanged();
    } catch (e) {
      toast.error(t("common:toast.savingFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  }

  const connectedProviders = providers.filter((p) => p.has_key || p.type === "ollama").length;
  const totalModels = models.length;

  return (
    <>
      <SettingsSection
        title={t("settings:defaultModel.title")}
        icon="★"
        description={t("settings:defaultModel.description")}
      >
        <div className="s-quick-card">
          <select
            className="s-select"
            value={current}
            disabled={saving || models.length === 0}
            onChange={(e) => void setDefault(e.target.value)}
          >
            <option value="">{t("settings:defaultModel.chooseHint")}</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id} ({m.provider})
              </option>
            ))}
          </select>
          {models.length === 0 ? (
            <p className="s-quick-card__desc">
              <Trans
                i18nKey="settings:defaultModel.noModelsRegistered"
                components={{ bold: <b /> }}
              />
            </p>
          ) : currentModel ? (
            <p className="s-quick-card__desc">
              <Trans
                i18nKey="settings:defaultModel.inUse"
                values={{ id: currentModel.id, provider: currentModel.provider }}
                components={{ bold: <b /> }}
              />
              {currentModel.notes && <> — {currentModel.notes}</>}
            </p>
          ) : (
            <p className="s-quick-card__desc">{t("settings:defaultModel.selectAbove")}</p>
          )}
        </div>
      </SettingsSection>

      <SettingsSection title={t("settings:appearance.title")} icon="🎨">
        <AppearanceSection />
      </SettingsSection>

      <SettingsSection title={t("settings:status.title")} icon="✓">
        <div className="s-quick-status">
          <div className="s-quick-status__row">
            <span
              className={`s-quick-status__dot s-quick-status__dot--${
                connectedProviders > 0 ? "ok" : "warn"
              }`}
            />
            {connectedProviders > 0
              ? t("settings:status.providersConnected", { count: connectedProviders })
              : t("settings:status.providersNone")}
          </div>
          <div className="s-quick-status__row">
            <span
              className={`s-quick-status__dot s-quick-status__dot--${
                totalModels > 0 ? "ok" : "warn"
              }`}
            />
            {totalModels > 0
              ? t("settings:status.modelsAvailable", { count: totalModels })
              : t("settings:status.modelsNone")}
          </div>
          <div className="s-quick-status__row">
            <span
              className={`s-quick-status__dot s-quick-status__dot--${
                current ? "ok" : "warn"
              }`}
            />
            {current ? t("settings:status.defaultSet") : t("settings:status.defaultUnset")}
          </div>
        </div>
      </SettingsSection>
    </>
  );
}
