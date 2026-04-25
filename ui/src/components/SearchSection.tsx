import { useEffect, useState } from "react";
import { getConfig, patchConfig, type SearchConfig, type SearchProviderEntry } from "../api";
import "./SearchSection.css";

const TYPES: { value: string; label: string; needsKey: boolean }[] = [
  { value: "ddgs", label: "DuckDuckGo (no key)", needsKey: false },
  { value: "brave", label: "Brave Search", needsKey: true },
  { value: "tavily", label: "Tavily", needsKey: true },
];

export default function SearchSection() {
  const [cfg, setCfg] = useState<SearchConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getConfig()
      .then((c) => setCfg(c.search ?? null))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function commit(next: SearchConfig) {
    setSaving(true);
    setError(null);
    setCfg(next);
    try {
      const updated = await patchConfig({ search: next });
      if (updated.search) setCfg(updated.search);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save search config");
    } finally {
      setSaving(false);
    }
  }

  if (!cfg) {
    return (
      <section className="search-section">
        <h3 className="search-section-title">Web search</h3>
        {error ? <p className="settings-error">{error}</p> : <p className="settings-loading">Loading…</p>}
      </section>
    );
  }

  const updateProvider = (idx: number, patch: Partial<SearchProviderEntry>) => {
    const providers = cfg.providers.map((p, i) => (i === idx ? { ...p, ...patch } : p));
    void commit({ ...cfg, providers });
  };
  const removeProvider = (idx: number) => {
    void commit({ ...cfg, providers: cfg.providers.filter((_, i) => i !== idx) });
  };
  const addProvider = () => {
    void commit({
      ...cfg,
      providers: [...cfg.providers, { type: "ddgs", key_env: "", timeout: 10 }],
    });
  };

  return (
    <section className="search-section">
      <h3 className="search-section-title">Web search</h3>
      <div className="search-row">
        <label className="search-row-label">
          <input
            type="checkbox"
            checked={cfg.enabled}
            disabled={saving}
            onChange={(e) => commit({ ...cfg, enabled: e.target.checked })}
          />
          Enable web_search tool
        </label>
        <p className="search-row-desc">
          Tool reload requires a server restart for changes here to take effect.
        </p>
      </div>

      <div className="search-providers">
        {cfg.providers.length === 0 && (
          <p className="search-empty">No providers configured.</p>
        )}
        {cfg.providers.map((p, idx) => {
          const meta = TYPES.find((t) => t.value === p.type);
          return (
            <div key={idx} className={`search-provider${p.ready ? "" : " is-stale"}`}>
              <select
                value={p.type}
                disabled={saving}
                onChange={(e) => updateProvider(idx, { type: e.target.value })}
              >
                {TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
              {meta?.needsKey && (
                <input
                  type="text"
                  placeholder="API key env var (e.g. BRAVE_API_KEY)"
                  value={p.key_env}
                  disabled={saving}
                  onChange={(e) => updateProvider(idx, { key_env: e.target.value })}
                />
              )}
              <span className={`search-status${p.ready ? " ok" : ""}`}>
                {p.ready ? "ready" : meta?.needsKey ? "set env var" : "—"}
              </span>
              <button
                type="button"
                className="settings-btn"
                onClick={() => removeProvider(idx)}
                disabled={saving}
              >
                Remove
              </button>
            </div>
          );
        })}
        <button
          type="button"
          className="settings-btn settings-btn--primary"
          onClick={addProvider}
          disabled={saving}
        >
          Add provider
        </button>
      </div>
      {error && <p className="settings-error">{error}</p>}
    </section>
  );
}
