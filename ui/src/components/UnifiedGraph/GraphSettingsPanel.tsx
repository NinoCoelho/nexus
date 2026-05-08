import { useCallback } from "react";
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
    (key: keyof GraphSettings, value: number) => {
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
            ×
          </button>
        </div>
      </div>
      <div className="ug-settings-body">
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
