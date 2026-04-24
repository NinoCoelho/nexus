/**
 * ProvidersSection — LLM provider configuration for the settings drawer.
 *
 * Each provider card shows:
 *   - Connection status (green/red indicator)
 *   - API key input (stored in nexus secrets, not config.toml)
 *   - Base URL (for OpenAI-compat providers)
 *   - Type selector (openai_compat / anthropic / ollama)
 *
 * Key operations go through the secrets API (POST/DELETE /providers/{name}/key)
 * so they're never written to the config file.
 */

import { useState } from "react";
import { patchConfig, setProviderKey, clearProviderKey, type Provider } from "../api";
import { useToast } from "../toast/ToastProvider";

interface Props {
  providers: Provider[];
  onRefresh: () => void;
}

interface EditState {
  name: string;
  base_url: string;
  key_env: string;
  api_key: string;
}

interface AddState {
  name: string;
  base_url: string;
  key_env: string;
  key_env_touched: boolean;
  api_key: string;
  type: "openai_compat" | "anthropic" | "ollama";
}

// Derive the conventional env var name for a provider.
function defaultKeyEnv(providerName: string, type: AddState["type"]): string {
  if (type === "ollama") return "";
  const slug = providerName.trim().replace(/[^a-zA-Z0-9]+/g, "_").toUpperCase();
  return slug ? `${slug}_API_KEY` : "";
}

export default function ProvidersSection({ providers, onRefresh }: Props) {
  const toast = useToast();
  const [editing, setEditing] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<EditState>({ name: "", base_url: "", key_env: "", api_key: "" });
  const [adding, setAdding] = useState(false);
  const [addForm, setAddForm] = useState<AddState>({ name: "", base_url: "", key_env: "", key_env_touched: false, api_key: "", type: "openai_compat" });
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [confirmClearKey, setConfirmClearKey] = useState<string | null>(null);

  function startEdit(p: Provider) {
    setEditing(p.name);
    setEditForm({ name: p.name, base_url: p.base_url ?? "", key_env: p.key_env ?? "", api_key: "" });
    setConfirmClearKey(null);
  }

  async function saveEdit() {
    try {
      await patchConfig({
        providers: {
          [editForm.name]: {
            base_url: editForm.base_url || undefined,
            key_env: editForm.key_env || undefined,
            has_key: false,
          },
        },
      });
      if (editForm.api_key.trim()) {
        await setProviderKey(editForm.name, editForm.api_key.trim());
      }
      setEditing(null);
      toast.success(`Saved ${editForm.name}`);
      onRefresh();
    } catch (e) {
      toast.error("Save failed", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function removeProvider(name: string) {
    try {
      await patchConfig({ providers: { [name]: { has_key: false } } });
      setConfirmRemove(null);
      toast.success(`Removed ${name}`);
      onRefresh();
    } catch (e) {
      toast.error("Remove failed", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function doClearKey(name: string) {
    try {
      await clearProviderKey(name);
      setConfirmClearKey(null);
      toast.success(`Cleared key for ${name}`);
      onRefresh();
    } catch (e) {
      toast.error("Clear key failed", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function addProvider() {
    if (!addForm.name.trim()) return;
    try {
      await patchConfig({
        providers: {
          [addForm.name.trim()]: {
            base_url: addForm.base_url || undefined,
            key_env: addForm.key_env || undefined,
            has_key: false,
            // @ts-expect-error extra field accepted by backend
            type: addForm.type,
          },
        },
      });
      if (addForm.api_key.trim()) {
        await setProviderKey(addForm.name.trim(), addForm.api_key.trim());
      }
      const name = addForm.name.trim();
      setAdding(false);
      setAddForm({ name: "", base_url: "", key_env: "", key_env_touched: false, api_key: "", type: "openai_compat" });
      toast.success(`Added ${name}`);
      onRefresh();
    } catch (e) {
      toast.error("Add failed", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  function badgeClass(p: Provider) {
    if (p.key_source === "anonymous") return "settings-badge settings-badge--warn";
    return `settings-badge${p.has_key ? " settings-badge--ok" : " settings-badge--bad"}`;
  }

  function badgeText(p: Provider) {
    if (p.key_source === "anonymous") return "anonymous";
    if (p.has_key && p.key_source === "inline") return "configured (inline)";
    if (p.has_key && p.key_source === "env") return "configured (env)";
    return "not configured";
  }

  const isOllama = addForm.type === "ollama";

  return (
    <div className="settings-section">
      <div className="settings-section-label">Providers</div>

      {providers.map((p) => (
        <div key={p.name} className="settings-card">
          {editing === p.name ? (
            <div className="settings-inline-form">
              <div className="settings-field">
                <label className="settings-field-label">Base URL</label>
                <input
                  className="settings-input"
                  value={editForm.base_url}
                  onChange={(e) => setEditForm((f) => ({ ...f, base_url: e.target.value }))}
                  placeholder="https://api.example.com"
                />
              </div>
              <div className="settings-field">
                <label className="settings-field-label">Key env var</label>
                <input
                  className="settings-input"
                  value={editForm.key_env}
                  onChange={(e) => setEditForm((f) => ({ ...f, key_env: e.target.value }))}
                  placeholder="MY_PROVIDER_API_KEY"
                />
              </div>
              <div className="settings-field">
                <label className="settings-field-label">API key <span style={{opacity:0.7,fontWeight:400}}>(override)</span></label>
                <input
                  className="settings-input"
                  type="password"
                  value={editForm.api_key}
                  onChange={(e) => setEditForm((f) => ({ ...f, api_key: e.target.value }))}
                  placeholder="sk-…  (leave blank to use env var)"
                  autoComplete="off"
                />
                <span className="settings-field-hint">
                  Optional — overrides the env var above. Stored at ~/.nexus/secrets.toml (0600).
                </span>
              </div>
              {p.key_source === "inline" && (
                <div className="settings-row">
                  {confirmClearKey === p.name ? (
                    <>
                      <button className="settings-icon-btn settings-icon-btn--bad" onClick={() => doClearKey(p.name)}>
                        Confirm clear
                      </button>
                      <button className="settings-icon-btn" onClick={() => setConfirmClearKey(null)}>
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button className="settings-clear-key-btn" onClick={() => setConfirmClearKey(p.name)}>
                      Clear key
                    </button>
                  )}
                </div>
              )}
              <div className="settings-row settings-row--end">
                <button className="settings-btn settings-btn--ghost" onClick={() => setEditing(null)}>
                  Cancel
                </button>
                <button className="settings-btn settings-btn--primary" onClick={saveEdit}>
                  Save
                </button>
              </div>
            </div>
          ) : (
            <div className="settings-card-row">
              <div className="settings-card-info">
                <span className="settings-provider-name">{p.name}</span>
                {p.base_url && (
                  <span className="settings-provider-url">{p.base_url}</span>
                )}
              </div>
              <div className="settings-card-actions">
                <span className={badgeClass(p)}>{badgeText(p)}</span>
                <button className="settings-icon-btn" title="Edit" onClick={() => startEdit(p)}>
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9.5 2.5l2 2L4 12H2v-2L9.5 2.5z" />
                  </svg>
                </button>
                {confirmRemove === p.name ? (
                  <>
                    <button className="settings-icon-btn settings-icon-btn--bad" onClick={() => removeProvider(p.name)}>
                      Confirm
                    </button>
                    <button className="settings-icon-btn" onClick={() => setConfirmRemove(null)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <button className="settings-icon-btn settings-icon-btn--bad" title="Remove" onClick={() => setConfirmRemove(p.name)}>
                    ✕
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      ))}

      {adding ? (
        <div className="settings-card settings-inline-form">
          <div className="settings-field">
            <label className="settings-field-label">Name</label>
            <input
              className="settings-input"
              value={addForm.name}
              onChange={(e) => {
                const name = e.target.value;
                setAddForm((f) => ({
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
                onClick={() => setAddForm((f) => ({
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
                onClick={() => setAddForm((f) => ({
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
                onClick={() => setAddForm((f) => ({
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
              onChange={(e) => setAddForm((f) => ({ ...f, base_url: e.target.value }))}
              placeholder={isOllama ? "http://localhost:11434" : "https://api.openai.com/v1"}
            />
          </div>
          {!isOllama && (
            <>
              <div className="settings-field">
                <label className="settings-field-label">Key env var</label>
                <input
                  className="settings-input"
                  value={addForm.key_env}
                  onChange={(e) => setAddForm((f) => ({ ...f, key_env: e.target.value, key_env_touched: true }))}
                  placeholder={defaultKeyEnv(addForm.name || "provider", addForm.type)}
                />
                <span className="settings-field-hint">
                  Auto-filled from the provider name. Export this variable in your shell so Nexus can read the key.
                </span>
              </div>
              <div className="settings-field">
                <label className="settings-field-label">API key <span style={{opacity:0.7,fontWeight:400}}>(override)</span></label>
                <input
                  className="settings-input"
                  type="password"
                  value={addForm.api_key}
                  onChange={(e) => setAddForm((f) => ({ ...f, api_key: e.target.value }))}
                  placeholder="sk-…  (leave blank to use env var)"
                  autoComplete="off"
                />
                <span className="settings-field-hint">
                  Optional — overrides the env var above. Stored at ~/.nexus/secrets.toml (0600).
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
            <button className="settings-btn settings-btn--ghost" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button className="settings-btn settings-btn--primary" onClick={addProvider} disabled={!addForm.name.trim()}>
              Add
            </button>
          </div>
        </div>
      ) : (
        <button className="settings-add-btn" onClick={() => setAdding(true)}>
          + Add provider
        </button>
      )}
    </div>
  );
}
