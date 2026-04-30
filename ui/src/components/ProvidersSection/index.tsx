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
import { useTranslation } from "react-i18next";
import {
  patchConfig,
  setProviderKey,
  clearProviderKey,
  setProviderCredential,
  type Provider,
} from "../../api";
import CredentialPicker from "../settings/CredentialPicker";
import { useToast } from "../../toast/ToastProvider";
import { AddProviderForm } from "./AddProviderForm";
import type { EditState, AddState } from "./types";

interface Props {
  providers: Provider[];
  onRefresh: () => void;
}

function badgeClass(p: Provider) {
  if (p.key_source === "anonymous") return "settings-badge settings-badge--warn";
  return `settings-badge${p.has_key ? " settings-badge--ok" : " settings-badge--bad"}`;
}

function badgeText(p: Provider, t: (key: string, opts?: Record<string, unknown>) => string) {
  if (p.key_source === "anonymous") return t("providers:badge.anonymous");
  if (p.has_key && p.key_source === "credential") return t("providers:badge.viaCredential", { ref: p.credential_ref });
  if (p.has_key && p.key_source === "inline") return t("providers:badge.inlineLegacy");
  if (p.has_key && p.key_source === "env") return t("providers:badge.env", { env: p.key_env });
  return t("providers:badge.notConfigured");
}

/** Default credential name for the picker's "Create new" modal. Prefer the
 *  configured env var when present (it's already the user's canonical
 *  identifier for this provider); otherwise synthesize ``<NAME>_API_KEY``. */
function defaultCredentialName(p: Provider): string {
  if (p.key_env) return p.key_env;
  return `${p.name.toUpperCase()}_API_KEY`;
}

export default function ProvidersSection({ providers, onRefresh }: Props) {
  const { t } = useTranslation("providers");
  const toast = useToast();
  const [editing, setEditing] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<EditState>({ name: "", base_url: "", key_env: "", api_key: "" });
  const [adding, setAdding] = useState(false);
  const [addForm, setAddForm] = useState<AddState>({ name: "", base_url: "", key_env: "", key_env_touched: false, api_key: "", credential_ref: null, type: "openai_compat" });
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [confirmClearKey, setConfirmClearKey] = useState<string | null>(null);

  function startEdit(p: Provider) {
    setEditing(p.name);
    setEditForm({ name: p.name, base_url: p.base_url ?? "", key_env: p.key_env ?? "", api_key: "" });
    setConfirmClearKey(null);
  }

  async function saveEdit() {
    // The credential picker mutates state on its own (via PUT
    // /providers/{name}/credential); this save handles the non-credential
    // edit fields (base_url, key_env legacy fallback) only.
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
      setEditing(null);
      toast.success(t("providers:toast.saved", { name: editForm.name }));
      onRefresh();
    } catch (e) {
      toast.error(t("providers:toast.saveFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  }

  async function handleCredentialChange(p: Provider, ref: string | null) {
    try {
      await setProviderCredential(p.name, ref);
      toast.success(
        ref ? t("providers:toast.credentialBound", { name: p.name, ref }) : t("providers:toast.credentialCleared", { name: p.name }),
      );
      onRefresh();
    } catch (e) {
      toast.error(t("providers:toast.credentialBindFailed"), {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function removeProvider(name: string) {
    try {
      await patchConfig({ providers: { [name]: { has_key: false } } });
      setConfirmRemove(null);
      toast.success(t("providers:toast.removed", { name }));
      onRefresh();
    } catch (e) {
      toast.error(t("providers:toast.removeFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  }

  async function doClearKey(name: string) {
    try {
      await clearProviderKey(name);
      setConfirmClearKey(null);
      toast.success(t("providers:toast.keyCleared", { name }));
      onRefresh();
    } catch (e) {
      toast.error(t("providers:toast.clearKeyFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  }

  async function addProvider() {
    if (!addForm.name.trim()) return;
    const name = addForm.name.trim();
    try {
      await patchConfig({
        providers: {
          [name]: {
            base_url: addForm.base_url || undefined,
            key_env: addForm.key_env || undefined,
            has_key: false,
            // @ts-expect-error extra field accepted by backend
            type: addForm.type,
          },
        },
      });
      // After the provider is registered, bind it to the chosen credential
      // (if any). The PUT endpoint also clears use_inline_key/api_key_env on
      // the server side so the legacy paths can't shadow the user's choice.
      if (addForm.credential_ref) {
        await setProviderCredential(name, addForm.credential_ref);
      } else if (addForm.api_key.trim()) {
        // Legacy "paste key directly" support — kept for the rare user who
        // skips the picker and types into the inline field.
        await setProviderKey(name, addForm.api_key.trim());
      }
      setAdding(false);
      setAddForm({ name: "", base_url: "", key_env: "", key_env_touched: false, api_key: "", credential_ref: null, type: "openai_compat" });
      toast.success(t("providers:toast.added", { name }));
      onRefresh();
    } catch (e) {
      toast.error(t("providers:toast.addFailed"), { detail: e instanceof Error ? e.message : undefined });
    }
  }

  return (
    <div className="providers-section">
      {providers.map((p) => (
        <div key={p.name} className="settings-card">
          {editing === p.name ? (
            <div className="settings-inline-form">
              <div className="settings-field">
                <label className="settings-field-label">{t("providers:edit.baseUrlLabel")}</label>
                <input
                  className="settings-input"
                  value={editForm.base_url}
                  onChange={(e) => setEditForm((f) => ({ ...f, base_url: e.target.value }))}
                  placeholder="https://api.example.com"
                />
              </div>
              <div className="settings-field">
                <label className="settings-field-label">{t("providers:edit.credentialLabel")}</label>
                <CredentialPicker
                  value={p.credential_ref ?? null}
                  onChange={(ref) => void handleCredentialChange(p, ref)}
                  defaultNameSuggestion={defaultCredentialName(p)}
                />
                <span className="settings-field-hint">
                  {t("providers:edit.credentialHint")}
                </span>
              </div>
              <div className="settings-field">
                <label className="settings-field-label">
                  {t("providers:edit.keyEnvLabel")} <span style={{opacity:0.7,fontWeight:400}}>{t("providers:edit.keyEnvLegacy")}</span>
                </label>
                <input
                  className="settings-input"
                  value={editForm.key_env}
                  onChange={(e) => setEditForm((f) => ({ ...f, key_env: e.target.value }))}
                  placeholder={t("providers:edit.keyEnvPlaceholder")}
                  disabled={!!p.credential_ref}
                />
                <span className="settings-field-hint">
                  {p.credential_ref
                    ? t("providers:edit.keyEnvDisabledHint")
                    : t("providers:edit.keyEnvFallbackHint")}
                </span>
              </div>
              {p.key_source === "inline" && !p.credential_ref && (
                <div className="settings-row">
                  {confirmClearKey === p.name ? (
                    <>
                      <button className="settings-icon-btn settings-icon-btn--bad" onClick={() => doClearKey(p.name)}>
                        {t("providers:edit.confirmClear")}
                      </button>
                      <button className="settings-icon-btn" onClick={() => setConfirmClearKey(null)}>
                        {t("providers:edit.cancel")}
                      </button>
                    </>
                  ) : (
                    <button className="settings-clear-key-btn" onClick={() => setConfirmClearKey(p.name)}>
                      {t("providers:edit.clearInlineKey")}
                    </button>
                  )}
                </div>
              )}
              <div className="settings-row settings-row--end">
                <button className="settings-btn settings-btn--ghost" onClick={() => setEditing(null)}>
                  {t("providers:edit.cancel")}
                </button>
                <button className="settings-btn settings-btn--primary" onClick={saveEdit}>
                  {t("providers:edit.save")}
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
                <span className={badgeClass(p)}>{badgeText(p, t)}</span>
                <button className="settings-icon-btn" title={t("providers:edit.editTitle")} onClick={() => startEdit(p)}>
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9.5 2.5l2 2L4 12H2v-2L9.5 2.5z" />
                  </svg>
                </button>
                {confirmRemove === p.name ? (
                  <>
                    <button className="settings-icon-btn settings-icon-btn--bad" onClick={() => removeProvider(p.name)}>
                      {t("providers:remove.confirm")}
                    </button>
                    <button className="settings-icon-btn" onClick={() => setConfirmRemove(null)}>
                      {t("providers:remove.cancel")}
                    </button>
                  </>
                ) : (
                  <button className="settings-icon-btn settings-icon-btn--bad" title={t("providers:remove.removeTitle")} onClick={() => setConfirmRemove(p.name)}>
                    ✕
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      ))}

      {adding ? (
        <AddProviderForm
          addForm={addForm}
          onAddFormChange={setAddForm}
          onCancel={() => setAdding(false)}
          onSubmit={addProvider}
        />
      ) : (
        <button className="settings-add-btn" onClick={() => setAdding(true)}>
          {t("providers:section.addProvider")}
        </button>
      )}
    </div>
  );
}
