// Sub-component for GraphView: scope/filter toolbar above the canvas.

import type { ScopeType } from "./types";

interface GraphToolbarProps {
  scope: ScopeType;
  seed: string;
  hops: number;
  edgeTypes: string;
  loading: boolean;
  nodeCount: number;
  edgeCount: number;
  entityCount: number;
  showFilters: boolean;
  allTags: string[];
  tagFilter: Set<string>;
  onScopeChange: (s: ScopeType) => void;
  onSeedChange: (s: string) => void;
  onHopsChange: (h: number) => void;
  onEdgeTypesChange: (e: string) => void;
  onToggleFilters: () => void;
  onFitToView: () => void;
  onFetchGraph: () => void;
  onTagFilterChange: (tags: Set<string>) => void;
}

const scopeLabels: Record<ScopeType, string> = {
  all: "All", file: "File", folder: "Folder", tag: "Tag", search: "Search", entity: "Entity",
};
const seedPlaceholders: Record<ScopeType, string> = {
  all: "", file: "path/to/file.md", folder: "folder/", tag: "tag-name", search: "search query…", entity: "entity ID",
};

export function GraphToolbar({
  scope,
  seed,
  hops,
  edgeTypes,
  loading,
  nodeCount,
  edgeCount,
  entityCount,
  showFilters,
  allTags,
  tagFilter,
  onScopeChange,
  onSeedChange,
  onHopsChange,
  onEdgeTypesChange,
  onToggleFilters,
  onFitToView,
  onFetchGraph,
  onTagFilterChange,
}: GraphToolbarProps) {
  return (
    <>
      <div className="graph-toolbar">
        <select
          className="graph-toolbar-select"
          value={scope}
          onChange={e => { onScopeChange(e.target.value as ScopeType); onSeedChange(""); }}
        >
          {Object.entries(scopeLabels).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>

        {scope !== "all" && (
          <input
            className="graph-toolbar-input"
            type="text"
            placeholder={seedPlaceholders[scope]}
            value={seed}
            onChange={e => onSeedChange(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") onFetchGraph(); }}
          />
        )}

        <select className="graph-toolbar-select graph-toolbar-select-sm" value={hops} onChange={e => onHopsChange(Number(e.target.value))}>
          <option value={1}>1 hop</option>
          <option value={2}>2 hops</option>
          <option value={3}>3 hops</option>
        </select>

        <select
          className="graph-toolbar-select graph-toolbar-select-sm"
          value={edgeTypes}
          onChange={e => onEdgeTypesChange(e.target.value)}
        >
          <option value="link">Links</option>
          <option value="link,tag">Links + Tags</option>
          <option value="link,entity">Links + Entities</option>
          <option value="link,tag,entity">All</option>
        </select>

        <button className="graph-toolbar-btn" onClick={onToggleFilters}>Tags</button>
        <button className="graph-toolbar-btn" onClick={onFitToView}>Fit</button>
        <button className="graph-toolbar-btn" onClick={onFetchGraph} disabled={loading}>
          {loading ? "…" : "Go"}
        </button>
        <span className="graph-toolbar-stat">{nodeCount} nodes</span>
        <span className="graph-toolbar-stat">{edgeCount} edges</span>
        {entityCount > 0 && <span className="graph-toolbar-stat">{entityCount} entities</span>}
      </div>

      {showFilters && allTags.length > 0 && (
        <div className="graph-filter-bar">
          {allTags.map(tag => (
            <button
              key={tag}
              className={`graph-tag-chip${tagFilter.has(tag) ? " active" : ""}`}
              onClick={() => {
                const next = new Set(tagFilter);
                if (next.has(tag)) next.delete(tag); else next.add(tag);
                onTagFilterChange(next);
              }}
            >
              {tag}
            </button>
          ))}
          {tagFilter.size > 0 && (
            <button className="graph-tag-chip" onClick={() => onTagFilterChange(new Set())}>
              clear
            </button>
          )}
        </div>
      )}
    </>
  );
}
