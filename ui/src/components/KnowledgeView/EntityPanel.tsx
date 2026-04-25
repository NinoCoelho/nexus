// Sub-component for KnowledgeView: left panel with entity list or search results.

import type { KnowledgeEntity, KnowledgeQueryResult, EntityDetail } from "../../api";
import { typeColor } from "./utils";
import { EntityDetailCard } from "./EntityDetailCard";

interface EntityPanelProps {
  hasResults: boolean;
  loading: boolean;
  queryResult: KnowledgeQueryResult | null;
  topEntities: KnowledgeEntity[];
  typeFilter: string | null;
  entityFilter: string;
  onEntityFilterChange: (v: string) => void;
  selectedEntity: EntityDetail | null;
  pinnedEntities: EntityDetail[];
  onSelectEntity: (id: number) => void;
  onPreviewPath: (path: string) => void;
  onPinEntity: (detail: EntityDetail) => void;
  onUnpinEntity: (entityId: number) => void;
  onCloseSelected: () => void;
  isPinned: (entityId: number) => boolean;
}

export function EntityPanel({
  hasResults,
  loading,
  queryResult,
  topEntities,
  typeFilter,
  entityFilter,
  onEntityFilterChange,
  selectedEntity,
  pinnedEntities,
  onSelectEntity,
  onPreviewPath,
  onPinEntity,
  onUnpinEntity,
  onCloseSelected,
  isPinned,
}: EntityPanelProps) {
  return (
    <>
      <div className="kv-evidence-top">
        {!hasResults && !loading && (
          <div className="kv-landing">
            <div className="kv-landing-header">
              <h3 className="kv-landing-title">Top Entities</h3>
              <input
                className="kv-entity-filter"
                type="text"
                placeholder="Filter…"
                value={entityFilter}
                onChange={(e) => onEntityFilterChange(e.target.value)}
              />
            </div>
            <div className="kv-entity-grid">
              {topEntities
                .filter((e) => {
                  if (typeFilter !== null && e.type !== typeFilter) return false;
                  if (entityFilter && !e.name.toLowerCase().includes(entityFilter.toLowerCase())) return false;
                  return true;
                })
                .map((e) => (
                  <button
                    key={e.id}
                    className="kv-entity-card"
                    onClick={() => void onSelectEntity(e.id)}
                  >
                    <span className="kv-entity-dot" style={{ background: typeColor(e.type) }} />
                    <span className="kv-entity-name">{e.name}</span>
                    <span className="kv-entity-type">{e.type}</span>
                    <span className="kv-entity-degree">{e.degree}</span>
                  </button>
                ))}
            </div>
          </div>
        )}

        {loading && <div className="kv-loading">Searching...</div>}

        {hasResults && queryResult && (
          <div className="kv-results">
            {queryResult.results.map((r, i) => (
              <div key={r.chunk_id + i} className="kv-evidence-card">
                <div className="kv-evidence-header">
                  <button
                    className="kv-evidence-source"
                    onClick={() => onPreviewPath(r.source_path)}
                  >
                    {r.source_path} &rsaquo; {r.heading}
                  </button>
                  <span className={`kv-evidence-badge kv-evidence-badge--${r.source}`}>
                    {r.source}
                  </span>
                  <span className="kv-evidence-score">{(r.score * 100).toFixed(0)}%</span>
                </div>
                <p className="kv-evidence-snippet">{r.content.slice(0, 300)}</p>
                {r.related_entities.length > 0 && (
                  <div className="kv-evidence-entities">
                    {r.related_entities.slice(0, 8).map((name) => (
                      <span key={name} className="kv-entity-tag">{name}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {(selectedEntity || pinnedEntities.length > 0) && (
        <div className="kv-cards">
          {selectedEntity && selectedEntity.entity && (() => {
            const se = selectedEntity;
            const seId = se.entity!.id;
            return (
              <EntityDetailCard
                key={`sel-${seId}`}
                detail={se}
                pinned={isPinned(seId)}
                onPin={() => onPinEntity(se)}
                onUnpin={() => onUnpinEntity(seId)}
                onClose={onCloseSelected}
                onSelectEntity={(id) => void onSelectEntity(id)}
                onPreview={onPreviewPath}
              />
            );
          })()}
          {pinnedEntities
            .filter((p) => p.entity && p.entity.id !== selectedEntity?.entity?.id)
            .map((p) => (
              <EntityDetailCard
                key={`pin-${p.entity!.id}`}
                detail={p}
                pinned={true}
                onPin={() => {}}
                onUnpin={() => onUnpinEntity(p.entity!.id)}
                onClose={() => onUnpinEntity(p.entity!.id)}
                onSelectEntity={(id) => void onSelectEntity(id)}
                onPreview={onPreviewPath}
              />
            ))}
        </div>
      )}
    </>
  );
}
