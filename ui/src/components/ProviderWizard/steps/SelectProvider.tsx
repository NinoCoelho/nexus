import { useMemo, useState } from "react";
import type { ProviderCatalogEntry } from "../../../api";

interface Props {
  catalog: ProviderCatalogEntry[];
  onPick: (entry: ProviderCatalogEntry) => void;
  /** Names of providers already configured. Shown with a "configured"
   *  hint so the user can tell them apart from fresh tiles. */
  configuredNames: string[];
}

const CATEGORY_LABEL: Record<string, string> = {
  frontier: "Frontier models",
  aggregator: "Aggregators",
  open: "Open models",
  cloud: "Cloud / IAM",
  local: "Local & self-hosted",
  other: "Other",
};

const CATEGORY_ORDER: ProviderCatalogEntry["category"][] = [
  "frontier",
  "aggregator",
  "open",
  "cloud",
  "local",
  "other",
];

export default function SelectProvider({ catalog, onPick, configuredNames }: Props) {
  const [query, setQuery] = useState("");
  const configured = useMemo(() => new Set(configuredNames), [configuredNames]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return catalog;
    return catalog.filter(
      (e) =>
        e.id.toLowerCase().includes(q) ||
        e.display_name.toLowerCase().includes(q),
    );
  }, [catalog, query]);

  const groups = useMemo(() => {
    const out: Partial<Record<ProviderCatalogEntry["category"], ProviderCatalogEntry[]>> = {};
    for (const e of filtered) {
      (out[e.category] ??= []).push(e);
    }
    return out;
  }, [filtered]);

  return (
    <div className="provider-wizard-step provider-wizard-step--select">
      <input
        className="form-input provider-wizard-search"
        placeholder="Search providers…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        autoFocus
      />
      {CATEGORY_ORDER.map((cat) => {
        const entries = groups[cat];
        if (!entries || entries.length === 0) return null;
        return (
          <section key={cat} className="provider-wizard-group">
            <h3 className="provider-wizard-group__title">{CATEGORY_LABEL[cat] ?? cat}</h3>
            <div className="provider-wizard-grid">
              {entries.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  className="provider-wizard-tile"
                  onClick={() => onPick(e)}
                >
                  <span className="provider-wizard-tile__name">{e.display_name}</span>
                  <span className="provider-wizard-tile__meta">
                    {e.runtime_kind.replace(/_/g, " ")}
                    {configured.has(e.id) && (
                      <span className="provider-wizard-tile__configured"> · configured</span>
                    )}
                  </span>
                </button>
              ))}
            </div>
          </section>
        );
      })}
      {filtered.length === 0 && (
        <p className="provider-wizard-empty">No providers match “{query}”.</p>
      )}
    </div>
  );
}
