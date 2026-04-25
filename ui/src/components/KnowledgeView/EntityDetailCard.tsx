// Sub-component for KnowledgeView: entity detail card with relations and source documents.

import type { EntityDetail } from "../../api";
import { typeColor } from "./utils";

export function EntityDetailCard({
  detail,
  pinned,
  onPin,
  onUnpin,
  onClose,
  onSelectEntity,
  onPreview,
}: {
  detail: EntityDetail;
  pinned: boolean;
  onPin: () => void;
  onUnpin: () => void;
  onClose: () => void;
  onSelectEntity: (id: number) => void;
  onPreview: (path: string) => void;
}) {
  if (!detail.entity) return null;
  return (
    <div className={`kv-entity-detail${pinned ? " kv-entity-detail--pinned" : ""}`}>
      <div className="kv-entity-detail-header">
        <span className="kv-entity-dot" style={{ background: typeColor(detail.entity.type) }} />
        <h3 className="kv-entity-detail-name">{detail.entity.name}</h3>
        <span className="kv-entity-detail-type">{detail.entity.type}</span>
        <button
          className={`kv-entity-detail-pin${pinned ? " kv-entity-detail-pin--active" : ""}`}
          onClick={pinned ? onUnpin : onPin}
          title={pinned ? "Unpin" : "Pin"}
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9.828.722a.5.5 0 0 1 .354.146l4.95 4.95a.5.5 0 0 1-.354.853H11.5l-2.5 5-1.5-1.5-3.354 3.354a.5.5 0 0 1-.707-.708L6.793 9.47l-1.5-1.5 5-2.5V1.222a.5.5 0 0 1 .535-.5Z" />
          </svg>
        </button>
        <button className="kv-entity-detail-close" onClick={onClose}>&times;</button>
      </div>
      <div className="kv-entity-detail-degree">{detail.degree} connections</div>

      {detail.relations.length > 0 && (
        <div className="kv-entity-relations">
          <h4>Relations</h4>
          {detail.relations.slice(0, 20).map((rel, i) => (
            <button
              key={i}
              className="kv-relation-row"
              onClick={() => onSelectEntity(rel.entity_id)}
            >
              <span className="kv-relation-dir">{rel.direction === "outgoing" ? "→" : "←"}</span>
              <span className="kv-relation-name">{rel.relation.replace(/_/g, " ")}</span>
              <span className="kv-relation-entity">
                <span className="kv-entity-dot kv-entity-dot--sm" style={{ background: typeColor(rel.entity_type) }} />
                {rel.entity_name}
              </span>
            </button>
          ))}
        </div>
      )}

      {detail.chunks.length > 0 && (
        <div className="kv-entity-chunks">
          <h4>Source Documents</h4>
          {detail.chunks.map((c) => (
            <button
              key={c.chunk_id}
              className="kv-chunk-row"
              onClick={() => onPreview(c.source_path)}
            >
              {c.source_path} &rsaquo; {c.heading}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
