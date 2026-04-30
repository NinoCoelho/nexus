/**
 * DatabaseSchemaView — renders an ER diagram of all data-tables in a folder.
 *
 * Fetches mermaid `erDiagram` source from the backend and renders via the same
 * lazy-loaded mermaid module that powers MarkdownView's mermaid code blocks.
 */

import { useEffect, useRef, useState } from "react";
import { BASE } from "../../api/base";

interface Props {
  folder: string;
  onClose?: () => void;
}

interface MermaidApi {
  initialize: (config: object) => void;
  render: (id: string, code: string) => Promise<{ svg: string }>;
}

let _mermaidPromise: Promise<MermaidApi> | null = null;
function loadMermaid(): Promise<MermaidApi> {
  if (_mermaidPromise) return _mermaidPromise;
  _mermaidPromise = import("mermaid").then((m) => {
    const api = (m.default ?? m) as unknown as MermaidApi;
    api.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "strict" });
    return api;
  });
  return _mermaidPromise;
}

let _renderId = 0;

export default function DatabaseSchemaView({ folder, onClose }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [empty, setEmpty] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setEmpty(false);
    if (ref.current) ref.current.innerHTML = "";

    (async () => {
      try {
        const url = `${BASE}/vault/datatable/erdiagram?folder=${encodeURIComponent(folder)}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`ER diagram error: ${res.status}`);
        const body = await res.json();
        if (cancelled) return;
        const code: string = body.mermaid || "";
        if (!code.trim() || code.trim() === "erDiagram") {
          setEmpty(true);
          return;
        }
        const mermaid = await loadMermaid();
        if (cancelled) return;
        const id = `db-erd-${++_renderId}`;
        const { svg } = await mermaid.render(id, code);
        if (cancelled) return;
        if (ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        if (!cancelled) setError((e as Error).message ?? "render failed");
      }
    })();

    return () => { cancelled = true; };
  }, [folder]);

  return (
    <div className="db-schema-view">
      <div className="db-schema-view-header">
        <span className="db-schema-view-title">
          ER diagram · {folder || "(root)"}
        </span>
        {onClose && (
          <button className="dt-action-btn" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      {error && <div className="dt-error">{error}</div>}
      {empty && !error && (
        <div className="dt-empty">
          No relations to diagram in this database yet — add a column with{" "}
          <code>kind: ref</code> to a table to wire one up.
        </div>
      )}
      <div ref={ref} className="db-schema-view-svg" />
    </div>
  );
}
