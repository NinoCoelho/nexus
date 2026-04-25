// Sub-component for GraphView: detail sidebar shown when a node is selected.

import type { GraphData } from "../../api";
import type { DetailInfo } from "./types";
import { entityColor } from "./utils";

interface DetailPanelProps {
  detail: DetailInfo;
  graph: GraphData | null;
  detailEntities: { id: number; name: string; type: string }[];
  onClose: () => void;
  onExploreFromFile: (path: string) => void;
  onExploreEntity: (id: number) => void;
  onSetScope: (scope: "tag", seed: string) => void;
  onPreviewPath: (path: string) => void;
}

export function DetailPanel({
  detail,
  graph,
  detailEntities,
  onClose,
  onExploreFromFile,
  onExploreEntity,
  onSetScope,
  onPreviewPath,
}: DetailPanelProps) {
  const title = detail.type === "file"
    ? (graph?.nodes.find(n => n.path === detail.path)?.title || detail.path?.split("/").pop() || "")
    : detail.entity?.name || "";

  return (
    <div className="graph-detail-panel">
      <div className="graph-detail-header">
        <span className="graph-detail-title">{title}</span>
        <button className="graph-detail-close" onClick={onClose}>&times;</button>
      </div>
      <div className="graph-detail-body">
        {detail.type === "file" && detail.path && (
          <>
            <div className="graph-detail-meta">
              <span className="graph-detail-label">Path</span>
              <span className="graph-detail-value">{detail.path}</span>
            </div>
            {(() => {
              const node = graph?.nodes.find(n => n.path === detail.path);
              return node?.tags?.length ? (
                <div className="graph-detail-meta">
                  <span className="graph-detail-label">Tags</span>
                  <div className="graph-detail-tags">
                    {node.tags.map(t => (
                      <span key={t} className="graph-detail-tag" onClick={() => onSetScope("tag", t)}>{t}</span>
                    ))}
                  </div>
                </div>
              ) : null;
            })()}
            <button className="graph-detail-action" onClick={() => onExploreFromFile(detail.path!)}>
              Explore from here
            </button>
            {detailEntities.length > 0 && (
              <div className="graph-detail-section">
                <span className="graph-detail-label">Entities ({detailEntities.length})</span>
                <div className="graph-detail-entities">
                  {detailEntities.map(en => (
                    <span key={en.id} className="graph-detail-entity" onClick={() => onExploreEntity(en.id)}>
                      <span className="graph-detail-entity-dot" style={{ background: entityColor(en.type) }} />
                      {en.name}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
        {detail.type === "entity" && detail.entity && (
          <>
            <div className="graph-detail-meta">
              <span className="graph-detail-label">Type</span>
              <span className="graph-detail-value">{detail.entity.type}</span>
            </div>
            <button className="graph-detail-action" onClick={() => onExploreEntity(detail.entity!.id)}>
              Explore from here
            </button>
            {detail.entity.source_paths.length > 0 && (
              <div className="graph-detail-section">
                <span className="graph-detail-label">Source files ({detail.entity.source_paths.length})</span>
                <div className="graph-detail-sources">
                  {detail.entity.source_paths.map(sp => (
                    <span key={sp} className="graph-detail-source" onClick={() => onPreviewPath(sp)}>
                      {sp}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
