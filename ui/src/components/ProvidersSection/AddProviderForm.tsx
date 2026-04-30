// Sub-component for ProvidersSection: form for adding a new LLM provider.

import { useTranslation } from "react-i18next";
import CredentialPicker from "../settings/CredentialPicker";
import type { AddState } from "./types";

// Derive the conventional env var name for a provider.
export function defaultKeyEnv(providerName: string, type: AddState["type"]): string {
  if (type === "ollama") return "";
  const slug = providerName.trim().replace(/[^a-zA-Z0-9]+/g, "_").toUpperCase();
  return slug ? `${slug}_API_KEY` : "";
}

interface Props {
  addForm: AddState;
  onAddFormChange: (updater: (f: AddState) => AddState) => void;
  onCancel: () => void;
  onSubmit: () => void;
}

export function AddProviderForm({ addForm, onAddFormChange, onCancel, onSubmit }: Props) {
  const { t } = useTranslation("providers");
  const isOllama = addForm.type === "ollama";

  return (
    <div className="settings-card settings-inline-form">
      <div className="settings-field">
        <label className="settings-field-label">{t("providers:add.nameLabel")}</label>
        <input
          className="settings-input"
          value={addForm.name}
          onChange={(e) => {
            const name = e.target.value;
            onAddFormChange((f) => ({
              ...f,
              name,
              key_env: f.key_env_touched ? f.key_env : defaultKeyEnv(name, f.type),
            }));
          }}
          placeholder={t("providers:add.namePlaceholder")}
          autoFocus
        />
      </div>
      <div className="settings-field">
        <label className="settings-field-label">{t("providers:add.typeLabel")}</label>
        <div className="seg-control">
          <button
            className={`seg-btn${addForm.type === "openai_compat" ? " seg-btn--active" : ""}`}
            onClick={() => onAddFormChange((f) => ({
              ...f,
              type: "openai_compat",
              base_url: "",
              key_env: f.key_env_touched ? f.key_env : defaultKeyEnv(f.name, "openai_compat"),
            }))}
            type="button"
          >
            {t("providers:add.typeOpenAI")}
          </button>
          <button
            className={`seg-btn${addForm.type === "anthropic" ? " seg-btn--active" : ""}`}
            onClick={() => onAddFormChange((f) => ({
              ...f,
              type: "anthropic",
              base_url: "",
              key_env: f.key_env_touched ? f.key_env : defaultKeyEnv(f.name, "anthropic"),
            }))}
            type="button"
          >
            {t("providers:add.typeAnthropic")}
          </button>
          <button
            className={`seg-btn${addForm.type === "ollama" ? " seg-btn--active" : ""}`}
            onClick={() => onAddFormChange((f) => ({
              ...f,
              type: "ollama",
              base_url: "http://localhost:11434",
              key_env: "",
              api_key: "",
            }))}
            type="button"
          >
            {t("providers:add.typeOllama")}
          </button>
        </div>
      </div>
      <div className="settings-field">
        <label className="settings-field-label">{t("providers:add.baseUrlLabel")}{isOllama ? "" : t("providers:add.baseUrlOptional")}</label>
        <input
          className="settings-input"
          value={addForm.base_url}
          onChange={(e) => onAddFormChange((f) => ({ ...f, base_url: e.target.value }))}
          placeholder={isOllama ? "http://localhost:11434" : "https://api.openai.com/v1"}
        />
      </div>
      {!isOllama && (
        <>
          <div className="settings-field">
            <label className="settings-field-label">{t("providers:add.credentialLabel")}</label>
            <CredentialPicker
              value={addForm.credential_ref}
              onChange={(ref) => onAddFormChange((f) => ({ ...f, credential_ref: ref }))}
              defaultNameSuggestion={defaultKeyEnv(addForm.name || "provider", addForm.type)}
            />
            <span className="settings-field-hint">
              {t("providers:add.credentialHint")}
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">
              {t("providers:add.keyEnvLabel")} <span style={{opacity:0.7,fontWeight:400}}>{t("providers:add.keyEnvLegacy")}</span>
            </label>
            <input
              className="settings-input"
              value={addForm.key_env}
              onChange={(e) => onAddFormChange((f) => ({ ...f, key_env: e.target.value, key_env_touched: true }))}
              placeholder={defaultKeyEnv(addForm.name || "provider", addForm.type)}
              disabled={!!addForm.credential_ref}
            />
            <span className="settings-field-hint">
              {addForm.credential_ref
                ? t("providers:add.keyEnvDisabledHint")
                : t("providers:add.keyEnvAutoHint")}
            </span>
          </div>
        </>
      )}
      {isOllama && (
        <span className="settings-field-hint">
          {t("providers:add.ollamaNote")}
        </span>
      )}
      <div className="settings-row settings-row--end">
        <button className="settings-btn settings-btn--ghost" onClick={onCancel}>
          {t("providers:add.cancel")}
        </button>
        <button className="settings-btn settings-btn--primary" onClick={onSubmit} disabled={!addForm.name.trim()}>
          {t("providers:add.add")}
        </button>
      </div>
    </div>
  );
}
