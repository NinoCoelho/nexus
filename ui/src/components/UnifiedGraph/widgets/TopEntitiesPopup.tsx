/**
 * TopEntitiesPopup — anchored popup listing top knowledge entities.
 *
 * Replaces the always-visible "Top Entities" landing pane that used to live
 * inside EntityPanel. Toolbar-toggled; click an entity to select it.
 */

import { useEffect, useRef, useState } from "react";
import type { KnowledgeEntity } from "../../../api";
import { typeColor } from "../../KnowledgeView/typeColors";

interface Props {
  entities: KnowledgeEntity[];
  typeFilter: string | null;
  onPick: (id: number) => void;
  onClose: () => void;
}

export function TopEntitiesPopup({ entities, typeFilter, onPick, onClose }: Props) {
  const [filter, setFilter] = useState("");
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) onClose();
    };
    // delay one frame so the toolbar click that opened us doesn't close it
    const t = setTimeout(() => window.addEventListener("mousedown", handler), 0);
    return () => { clearTimeout(t); window.removeEventListener("mousedown", handler); };
  }, [onClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const visible = entities.filter((e) => {
    if (typeFilter !== null && e.type !== typeFilter) return false;
    if (filter && !e.name.toLowerCase().includes(filter.toLowerCase())) return false;
    return true;
  });

  return (
    <div ref={wrapRef} className="ug-top-entities-popup">
      <div className="kv-landing-header">
        <h3 className="kv-landing-title">Top Entities</h3>
        <input
          autoFocus
          className="kv-entity-filter"
          type="text"
          placeholder="Filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>
      <div className="kv-entity-grid ug-top-entities-grid">
        {visible.map((e) => (
          <button
            key={e.id}
            className="kv-entity-card"
            onClick={() => { onPick(e.id); onClose(); }}
          >
            <span className="kv-entity-dot" style={{ background: typeColor(e.type) }} />
            <span className="kv-entity-name">{e.name}</span>
            <span className="kv-entity-type">{e.type}</span>
            <span className="kv-entity-degree">{e.degree}</span>
          </button>
        ))}
        {visible.length === 0 && <p className="ug-top-entities-empty">No entities match.</p>}
      </div>
    </div>
  );
}
