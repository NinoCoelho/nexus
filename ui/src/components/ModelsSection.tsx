/**
 * ModelsSection — model configuration UI for the settings drawer.
 *
 * Models carry a single routing hint (`tier`) + optional free-text `notes`.
 * Tier auto-fills from the model name when adding; the user can override.
 */

import { useCallback, useEffect, useState } from "react";
import {
  clearModelRole,
  deleteModel,
  postModel,
  patchModel,
  fetchProviderModels,
  putRouting,
  setModelRole,
  suggestModelTier,
  type Model,
  type ModelTier,
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
const TIERS: ModelTier[] = ["fast", "balanced", "heavy"];

interface ModelForm {
  id: string;
  id_touched: boolean;
  provider: string;
  model_name: string;
  tags: string;
  tier: ModelTier;
  notes: string;
  tier_source: "heuristic" | "default" | "manual";
}

interface DiscoveryState {
  models: string[];
  fetchedAt: number;
  error: string | null;
}

const CACHE_TTL_MS = 30_000;
const emptyForm: ModelForm = {
  id: "",
  id_touched: false,
  provider: "",
  model_name: "",
  tags: "",
  tier: "balanced",
  notes: "",
  tier_source: "default",
};

export default function ModelsSection({ models, providers, routing, onRefresh }: Props) {
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ModelForm>(emptyForm);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<Record<string, DiscoveryState>>({});
  const [fetching, setFetching] = useState(false);
  const [filter, setFilter] = useState("");
  const [roleSaving, setRoleSaving] = useState(false);

  const embModelId = routing?.embedding_model_id ?? "";
  const extModelId = routing?.extraction_model_id ?? "";

  const providerTypeMap = Object.fromEntries(providers.map((p) => [p.name, p.type ?? "openai_compat"]));

  function getModelRoles(m: Model): string[] {
    const roles: string[] = [];
    if (embModelId === m.id) roles.push("embedding");
    if (extModelId === m.id) roles.push("extraction");
    return roles;
  }

  function canEmbed(m: Model): boolean {
    const ptype = providerTypeMap[m.provider];
    return EMBEDDING_COMPAT_TYPES.has(ptype);
  }

  function hasRole(m: Model): boolean {
    return embModelId === m.id || extModelId === m.id;
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

  async function unassignRole(role: string) {
    setRoleSaving(true);
    try {
      await clearModelRole(role);
      onRefresh();
    } catch (e) {
      toast.error("Failed to clear role", { detail: e instanceof Error ? e.message : undefined });
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

  // When the model_name changes in add-mode, fetch a tier suggestion.
  useEffect(() => {
    if (editingId) return; // editing keeps whatever was there
    const name = form.model_name.trim();
    if (!name) return;
    let cancelled = false;
    suggestModelTier(name).then((res) => {
      if (cancelled) return;
      // Only auto-apply if the user hasn't manually touched it yet.
      setForm((f) => {
        if (f.tier_source === "manual") return f;
        return { ...f, tier: res.tier, tier_source: res.source === "heuristic" ? "heuristic" : "default" };
      });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [form.model_name, editingId]);

  function pickModel(upstreamName: string) {
    setForm((f) => ({
      ...f,
      model_name: upstreamName,
      id: f.id_touched ? f.id : `${f.provider}/${upstreamName}`,
    }));
  }

  function openEdit(m: Model) {
    setEditingId(m.id);
    setAdding(false);
    setForm({
      id: m.id,
      id_touched: true,
      provider: m.provider,
      model_name: m.model_name,
      tags: m.tags.join(", "),
      tier: m.tier,
      notes: m.notes ?? "",
      tier_source: "manual",
    });
  }

  function cancelForm() {
    setAdding(false);
    setEditingId(null);
    setForm(emptyForm);
    setFilter("");
  }

  async function saveModel() {
    if (editingId) {
      try {
        await patchModel(editingId, {
          model_name: form.model_name.trim(),
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
          tier: form.tier,
          notes: form.notes,
        });
        toast.success(`Updated ${editingId}`);
        cancelForm();
        onRefresh();
      } catch (e) {
        toast.error("Update failed", { detail: e instanceof Error ? e.message : undefined });
      }
      return;
    }
    if (!form.id.trim() || !form.provider || !form.model_name.trim()) return;
    try {
      await postModel({
        id: form.id.trim(),
        provider: form.provider,
        model_name: form.model_name.trim(),
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        tier: form.tier,
        notes: form.notes,
      });
      const id = form.id.trim();
      toast.success(`Added model ${id}`);
      cancelForm();
      onRefresh();
    } catch (e) {
      toast.error("Add failed", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  const visibleFetched = filter.trim()
    ? fetchedModels.filter((m) => m.toLowerCase().includes(filter.trim().toLowerCase()))
    : fetchedModels;

  const discoveryError = currentDiscovery?.error ?? null;

  const routingMode = routing?.routing_mode ?? "fixed";
  const usingBuiltinEmbedder = !embModelId;

  async function setRoutingMode(mode: "fixed" | "auto") {
    try {
      await putRouting({ routing_mode: mode });
      onRefresh();
    } catch (e) {
      toast.error("Failed to set routing mode", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section-label">
        Models {roleSaving && <span style={{ color: "var(--fg-faint)", fontWeight: 400 }}>· saving…</span>}
      </div>
      {usingBuiltinEmbedder && (
        <div style={{ fontSize: 11, color: "var(--fg-faint)", marginBottom: 8 }}>
          GraphRAG is using the built-in local embedder (BAAI/bge-small-en-v1.5, 384-dim).
          Assign a model to <b>Embedding</b> below to override.
        </div>
      )}

      <div className="settings-card" style={{ padding: "10px 12px" }}>
        <div className="settings-card-row" style={{ alignItems: "center", gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>Auto-route new messages</div>
            <div style={{ fontSize: 11, color: "var(--fg-faint)", marginTop: 2 }}>
              {routingMode === "auto"
                ? "A local classifier picks the best model per turn based on tier + notes."
                : "All turns use the selected model. Toggle on to let the router pick per turn."}
            </div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={routingMode === "auto"}
            className={`model-role-badge${routingMode === "auto" ? " model-role-badge--active" : ""}`}
            onClick={() => setRoutingMode(routingMode === "auto" ? "fixed" : "auto")}
          >
            {routingMode === "auto" ? "On" : "Off"}
          </button>
        </div>
      </div>

      {models.map((m) => {
        if (editingId === m.id) return null;
        const roles = getModelRoles(m);
        const isEmb = roles.includes("embedding");
        const isExt = roles.includes("extraction");
        const locked = hasRole(m);
        return (
          <div key={m.id} className="settings-card">
            <div className="settings-card-row">
              <div className="settings-card-info">
                <div className="settings-model-header">
                  <span className="settings-model-id">{m.id}</span>
                  <span className="settings-model-provider">{m.provider}</span>
                  <span className={`model-tier-chip model-tier-chip--${m.tier}`}>{m.tier}</span>
                </div>
                {m.notes && <div className="settings-model-notes">{m.notes}</div>}
                <div className="settings-tag-row">
                  {m.tags.map((t) => (
                    <span key={t} className="settings-tag-chip">{t}</span>
                  ))}
                </div>
                <div className="model-role-badges">
                  <button
                    type="button"
                    className={`model-role-badge ${isEmb ? "model-role-badge--active" : ""} ${!canEmbed(m) && !isEmb ? "model-role-badge--disabled" : ""}`}
                    onClick={() => {
                      if (isEmb) unassignRole("embedding");
                      else if (canEmbed(m)) assignRole("embedding", m.id);
                    }}
                    disabled={(!isEmb && !canEmbed(m)) || roleSaving}
                    title={isEmb ? "Click to clear (falls back to built-in fastembed)" : canEmbed(m) ? "Set as embedding model" : "Incompatible provider"}
                  >
                    Embedding
                  </button>
                  <button
                    type="button"
                    className={`model-role-badge ${isExt ? "model-role-badge--active" : ""}`}
                    onClick={() => {
                      if (isExt) unassignRole("extraction");
                      else assignRole("extraction", m.id);
                    }}
                    disabled={roleSaving}
                    title={isExt ? "Click to clear extraction model" : "Set as extraction model"}
                  >
                    Extraction
                  </button>
                </div>
              </div>
              <div className="settings-card-actions">
                <button
                  className="settings-icon-btn"
                  title="Edit"
                  onClick={() => openEdit(m)}
                >
                  ✎
                </button>
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

      {(adding || editingId) ? (
        <div className="settings-card settings-inline-form">
          {!editingId && (
            <>
              <div className="settings-field">
                <label className="settings-field-label">1. Provider</label>
                <select
                  className="settings-select"
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
            </>
          )}

          {(editingId || (form.provider && form.model_name)) && (
            <>
              {!editingId && (
                <div className="settings-field">
                  <label className="settings-field-label">3. Model id (internal)</label>
                  <input
                    className="settings-input"
                    value={form.id}
                    onChange={(e) => setForm((f) => ({ ...f, id: e.target.value, id_touched: true }))}
                    placeholder={`${form.provider}/${form.model_name}`}
                  />
                  <span className="settings-field-hint">
                    Used internally as the model identifier. Locked after creation.
                  </span>
                </div>
              )}
              {editingId && (
                <div className="settings-field">
                  <label className="settings-field-label">Model name</label>
                  <input
                    className="settings-input"
                    value={form.model_name}
                    onChange={(e) => setForm((f) => ({ ...f, model_name: e.target.value }))}
                  />
                </div>
              )}
              <div className="settings-field">
                <label className="settings-field-label">Tier</label>
                <div style={{ display: "flex", gap: 6 }}>
                  {TIERS.map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={`model-tier-chip model-tier-chip--${t}${form.tier === t ? " model-tier-chip--active" : ""}`}
                      onClick={() => setForm((f) => ({ ...f, tier: t, tier_source: "manual" }))}
                    >
                      {t}
                    </button>
                  ))}
                </div>
                <span className="settings-field-hint">
                  {form.tier_source === "heuristic"
                    ? "Suggested from the model name — adjust if needed."
                    : form.tier_source === "default"
                      ? "Unknown model — defaulting to balanced. Edit if needed."
                      : "Used by the auto-router to balance cost vs capability."}
                </span>
              </div>
              <div className="settings-field">
                <label className="settings-field-label">Notes (optional)</label>
                <input
                  className="settings-input"
                  value={form.notes}
                  onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
                  placeholder="e.g. no tool use, Portuguese-fluent, image input"
                />
                <span className="settings-field-hint">
                  Free text shown to the auto-router — list limitations or strengths.
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
            </>
          )}

          <div className="settings-row settings-row--end">
            <button className="settings-btn settings-btn--ghost" onClick={cancelForm}>
              Cancel
            </button>
            <button
              className="settings-btn settings-btn--primary"
              onClick={saveModel}
              disabled={!editingId && (!form.id.trim() || !form.provider || !form.model_name.trim())}
            >
              {editingId ? "Save" : "Add model"}
            </button>
          </div>
        </div>
      ) : (
        <button className="settings-add-btn" onClick={() => { setAdding(true); setEditingId(null); setForm(emptyForm); }}>
          + Add model
        </button>
      )}
    </div>
  );
}
