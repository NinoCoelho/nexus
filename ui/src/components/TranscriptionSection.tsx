import { useCallback, useEffect, useState } from "react";
import { getConfig, patchConfig, type TranscriptionConfig } from "../api";

const DEFAULT_CFG: TranscriptionConfig = {
  mode: "local",
  model: "base",
  language: "",
  device: "auto",
  compute_type: "int8",
  remote: { base_url: "", api_key_env: "", model: "whisper-1" },
};

const WHISPER_SIZES = ["tiny", "base", "small", "medium", "large-v3"] as const;
const DEVICES = ["auto", "cpu", "cuda"] as const;
const COMPUTE_TYPES = ["int8", "int8_float16", "float16", "float32"] as const;

export default function TranscriptionSection() {
  const [cfg, setCfg] = useState<TranscriptionConfig>(DEFAULT_CFG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const c = await getConfig();
      if (c.transcription) setCfg({ ...DEFAULT_CFG, ...c.transcription, remote: { ...DEFAULT_CFG.remote, ...(c.transcription.remote ?? {}) } });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load transcription config");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = useCallback(async (next: TranscriptionConfig) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await patchConfig({ transcription: next });
      if (updated.transcription) {
        setCfg({ ...DEFAULT_CFG, ...updated.transcription, remote: { ...DEFAULT_CFG.remote, ...(updated.transcription.remote ?? {}) } });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }, []);

  const update = (patch: Partial<TranscriptionConfig>) => {
    const next = { ...cfg, ...patch };
    setCfg(next);
    void save(next);
  };

  const updateRemote = (patch: Partial<TranscriptionConfig["remote"]>) => {
    const next = { ...cfg, remote: { ...cfg.remote, ...patch } };
    setCfg(next);
    void save(next);
  };

  return (
    <section className="settings-section">
      <div className="settings-section-label">Transcription</div>

      <div className="settings-row">
        <span className="settings-row-name">Mode</span>
        <div className="seg-control">
          <button
            className={`seg-btn${cfg.mode === "local" ? " seg-btn--active" : ""}`}
            onClick={() => update({ mode: "local" })}
            disabled={saving}
          >Local</button>
          <button
            className={`seg-btn${cfg.mode === "remote" ? " seg-btn--active" : ""}`}
            onClick={() => update({ mode: "remote" })}
            disabled={saving}
          >Remote</button>
        </div>
      </div>

      {cfg.mode === "local" && (
        <>
          <div className="settings-row">
            <span className="settings-row-name">Model</span>
            <select
              className="settings-select"
              value={WHISPER_SIZES.includes(cfg.model as typeof WHISPER_SIZES[number]) ? cfg.model : "__custom"}
              onChange={(e) => {
                if (e.target.value === "__custom") return;
                update({ model: e.target.value });
              }}
              disabled={saving}
            >
              {WHISPER_SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
              <option value="__custom">Custom…</option>
            </select>
          </div>
          {!WHISPER_SIZES.includes(cfg.model as typeof WHISPER_SIZES[number]) && (
            <div className="settings-row">
              <span className="settings-row-name">Custom model</span>
              <input
                className="settings-input"
                value={cfg.model}
                onChange={(e) => setCfg({ ...cfg, model: e.target.value })}
                onBlur={() => save(cfg)}
                placeholder="faster-whisper model id or path"
                disabled={saving}
              />
            </div>
          )}
          <div className="settings-row">
            <span className="settings-row-name">Device</span>
            <select
              className="settings-select"
              value={cfg.device}
              onChange={(e) => update({ device: e.target.value as TranscriptionConfig["device"] })}
              disabled={saving}
            >
              {DEVICES.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
          <div className="settings-row">
            <span className="settings-row-name">Compute type</span>
            <select
              className="settings-select"
              value={cfg.compute_type}
              onChange={(e) => update({ compute_type: e.target.value })}
              disabled={saving}
            >
              {COMPUTE_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </>
      )}

      {cfg.mode === "remote" && (
        <>
          <div className="settings-row">
            <span className="settings-row-name">Base URL</span>
            <input
              className="settings-input"
              value={cfg.remote.base_url}
              onChange={(e) => setCfg({ ...cfg, remote: { ...cfg.remote, base_url: e.target.value } })}
              onBlur={() => save(cfg)}
              placeholder="https://api.openai.com/v1"
              disabled={saving}
            />
          </div>
          <div className="settings-row">
            <span className="settings-row-name">API key env</span>
            <input
              className="settings-input"
              value={cfg.remote.api_key_env}
              onChange={(e) => setCfg({ ...cfg, remote: { ...cfg.remote, api_key_env: e.target.value } })}
              onBlur={() => save(cfg)}
              placeholder="OPENAI_API_KEY"
              disabled={saving}
            />
          </div>
          <div className="settings-row">
            <span className="settings-row-name">Model</span>
            <input
              className="settings-input"
              value={cfg.remote.model}
              onChange={(e) => updateRemote({ model: e.target.value })}
              placeholder="whisper-1"
              disabled={saving}
            />
          </div>
        </>
      )}

      <div className="settings-row">
        <span className="settings-row-name">Language</span>
        <input
          className="settings-input"
          value={cfg.language}
          onChange={(e) => setCfg({ ...cfg, language: e.target.value })}
          onBlur={() => save(cfg)}
          placeholder="auto (e.g. en, pt, es)"
          disabled={saving}
        />
      </div>

      {loading && <p className="settings-info">Loading…</p>}
      {error && <p className="settings-error">{error}</p>}
    </section>
  );
}
