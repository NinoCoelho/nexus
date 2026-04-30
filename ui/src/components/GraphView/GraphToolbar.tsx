// Sub-component for GraphView: scope/filter toolbar above the canvas.

import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("graph");

  const scopeLabels: Record<ScopeType, string> = {
    all: t("graph:toolbar.scopes.all"),
    file: t("graph:toolbar.scopes.file"),
    folder: t("graph:toolbar.scopes.folder"),
    tag: t("graph:toolbar.scopes.tag"),
    search: t("graph:toolbar.scopes.search"),
    entity: t("graph:toolbar.scopes.entity"),
  };
  const seedPlaceholders: Record<ScopeType, string> = {
    all: "",
    file: t("graph:toolbar.seedPlaceholders.file"),
    folder: t("graph:toolbar.seedPlaceholders.folder"),
    tag: t("graph:toolbar.seedPlaceholders.tag"),
    search: t("graph:toolbar.seedPlaceholders.search"),
    entity: t("graph:toolbar.seedPlaceholders.entity"),
  };

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
          <option value={1}>{t("graph:toolbar.hops.one")}</option>
          <option value={2}>{t("graph:toolbar.hops.two")}</option>
          <option value={3}>{t("graph:toolbar.hops.three")}</option>
        </select>

        <select
          className="graph-toolbar-select graph-toolbar-select-sm"
          value={edgeTypes}
          onChange={e => onEdgeTypesChange(e.target.value)}
        >
          <option value="link">{t("graph:toolbar.edgeTypes.links")}</option>
          <option value="link,tag">{t("graph:toolbar.edgeTypes.linksAndTags")}</option>
          <option value="link,entity">{t("graph:toolbar.edgeTypes.linksAndEntities")}</option>
          <option value="link,tag,entity">{t("graph:toolbar.edgeTypes.all")}</option>
        </select>

        <button className="graph-toolbar-btn" onClick={onToggleFilters}>{t("graph:toolbar.tagsButton")}</button>
        <button className="graph-toolbar-btn" onClick={onFitToView}>{t("graph:toolbar.fitButton")}</button>
        <button className="graph-toolbar-btn" onClick={onFetchGraph} disabled={loading}>
          {loading ? t("graph:toolbar.loadingButton") : t("graph:toolbar.goButton")}
        </button>
        <span className="graph-toolbar-stat">{t("graph:toolbar.nodes", { count: nodeCount })}</span>
        <span className="graph-toolbar-stat">{t("graph:toolbar.edges", { count: edgeCount })}</span>
        {entityCount > 0 && <span className="graph-toolbar-stat">{t("graph:toolbar.entities", { count: entityCount })}</span>}
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
              {t("graph:toolbar.clearTags")}
            </button>
          )}
        </div>
      )}
    </>
  );
}
