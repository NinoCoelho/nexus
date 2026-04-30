import { useState } from "react";
import { useTranslation } from "react-i18next";
import { putRouting, type Model, type RoutingConfig } from "../../api";
import { useToast } from "../../toast/ToastProvider";

interface Props {
  routing: RoutingConfig | null;
  models: Model[];
  onChanged: () => void;
}

export default function DefaultModelStrip({ routing, models, onChanged }: Props) {
  const toast = useToast();
  const { t } = useTranslation(["settings", "common"]);
  const [picking, setPicking] = useState(false);
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
      setPicking(false);
      onChanged();
    } catch (e) {
      toast.error(t("settings:defaultModel.toast.saveFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="s-default-strip">
      <span className="s-default-strip__icon" aria-hidden>★</span>
      <div className="s-default-strip__text">
        <span className="s-default-strip__label">{t("settings:defaultModel.label")}</span>
        {picking ? (
          <select
            className="s-select"
            autoFocus
            disabled={saving}
            value={current}
            onChange={(e) => void setDefault(e.target.value)}
            onBlur={() => setPicking(false)}
          >
            <option value="">{t("common:common.none")}</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        ) : current ? (
          <span className="s-default-strip__value" title={current}>
            {current}
            {currentModel && (
              <span style={{ color: "var(--fg-faint)", fontFamily: "inherit", fontWeight: 400 }}>
                {" · "}
                {currentModel.provider}
              </span>
            )}
          </span>
        ) : (
          <span className="s-default-strip__value s-default-strip__value--empty">
            {t("settings:defaultModel.noModelSelected")}
          </span>
        )}
      </div>
      {!picking && (
        <button
          type="button"
          className="s-default-strip__btn"
          onClick={() => setPicking(true)}
          disabled={saving || models.length === 0}
          title={
            models.length === 0
              ? t("settings:defaultModel.addModelFirst")
              : t("settings:defaultModel.changeTooltip")
          }
        >
          {t("common:buttons.change")}
        </button>
      )}
    </div>
  );
}
