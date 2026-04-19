import { useEffect, useState } from "react";
import { putRouting, type Model, type RoutingConfig } from "../api";

interface Props {
  routing: RoutingConfig;
  models: Model[];
  onRefresh: () => void;
}

export default function RoutingSection({ routing, models, onRefresh }: Props) {
  const [mode, setMode] = useState<"fixed" | "auto">(routing.mode);
  const [defaultModel, setDefaultModel] = useState(routing.default_model);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sync local state with server-side routing whenever the prop changes.
  useEffect(() => {
    setMode(routing.mode);
    setDefaultModel(routing.default_model);
  }, [routing.mode, routing.default_model]);

  async function persist(patch: { mode?: "fixed" | "auto"; default_model?: string }) {
    setSaving(true);
    setError(null);
    try {
      await putRouting(patch);
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function handleModeChange(next: "fixed" | "auto") {
    if (next === mode) return;
    setMode(next);
    persist({ mode: next, default_model: next === "fixed" ? defaultModel : undefined });
  }

  function handleDefaultModelChange(next: string) {
    if (next === defaultModel) return;
    setDefaultModel(next);
    if (next) persist({ default_model: next });
  }

  return (
    <div className="settings-section">
      <div className="settings-section-label">
        Routing {saving && <span style={{ color: "var(--fg-faint)", fontWeight: 400 }}>· saving…</span>}
      </div>

      <div className="settings-row">
        <span className="settings-row-name">Routing mode</span>
        <div className="seg-control">
          <button
            className={`seg-btn${mode === "fixed" ? " seg-btn--active" : ""}`}
            onClick={() => handleModeChange("fixed")}
            type="button"
          >
            Fixed
          </button>
          <button
            className={`seg-btn${mode === "auto" ? " seg-btn--active" : ""}`}
            onClick={() => handleModeChange("auto")}
            type="button"
          >
            Auto
          </button>
        </div>
      </div>

      {mode === "fixed" && (
        <div className="settings-row settings-row--col">
          <label className="settings-row-name" htmlFor="default-model-select">
            Default model
          </label>
          <select
            id="default-model-select"
            className="settings-select"
            value={defaultModel}
            onChange={(e) => handleDefaultModelChange(e.target.value)}
            disabled={models.length === 0}
          >
            <option value="" disabled>
              {models.length === 0 ? "No models configured" : "Select a model…"}
            </option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
                {m.tags.length ? ` [${m.tags.join(", ")}]` : ""}
              </option>
            ))}
          </select>
          {models.length === 0 && (
            <span className="settings-field-hint">
              Add a model in the section below, then it will appear here.
            </span>
          )}
        </div>
      )}

      {mode === "auto" && (
        <p className="settings-info">
          Nexus picks the best configured model for each task based on tags and
          strengths. Experimental.
        </p>
      )}

      {error && <p className="settings-error">{error}</p>}
    </div>
  );
}
