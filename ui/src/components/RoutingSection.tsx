import { useState } from "react";
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

  const dirty =
    mode !== routing.mode ||
    (mode === "fixed" && defaultModel !== routing.default_model);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await putRouting({ mode, default_model: mode === "fixed" ? defaultModel : undefined });
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-section">
      <div className="settings-section-label">Routing</div>

      <div className="settings-row">
        <span className="settings-row-name">Routing mode</span>
        <div className="seg-control">
          <button
            className={`seg-btn${mode === "fixed" ? " seg-btn--active" : ""}`}
            onClick={() => setMode("fixed")}
          >
            Fixed
          </button>
          <button
            className={`seg-btn${mode === "auto" ? " seg-btn--active" : ""}`}
            onClick={() => setMode("auto")}
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
            onChange={(e) => setDefaultModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
                {m.tags.length ? ` [${m.tags.join(", ")}]` : ""}
              </option>
            ))}
          </select>
        </div>
      )}

      {mode === "auto" && (
        <p className="settings-info">
          Nexus picks the best configured model for each task based on tags and
          strengths. Experimental.
        </p>
      )}

      {error && <p className="settings-error">{error}</p>}

      <div className="settings-row settings-row--end">
        <button
          className="settings-btn settings-btn--primary"
          disabled={!dirty || saving}
          onClick={handleSave}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
