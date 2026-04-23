import { useState, useCallback } from "react";
import {
  deleteModel,
  postModel,
  fetchProviderModels,
  setModelRole,
  type Model,
  type ModelStrengths,
  type Provider,
  type RoutingConfig,
} from "../api";
import { useToast } from "../toast/ToastProvider";

interface Props {
  models: Model[];
  providers: Provider[];
  routing: RoutingConfig | null;
  onRefresh: () => void;
}

const EMBEDDING_COMPAT_TYPES = new Set(["openai_compat", "ollama"]);

interface AddForm {
  id: string;
  id_touched: boolean;
  provider: string;
  model_name: string;
  tags: string;
  strengths: ModelStrengths;
}

interface DiscoveryState {
  models: string[];
  fetchedAt: number;
  error: string | null;
}

const CACHE_TTL_MS = 30_000;
const emptyStrengths: ModelStrengths = { speed: 5, cost: 5, reasoning: 5, coding: 5 };
const emptyForm: AddForm = {
  id: "",
  id_touched: false,
  provider: "",
  model_name: "",
  tags: "",
  strengths: emptyStrengths,
};

export default function ModelsSection({ models, providers, routing, onRefresh }: Props) {
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<AddForm>(emptyForm);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<Record<string, DiscoveryState>>({});
  const [fetching, setFetching] = useState(false);
  const [filter, setFilter] = useState("");
  const [roleSaving, setRoleSaving] = useState(false);

  const embModelId = routing?.embedding_model_id ?? "";
  const extModelId = routing?.extraction_model_id ?? "";
  const clsModelId = routing?.classification_model ?? "";

  const providerTypeMap = Object.fromEntries(providers.map((p) => [p.name, p.type ?? "openai_compat"]));

  function getModelRoles(m: Model): string[] {
    const roles: string[] = [];
    if (embModelId === m.id) roles.push("embedding");
    if (extModelId === m.id) roles.push("extraction");
    if (clsModelId === m.id) roles.push("classification");
    return roles;
  }

  function canEmbed(m: Model): boolean {
    const ptype = providerTypeMap[m.provider];
    return EMBEDDING_COMPAT_TYPES.has(ptype);
  }

  function hasRole(m: Model): boolean {
    return embModelId === m.id || extModelId === m.id || clsModelId === m.id;
  }

  async function assignRole(role: string, modelId: string) {
    setRoleSaving(true);
    try {
      await setModelRole(role, modelId);
      onRefresh();
    } catch (e) {
      toast.error("Failed to assign role", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setRoleSaving(false);
    }
  }

  async function removeModel(id: string) {
    try {
      await deleteModel(id);
      setConfirmRemove(null);
      toast.success(`Removed ${id}`);
      onRefresh();
    } catch (e) {
      toast.error("Remove failed", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  const currentDiscovery = form.provider ? discovery[form.provider] : undefined;
  const fetchedModels = currentDiscovery?.models ?? [];

  const doFetchModels = useCallback(async (provider: string, force = false) => {
    if (!provider) return;
    const cached = discovery[provider];
    if (!force && cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) return;
    setFetching(true);
    try {
      const result = await fetchProviderModels(provider);
      setDiscovery((c) => ({
        ...c,
        [provider]: {
          models: result.ok ? result.models : [],
          fetchedAt: Date.now(),
          error: result.ok ? null : (result.error || "Failed to fetch models"),
        },
      }));
    } catch (e) {
      setDiscovery((c) => ({
        ...c,
        [provider]: {
          models: [],
          fetchedAt: Date.now(),
          error: e instanceof Error ? e.message : "Fetch failed",
        },
      }));
    } finally {
      setFetching(false);
    }
  }, [discovery]);

  function pickModel(upstreamName: string) {
    setForm((f) => ({
      ...f,
      model_name: upstreamName,
      id: f.id_touched ? f.id : `${f.provider}/${upstreamName}`,
    }));
  }

  async function addModel() {
    if (!form.id.trim() || !form.provider || !form.model_name.trim()) return;
    try {
      await postModel({
        id: form.id.trim(),
        provider: form.provider,
        model_name: form.model_name.trim(),
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        strengths: form.strengths,
      });
      const id = form.id.trim();
      setAdding(false);
      setForm(emptyForm);
      setFilter("");
      toast.success(`Added model ${id}`);
      onRefresh();
    } catch (e) {
      toast.error("Add failed", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  const visibleFetched = filter.trim()
    ? fetchedModels.filter((m) => m.toLowerCase().includes(filter.trim().toLowerCase()))
    : fetchedModels;

  const discoveryError = currentDiscovery?.error ?? null;

  return (
    <div className="settings-section">
      <div className="settings-section-label">
        Models {roleSaving && <span style={{ color: "var(--fg-faint)", fontWeight: 400 }}>· saving…</span>}
      </div>

      {models.map((m) => {
        const roles = getModelRoles(m);
        const isEmb = roles.includes("embedding");
        const isExt = roles.includes("extraction");
        const isCls = roles.includes("classification");
        const locked = hasRole(m);
        return (
          <div key={m.id} className="settings-card">
            <div className="settings-card-row">
              <div className="settings-card-info">
                <div className="settings-model-header">
                  <span className="settings-model-id">{m.id}</span>
                  <span className="settings-model-provider">{m.provider}</span>
                </div>
                <div className="settings-tag-row">
                  {m.tags.map((t) => (
                    <span key={t} className="settings-tag-chip">{t}</span>
                  ))}
                </div>
                <div className="model-role-badges">
                  <button
                    type="button"
                    className={`model-role-badge ${isEmb ? "model-role-badge--active" : ""} ${!canEmbed(m) ? "model-role-badge--disabled" : ""}`}
                    onClick={() => !isEmb && canEmbed(m) && assignRole("embedding", m.id)}
                    disabled={isEmb || !canEmbed(m) || roleSaving}
                    title={isEmb ? "Embedding model" : canEmbed(m) ? "Set as embedding model" : "Incompatible provider"}
                  >
                    Embedding
                  </button>
                  <button
                    type="button"
                    className={`model-role-badge ${isExt ? "model-role-badge--active" : ""}`}
                    onClick={() => !isExt && assignRole("extraction", m.id)}
                    disabled={isExt || roleSaving}
                    title={isExt ? "Extraction model" : "Set as extraction model"}
                  >
                    Extraction
                  </button>
                  <button
                    type="button"
                    className={`model-role-badge ${isCls ? "model-role-badge--active" : ""}`}
                    onClick={() => !isCls && assignRole("classification", m.id)}
                    disabled={isCls || roleSaving}
                    title={isCls ? "Auto-routing model" : "Set as auto-routing model"}
                  >
                    Auto-route
                  </button>
                </div>
              </div>
              <div className="settings-strength-bars">
                {(["speed", "cost", "reasoning", "coding"] as const).map((k) => (
                  <div key={k} className="settings-strength-row">
                    <span className="settings-strength-label">{k.slice(0, 3)}</span>
                    <span className="settings-strength-track">
                      <span
                        className="settings-strength-fill"
                        style={{ width: `${(m.strengths[k] ?? 0) * 10}%` }}
                      />
                    </span>
                  </div>
                ))}
              </div>
              <div className="settings-card-actions">
                {locked ? (
                  <span className="settings-icon-btn settings-icon-btn--locked" title="Cannot remove — reassign role first">
                    🔒
                  </span>
                ) : confirmRemove === m.id ? (
                  <>
                    <button className="settings-icon-btn settings-icon-btn--bad" onClick={() => removeModel(m.id)}>
                      Confirm
                    </button>
                    <button className="settings-icon-btn" onClick={() => setConfirmRemove(null)}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <button className="settings-icon-btn settings-icon-btn--bad" title="Remove" onClick={() => setConfirmRemove(m.id)}>
                    ✕
                  </button>
                )}
              </div>
            </div>
          </div>
        );
      })}

      {adding ? (
        <div className="settings-card settings-inline-form">
          <div className="settings-field">
            <label className="settings-field-label">1. Provider</label>
            <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
              <select
                className="settings-select"
                style={{ flex: 1 }}
                value={form.provider}
                onChange={(e) => {
                  setForm((f) => ({ ...f, provider: e.target.value }));
                  setFilter("");
                }}
              >
                <option value="">Select provider…</option>
                {providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}{p.type ? ` (${p.type})` : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {form.provider && (
            <div className="settings-field">
              <label className="settings-field-label">2. Pick a model</label>
              <div className="model-discover-toolbar">
                <button
                  className="settings-btn"
                  type="button"
                  disabled={fetching}
                  onClick={() => doFetchModels(form.provider, true)}
                >
                  {fetching
                    ? "Fetching…"
                    : fetchedModels.length > 0
                      ? `↻ Refresh (${fetchedModels.length})`
                      : "List available models"}
                </button>
                {fetchedModels.length > 0 && (
                  <input
                    className="settings-input model-filter-input"
                    placeholder="Filter…"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  />
                )}
              </div>

              {discoveryError && (
                <p className="settings-error">Could not list models: {discoveryError}</p>
              )}

              {fetchedModels.length > 0 && (
                <div className="model-list">
                  {visibleFetched.length === 0 ? (
                    <div className="model-list-empty">No models match "{filter}"</div>
                  ) : (
                    visibleFetched.map((m) => {
                      const picked = form.model_name === m;
                      return (
                        <button
                          key={m}
                          type="button"
                          className={`model-list-row${picked ? " model-list-row--picked" : ""}`}
                          onClick={() => pickModel(m)}
                        >
                          <span className="model-list-row-name">{m}</span>
                          {picked && <span className="model-list-row-picked">✓ selected</span>}
                        </button>
                      );
                    })
                  )}
                </div>
              )}

              <details className="model-custom-details">
                <summary>Or enter a custom model name</summary>
                <input
                  className="settings-input"
                  style={{ marginTop: 6 }}
                  value={form.model_name}
                  onChange={(e) => setForm((f) => ({
                    ...f,
                    model_name: e.target.value,
                    id: f.id_touched ? f.id : (f.provider ? `${f.provider}/${e.target.value}` : f.id),
                  }))}
                  placeholder="e.g. gpt-4o-2024-08-06"
                />
              </details>
            </div>
          )}

          {form.provider && form.model_name && (
            <>
              <div className="settings-field">
                <label className="settings-field-label">3. Model id (internal)</label>
                <input
                  className="settings-input"
                  value={form.id}
                  onChange={(e) => setForm((f) => ({ ...f, id: e.target.value, id_touched: true }))}
                  placeholder={`${form.provider}/${form.model_name}`}
                />
                <span className="settings-field-hint">
                  Auto-generated from provider/name. Used internally as the model identifier.
                </span>
              </div>
              <div className="settings-field">
                <label className="settings-field-label">Tags (comma separated)</label>
                <input
                  className="settings-input"
                  value={form.tags}
                  onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
                  placeholder="fast, cheap"
                />
              </div>
              <div className="settings-field">
                <label className="settings-field-label">Strengths (0–10)</label>
                <div className="settings-strength-inputs">
                  {(["speed", "cost", "reasoning", "coding"] as const).map((k) => (
                    <label key={k} className="settings-strength-input">
                      <span>{k}</span>
                      <input
                        type="number"
                        min={0}
                        max={10}
                        className="settings-input settings-input--num"
                        value={form.strengths[k]}
                        onChange={(e) =>
                          setForm((f) => ({
                            ...f,
                            strengths: { ...f.strengths, [k]: Number(e.target.value) },
                          }))
                        }
                      />
                    </label>
                  ))}
                </div>
              </div>
            </>
          )}

          <div className="settings-row settings-row--end">
            <button
              className="settings-btn settings-btn--ghost"
              onClick={() => { setAdding(false); setForm(emptyForm); setFilter(""); }}
            >
              Cancel
            </button>
            <button
              className="settings-btn settings-btn--primary"
              onClick={addModel}
              disabled={!form.id.trim() || !form.provider || !form.model_name.trim()}
            >
              Add model
            </button>
          </div>
        </div>
      ) : (
        <button className="settings-add-btn" onClick={() => setAdding(true)}>
          + Add model
        </button>
      )}
    </div>
  );
}
