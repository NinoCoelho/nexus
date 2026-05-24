import { useState, useEffect, useCallback } from "react";
import type { WorkflowSummary } from "../types/workflow";
import * as api from "../api/workflows";

export default function WorkflowListPanel({
  selectedPath,
  onOpen,
}: {
  selectedPath: string | null;
  onOpen: (path: string) => void;
}) {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);

  const load = useCallback(async () => {
    try {
      const list = await api.listWorkflows();
      setWorkflows(list);
    } catch {}
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (workflows.length === 0) return null;

  return (
    <div style={{ padding: "8px 0" }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          color: "var(--text-muted)",
          padding: "4px 12px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>Workflows · {workflows.length}</span>
        <button
          onClick={load}
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            fontSize: 12,
            color: "var(--text-muted)",
          }}
          title="Refresh"
        >
          ↻
        </button>
      </div>
      {workflows.map((w) => (
        <div
          key={w.path}
          onClick={() => onOpen(w.path)}
          style={{
            padding: "6px 12px",
            cursor: "pointer",
            fontSize: 13,
            background:
              selectedPath === w.path ? "var(--bg-hover)" : "transparent",
            borderRadius: 4,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span>⚡</span>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {w.title}
          </span>
          {!w.enabled && (
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              OFF
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
