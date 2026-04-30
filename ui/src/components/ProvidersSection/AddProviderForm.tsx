// Sub-component for ProvidersSection: form for adding a new LLM provider.

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
  const isOllama = addForm.type === "ollama";

  return (
    <div className="settings-card settings-inline-form">
      <div className="settings-field">
        <label className="settings-field-label">Name</label>
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
          placeholder="my-provider"
          autoFocus
        />
      </div>
      <div className="settings-field">
        <label className="settings-field-label">Type</label>
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
            OpenAI-compatible
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
            Anthropic
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
            Ollama
          </button>
        </div>
      </div>
      <div className="settings-field">
        <label className="settings-field-label">Base URL{isOllama ? "" : " (optional)"}</label>
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
            <label className="settings-field-label">Credential</label>
            <CredentialPicker
              value={addForm.credential_ref}
              onChange={(ref) => onAddFormChange((f) => ({ ...f, credential_ref: ref }))}
              defaultNameSuggestion={defaultKeyEnv(addForm.name || "provider", addForm.type)}
            />
            <span className="settings-field-hint">
              Pick an existing stored credential or click "+ Create new" to
              save one now. Leave as <em>(none)</em> to use the legacy env-var
              path below.
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">
              Key env var <span style={{opacity:0.7,fontWeight:400}}>(legacy fallback)</span>
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
                ? "Disabled — a credential is bound."
                : "Auto-filled from the provider name. Export this variable in your shell so Nexus can read the key."}
            </span>
          </div>
        </>
      )}
      {isOllama && (
        <span className="settings-field-hint">
          Ollama runs locally and requires no API key.
        </span>
      )}
      <div className="settings-row settings-row--end">
        <button className="settings-btn settings-btn--ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="settings-btn settings-btn--primary" onClick={onSubmit} disabled={!addForm.name.trim()}>
          Add
        </button>
      </div>
    </div>
  );
}
