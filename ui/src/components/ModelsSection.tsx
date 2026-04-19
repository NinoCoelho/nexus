import { useState, useCallback } from "react";
import {
  deleteModel,
  postModel,
  fetchProviderModels,
  type Model,
  type ModelStrengths,
  type Provider,
} from "../api";

interface Props {
  models: Model[];
  providers: Provider[];
  onRefresh: () => void;
}

interface AddForm {
  id: string;
  provider: string;
  model_name: string;
  tags: string;
  strengths: ModelStrengths;
}

interface ModelCache {
  models: string[];
  fetchedAt: number;
}

const CACHE_TTL_MS = 30_000;
const emptyStrengths: ModelStrengths = { speed: 5, cost: 5, reasoning: 5, coding: 5 };
const emptyForm: AddForm = { id: "", provider: "", model_name: "", tags: "", strengths: emptyStrengths };

export default function ModelsSection({ models, providers, onRefresh }: Props) {
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<AddForm>(emptyForm);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [modelCache, setModelCache] = useState<Record<string, ModelCache>>({});
  const [fetchingModels, setFetchingModels] = useState(false);

  const cachedModels = form.provider
    ? (modelCache[form.provider]?.models ?? [])
    : [];

  const doFetchModels = useCallback(async (provider: string, force = false) => {
    if (!provider) return;
    const cached = modelCache[provider];
    if (!force && cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) return;
    setFetchingModels(true);
    try {
      const result = await fetchProviderModels(provider);
      if (result.ok) {
        setModelCache((c) => ({
          ...c,
          [provider]: { models: result.models, fetchedAt: Date.now() },
        }));
      }
    } catch {
      // silently ignore — user can still type freely
    } finally {
      setFetchingModels(false);
    }
  }, [modelCache]);

  async function addModel() {
    if (!form.id.trim() || !form.provider || !form.model_name.trim()) return;
    setError(null);
    try {
      await postModel({
        id: form.id.trim(),
        provider: form.provider,
        model_name: form.model_name.trim(),
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        strengths: form.strengths,
      });
      setAdding(false);
      setForm(emptyForm);
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Add failed");
    }
  }

  async function removeModel(id: string) {
    setError(null);
    try {
      await deleteModel(id);
      setConfirmRemove(null);
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Remove failed");
    }
  }

  const datalistId = form.provider ? `model-list-${form.provider}` : undefined;

  return (
    <div className="settings-section">
      <div className="settings-section-label">Models</div>

      {models.map((m) => (
        <div key={m.id} className="settings-card">
          <div className="settings-card-row">
            <div className="settings-card-info">
              <span className="settings-model-id">{m.id}</span>
              <span className="settings-model-provider">{m.provider}</span>
              <div className="settings-tag-row">
                {m.tags.map((t) => (
                  <span key={t} className="settings-tag-chip">{t}</span>
                ))}
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
              {confirmRemove === m.id ? (
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
      ))}

      {error && <p className="settings-error">{error}</p>}

      {adding ? (
        <div className="settings-card settings-inline-form">
          <div className="settings-field">
            <label className="settings-field-label">Model id</label>
            <input
              className="settings-input"
              value={form.id}
              onChange={(e) => setForm((f) => ({ ...f, id: e.target.value }))}
              placeholder="openai/gpt-4o-mini"
              autoFocus
            />
          </div>
          <div className="settings-field">
            <label className="settings-field-label">Provider</label>
            <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
              <select
                className="settings-select"
                style={{ flex: 1 }}
                value={form.provider}
                onChange={(e) => {
                  const p = e.target.value;
                  setForm((f) => ({ ...f, provider: p }));
                  if (p) doFetchModels(p);
                }}
              >
                <option value="">Select provider…</option>
                {providers.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}{p.type ? ` (${p.type})` : ""}
                  </option>
                ))}
              </select>
              <button
                className="settings-icon-btn"
                title="Refresh model list"
                disabled={!form.provider || fetchingModels}
                onClick={() => form.provider && doFetchModels(form.provider, true)}
                type="button"
              >
                {fetchingModels ? "…" : "↻"}
              </button>
            </div>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">Upstream model name</label>
            {datalistId && (
              <datalist id={datalistId}>
                {cachedModels.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            )}
            <input
              className="settings-input"
              list={datalistId}
              value={form.model_name}
              onChange={(e) => setForm((f) => ({ ...f, model_name: e.target.value }))}
              placeholder="gpt-4o-mini"
            />
            {cachedModels.length > 0 && form.provider && (
              <span className="settings-field-hint">
                {cachedModels.length} model(s) available from {form.provider}. Type to filter or enter a custom name.
              </span>
            )}
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
          <div className="settings-row settings-row--end">
            <button className="settings-btn settings-btn--ghost" onClick={() => { setAdding(false); setForm(emptyForm); }}>
              Cancel
            </button>
            <button
              className="settings-btn settings-btn--primary"
              onClick={addModel}
              disabled={!form.id.trim() || !form.provider || !form.model_name.trim()}
            >
              Add
            </button>
          </div>
        </div>
      ) : (
        <button className="settings-add-btn" onClick={() => { setAdding(true); setError(null); }}>
          + Add model
        </button>
      )}
    </div>
  );
}
