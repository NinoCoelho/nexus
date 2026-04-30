import { useTranslation } from "react-i18next";
import type { Provider } from "../../api";
import { TIERS, type ModelForm } from "./types";

interface Props {
  form: ModelForm;
  editingId: string | null;
  providers: Provider[];
  fetchedModels: string[];
  visibleFetched: string[];
  filter: string;
  fetching: boolean;
  discoveryError: string | null;
  onFormChange: (patch: Partial<ModelForm>) => void;
  onProviderChange: (provider: string) => void;
  onPickModel: (name: string) => void;
  onFilterChange: (f: string) => void;
  onFetchModels: (provider: string, force?: boolean) => void;
  onCancel: () => void;
  onSave: () => void;
}

export default function ModelFormPanel({
  form,
  editingId,
  providers,
  fetchedModels,
  visibleFetched,
  filter,
  fetching,
  discoveryError,
  onFormChange,
  onProviderChange,
  onPickModel,
  onFilterChange,
  onFetchModels,
  onCancel,
  onSave,
}: Props) {
  const { t } = useTranslation("models");
  return (
    <div className="settings-card settings-inline-form">
      {!editingId && (
        <>
          <div className="settings-field">
            <label className="settings-field-label">{t("models:form.providerLabel")}</label>
            <select
              className="settings-select"
              value={form.provider}
              onChange={(e) => onProviderChange(e.target.value)}
            >
              <option value="">{t("models:form.providerPlaceholder")}</option>
              {providers.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}{p.type ? ` (${p.type})` : ""}
                </option>
              ))}
            </select>
          </div>

          {form.provider && (
            <div className="settings-field">
              <label className="settings-field-label">{t("models:form.pickModelLabel")}</label>
              <div className="model-discover-toolbar">
                <button
                  className="settings-btn"
                  type="button"
                  disabled={fetching}
                  onClick={() => onFetchModels(form.provider, true)}
                >
                  {fetching
                    ? t("models:form.fetchingModels")
                    : fetchedModels.length > 0
                      ? t("models:form.refreshModels", { count: fetchedModels.length })
                      : t("models:form.listModels")}
                </button>
                {fetchedModels.length > 0 && (
                  <input
                    className="settings-input model-filter-input"
                    placeholder={t("models:form.filterPlaceholder")}
                    value={filter}
                    onChange={(e) => onFilterChange(e.target.value)}
                  />
                )}
              </div>

              {discoveryError && (
                <p className="settings-error">{t("models:form.fetchError", { error: discoveryError })}</p>
              )}

              {fetchedModels.length > 0 && (
                <div className="model-list">
                  {visibleFetched.length === 0 ? (
                    <div className="model-list-empty">{t("models:form.noMatch", { filter })}</div>
                  ) : (
                    visibleFetched.map((m) => {
                      const picked = form.model_name === m;
                      return (
                        <button
                          key={m}
                          type="button"
                          className={`model-list-row${picked ? " model-list-row--picked" : ""}`}
                          onClick={() => onPickModel(m)}
                        >
                          <span className="model-list-row-name">{m}</span>
                          {picked && <span className="model-list-row-picked">{t("models:form.selected")}</span>}
                        </button>
                      );
                    })
                  )}
                </div>
              )}

              <details className="model-custom-details">
                <summary>{t("models:form.customModelSummary")}</summary>
                <input
                  className="settings-input"
                  style={{ marginTop: 6 }}
                  value={form.model_name}
                  onChange={(e) => onFormChange({
                    model_name: e.target.value,
                    id: form.id_touched ? form.id : (form.provider ? `${form.provider}/${e.target.value}` : form.id),
                  })}
                  placeholder={t("models:form.customModelPlaceholder")}
                />
              </details>
            </div>
          )}
        </>
      )}

      {(editingId || (form.provider && form.model_name)) && (
        <>
          {!editingId && (
            <div className="settings-field">
              <label className="settings-field-label">{t("models:form.idLabel")}</label>
              <input
                className="settings-input"
                value={form.id}
                onChange={(e) => onFormChange({ id: e.target.value, id_touched: true })}
                placeholder={`${form.provider}/${form.model_name}`}
              />
              <span className="settings-field-hint">
                {t("models:form.idHint")}
              </span>
            </div>
          )}
          {editingId && (
            <div className="settings-field">
              <label className="settings-field-label">{t("models:form.modelNameLabel")}</label>
              <input
                className="settings-input"
                value={form.model_name}
                onChange={(e) => onFormChange({ model_name: e.target.value })}
              />
            </div>
          )}
          <div className="settings-field">
            <label className="settings-field-label">{t("models:form.tierLabel")}</label>
            <div style={{ display: "flex", gap: 6 }}>
              {TIERS.map((tier) => (
                <button
                  key={tier}
                  type="button"
                  className={`model-tier-chip model-tier-chip--${tier}${form.tier === tier ? " model-tier-chip--active" : ""}`}
                  onClick={() => onFormChange({ tier, tier_source: "manual" })}
                >
                  {tier}
                </button>
              ))}
            </div>
            <span className="settings-field-hint">
              {form.tier_source === "heuristic"
                ? t("models:form.tierHintHeuristic")
                : form.tier_source === "default"
                  ? t("models:form.tierHintDefault")
                  : t("models:form.tierHintManual")}
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">{t("models:form.notesLabel")}</label>
            <input
              className="settings-input"
              value={form.notes}
              onChange={(e) => onFormChange({ notes: e.target.value })}
              placeholder={t("models:form.notesPlaceholder")}
            />
            <span className="settings-field-hint">
              {t("models:form.notesHint")}
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">{t("models:form.tagsLabel")}</label>
            <input
              className="settings-input"
              value={form.tags}
              onChange={(e) => onFormChange({ tags: e.target.value })}
              placeholder={t("models:form.tagsPlaceholder")}
            />
          </div>
          <div className="settings-field">
            <label className="settings-field-label" style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={form.is_embedding_capable}
                onChange={(e) => onFormChange({ is_embedding_capable: e.target.checked })}
              />
              {t("models:form.embeddingLabel")}
            </label>
            <span className="settings-field-hint">
              {t("models:form.embeddingHint")}
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">{t("models:form.contextWindowLabel")}</label>
            <input
              className="settings-input"
              type="number"
              min={0}
              step={1024}
              value={form.context_window}
              onChange={(e) => onFormChange({ context_window: e.target.value })}
              placeholder={t("models:form.contextWindowPlaceholder")}
            />
            <span className="settings-field-hint">
              {t("models:form.contextWindowHint")}
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">{t("models:form.maxOutputLabel")}</label>
            <input
              className="settings-input"
              type="number"
              min={0}
              step={1024}
              value={form.max_output_tokens}
              onChange={(e) => onFormChange({ max_output_tokens: e.target.value })}
              placeholder={t("models:form.maxOutputPlaceholder")}
            />
            <span className="settings-field-hint">
              {t("models:form.maxOutputHint")}
            </span>
          </div>
        </>
      )}

      <div className="settings-row settings-row--end">
        <button className="settings-btn settings-btn--ghost" onClick={onCancel}>
          {t("models:form.cancel")}
        </button>
        <button
          className="settings-btn settings-btn--primary"
          onClick={onSave}
          disabled={!editingId && (!form.id.trim() || !form.provider || !form.model_name.trim())}
        >
          {editingId ? t("models:form.save") : t("models:form.update")}
        </button>
      </div>
    </div>
  );
}
