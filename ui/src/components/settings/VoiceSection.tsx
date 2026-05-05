import { useCallback, useEffect, useState } from "react";
import {
  getConfig,
  patchConfig,
  type TTSConfig,
} from "../../api/config";
import { invalidateTTSConfigCache } from "../../hooks/useTTS";

const DEFAULT_CFG: TTSConfig = {
  enabled: true,
  ack_enabled: true,
  ack_mode: "voice",
  voices_dir: "",
};

export default function VoiceSection() {
  const [cfg, setCfg] = useState<TTSConfig>(DEFAULT_CFG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getConfig();
      if (c.tts) setCfg({ ...DEFAULT_CFG, ...c.tts });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load TTS config");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const save = useCallback(async (next: TTSConfig) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await patchConfig({ tts: next });
      if (updated.tts) setCfg({ ...DEFAULT_CFG, ...updated.tts });
      invalidateTTSConfigCache();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }, []);

  const update = (patch: Partial<TTSConfig>) => {
    const next = { ...cfg, ...patch };
    setCfg(next);
    void save(next);
  };

  return (
    <section className="settings-section">
      <div className="settings-section-label">Voice & speech</div>

      <div className="settings-row">
        <span className="settings-row-name">Click-to-listen</span>
        <button
          className={`hitl-switch${cfg.enabled ? " on" : ""}`}
          onClick={() => update({ enabled: !cfg.enabled })}
          aria-pressed={cfg.enabled}
          disabled={saving}
        >
          <span className="hitl-switch-knob" />
        </button>
      </div>

      <div className="settings-row">
        <span className="settings-row-name">Voice acknowledgments</span>
        <button
          className={`hitl-switch${cfg.ack_enabled ? " on" : ""}`}
          onClick={() => update({ ack_enabled: !cfg.ack_enabled })}
          aria-pressed={cfg.ack_enabled}
          disabled={saving}
        >
          <span className="hitl-switch-knob" />
        </button>
      </div>

      {cfg.ack_enabled && (
        <div className="settings-row">
          <span className="settings-row-name">
            Always announce
            <span className="settings-row-hint" style={{ display: "block", fontSize: 12, opacity: 0.6, marginTop: 2 }}>
              Speak responses even when you type
            </span>
          </span>
          <button
            className={`hitl-switch${cfg.ack_mode === "always" ? " on" : ""}`}
            onClick={() => update({ ack_mode: cfg.ack_mode === "always" ? "voice" : "always" })}
            aria-pressed={cfg.ack_mode === "always"}
            disabled={saving}
          >
            <span className="hitl-switch-knob" />
          </button>
        </div>
      )}

      {loading && <p className="settings-info">Loading…</p>}
      {error && <p className="settings-error">{error}</p>}
    </section>
  );
}
