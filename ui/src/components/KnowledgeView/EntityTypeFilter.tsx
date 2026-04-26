// Entity type filter pills for KnowledgeView — each pill is also a color legend swatch.

import { typeColor } from "./typeColors";
import type { KnowledgeStats } from "../../api";

interface EntityTypeFilterProps {
  stats: KnowledgeStats | null;
  typeFilter: string | null;
  onTypeFilterChange: (t: string | null) => void;
}

export function EntityTypeFilter({ stats, typeFilter, onTypeFilterChange }: EntityTypeFilterProps) {
  const entityTypes = stats?.types ? Object.keys(stats.types) : [];

  return (
    <>
      <button
        className={`kv-pill${typeFilter === null ? " kv-pill--active" : ""}`}
        onClick={() => onTypeFilterChange(null)}
      >
        All
      </button>
      {entityTypes.map((t) => (
        <button
          key={t}
          className={`kv-pill${typeFilter === t ? " kv-pill--active" : ""}`}
          style={{ "--pill-color": typeColor(t) } as React.CSSProperties}
          onClick={() => onTypeFilterChange(typeFilter === t ? null : t)}
        >
          <span
            className="kv-pill-swatch"
            style={{ background: typeColor(t) }}
          />
          {t} <span className="kv-pill-count">{stats?.types[t] ?? 0}</span>
        </button>
      ))}
    </>
  );
}
