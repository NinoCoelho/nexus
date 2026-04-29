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
  return (
    <div className="settings-card settings-inline-form">
      {!editingId && (
        <>
          <div className="settings-field">
            <label className="settings-field-label">1. Provider</label>
            <select
              className="settings-select"
              value={form.provider}
              onChange={(e) => onProviderChange(e.target.value)}
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
                  onClick={() => onFetchModels(form.provider, true)}
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
                    onChange={(e) => onFilterChange(e.target.value)}
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
                          onClick={() => onPickModel(m)}
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
                  onChange={(e) => onFormChange({
                    model_name: e.target.value,
                    id: form.id_touched ? form.id : (form.provider ? `${form.provider}/${e.target.value}` : form.id),
                  })}
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
                onChange={(e) => onFormChange({ id: e.target.value, id_touched: true })}
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
                onChange={(e) => onFormChange({ model_name: e.target.value })}
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
                  onClick={() => onFormChange({ tier: t, tier_source: "manual" })}
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
              onChange={(e) => onFormChange({ notes: e.target.value })}
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
              onChange={(e) => onFormChange({ tags: e.target.value })}
              placeholder="fast, cheap"
            />
          </div>
          <div className="settings-field">
            <label className="settings-field-label" style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={form.is_embedding_capable}
                onChange={(e) => onFormChange({ is_embedding_capable: e.target.checked })}
              />
              Embedding capable
            </label>
            <span className="settings-field-hint">
              Mark this model as an embedding model (e.g. all-MiniLM, bge, nomic-embed).
              Only models with this enabled can be assigned the Embedding role.
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">Context size (n_ctx)</label>
            <input
              className="settings-input"
              type="number"
              min={0}
              step={1024}
              value={form.context_window}
              onChange={(e) => onFormChange({ context_window: e.target.value })}
              placeholder="0 = server default"
            />
            <span className="settings-field-hint">
              For local GGUF models, passed to llama-server as <code>--ctx-size</code> at start.
              Stop/Start the model to apply changes. Use ≥4096 if assigning to GraphRAG extraction
              (smaller contexts cause extraction failures).
            </span>
          </div>
          <div className="settings-field">
            <label className="settings-field-label">Max output tokens</label>
            <input
              className="settings-input"
              type="number"
              min={0}
              step={1024}
              value={form.max_output_tokens}
              onChange={(e) => onFormChange({ max_output_tokens: e.target.value })}
              placeholder="0 = use global default"
            />
            <span className="settings-field-hint">
              Per-call output cap forwarded as <code>max_tokens</code>. Overrides the
              global default in Advanced settings. 0 inherits the global value.
            </span>
          </div>
        </>
      )}

      <div className="settings-row settings-row--end">
        <button className="settings-btn settings-btn--ghost" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="settings-btn settings-btn--primary"
          onClick={onSave}
          disabled={!editingId && (!form.id.trim() || !form.provider || !form.model_name.trim())}
        >
          {editingId ? "Save" : "Add model"}
        </button>
      </div>
    </div>
  );
}
