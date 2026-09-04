import { useCallback } from "react";
import { X } from "lucide-react";
import {
  type GraphSettings,
  DEFAULT_GRAPH_SETTINGS,
  GRAPH_SETTINGS_FIELDS,
} from "./graphSettings";

interface Props {
  settings: GraphSettings;
  onChange: (s: GraphSettings) => void;
  onClose: () => void;
}

export function GraphSettingsPanel({ settings, onChange, onClose }: Props) {
  const update = useCallback(
    (key: Exclude<keyof GraphSettings, "renderer">, value: number) => {
      onChange({ ...settings, [key]: value });
    },
    [settings, onChange],
  );

  const reset = useCallback(() => {
    onChange({ ...DEFAULT_GRAPH_SETTINGS });
  }, [onChange]);

  return (
    <div
      className="ug-settings-panel"
      onClick={(e) => e.stopPropagation()}
      role="dialog"
      aria-label="Graph display settings"
    >
      <div className="ug-settings-header">
        <span className="ug-settings-title">Display Settings</span>
        <div style={{ display: "flex", gap: 4 }}>
          <button className="ug-settings-reset" onClick={reset} title="Reset to defaults">
            Reset
          </button>
          <button className="ug-settings-close" onClick={onClose} aria-label="Close settings">
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="ug-settings-body">
        <div className="ug-settings-row ug-settings-row--renderer">
          <span className="ug-settings-label">Renderer</span>
          <div className="ug-settings-renderer-toggle">
            <button
              className={`ug-settings-renderer-btn${settings.renderer === "2d" ? " ug-settings-renderer-btn--active" : ""}`}
              onClick={() => onChange({ ...settings, renderer: "2d" })}
              title="2D canvas — pan, zoom, drag nodes. No WebGL required."
            >
              2D
            </button>
            <button
              className={`ug-settings-renderer-btn${settings.renderer === "3d" ? " ug-settings-renderer-btn--active" : ""}`}
              onClick={() => onChange({ ...settings, renderer: "3d" })}
              title="3D WebGL — orbit camera. Heavier on large graphs."
            >
              3D
            </button>
          </div>
          <span className="ug-settings-value">
            {settings.renderer === "2d" ? "flat" : "orbit"}
          </span>
        </div>
        {GRAPH_SETTINGS_FIELDS.map((field) => (
          <label key={field.key} className="ug-settings-row">
            <span className="ug-settings-label">{field.label}</span>
            <input
              type="range"
              className="ug-settings-slider"
              min={field.min}
              max={field.max}
              step={field.step}
              value={settings[field.key]}
              onChange={(e) => update(field.key, parseFloat(e.target.value))}
            />
            <span className="ug-settings-value">
              {field.format(settings[field.key])}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
