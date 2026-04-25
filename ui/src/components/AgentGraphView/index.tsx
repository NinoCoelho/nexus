/**
 * AgentGraphView — agent/skill/session graph on a Cytoscape canvas.
 *
 * Shows the Nexus agent node, all registered skills, and recent sessions.
 * Edges connect sessions to the skills they used during execution.
 *
 * Interactions:
 *   - Skill node click → opens the SkillDrawer
 *   - Session node click → navigates to that chat session
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getAgentGraph, type AgentGraphData } from "../../api";
import { nodeRadius } from "./types";
import { useAgentSim } from "./useAgentSim";
import "../AgentGraphView.css";

interface Props {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
}

export default function AgentGraphView({ onOpenSkill, onSelectSession }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [graph, setGraph] = useState<AgentGraphData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const offsetRef = useRef({ x: 0, y: 0 });
  const scaleRef = useRef(1);
  const hoverRef = useRef<number | null>(null);

  const dragRef = useRef<{ nodeIdx: number | null; startX: number; startY: number; moved: boolean } | null>(null);
  const panRef = useRef<{ ox: number; oy: number; mx: number; my: number } | null>(null);

  const sim = useAgentSim(canvasRef, offsetRef, scaleRef, hoverRef);

  // ── fetch ───────────────────────────────────────────────────────────────────
  const fetchGraph = useCallback(() => {
    setError(null);
    getAgentGraph()
      .then((g) => {
        setGraph(g);
        sim.initSim(g, canvasRef.current);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load graph");
      });
  }, []);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  // ── canvas sizing ───────────────────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (!parent) return;

    const ro = new ResizeObserver(() => {
      canvas.width = parent.clientWidth;
      canvas.height = parent.clientHeight;
      const g = sim.graphRef.current;
      const nodes = sim.nodesRef.current;
      if (g && nodes.length > 0) sim.draw(canvas, g, nodes);
    });
    ro.observe(parent);

    canvas.width = parent.clientWidth;
    canvas.height = parent.clientHeight;

    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    return () => sim.stopRAF();
  }, []);

  // ── hit testing ─────────────────────────────────────────────────────────────
  function canvasPoint(e: React.MouseEvent): { x: number; y: number } {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const sc = scaleRef.current;
    const { x: ox, y: oy } = offsetRef.current;
    return { x: (mx - ox) / sc, y: (my - oy) / sc };
  }

  function hitTest(cx: number, cy: number): number | null {
    const nodes = sim.nodesRef.current;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const r = nodeRadius(n.type) + 4;
      const dx = cx - n.x; const dy = cy - n.y;
      if (dx * dx + dy * dy <= r * r) return i;
    }
    return null;
  }

  // ── mouse ───────────────────────────────────────────────────────────────────
  function onMouseDown(e: React.MouseEvent) {
    const { x, y } = canvasPoint(e);
    const hit = hitTest(x, y);
    if (hit !== null) {
      dragRef.current = { nodeIdx: hit, startX: e.clientX, startY: e.clientY, moved: false };
    } else {
      const { x: ox, y: oy } = offsetRef.current;
      panRef.current = { ox, oy, mx: e.clientX, my: e.clientY };
    }
  }

  function onMouseMove(e: React.MouseEvent) {
    const { x: cx, y: cy } = canvasPoint(e);
    const hit = hitTest(cx, cy);
    hoverRef.current = hit;

    if (dragRef.current?.nodeIdx !== null && dragRef.current !== null) {
      dragRef.current.moved = true;
      const n = sim.nodesRef.current[dragRef.current.nodeIdx!];
      n.x = cx; n.y = cy;
      n.vx = 0; n.vy = 0;
      n.pinned = true;
      if (!sim.runningRef.current) {
        sim.runningRef.current = true;
        sim.settledRef.current = false;
        sim.startRAF();
      }
    } else if (panRef.current) {
      offsetRef.current = {
        x: panRef.current.ox + e.clientX - panRef.current.mx,
        y: panRef.current.oy + e.clientY - panRef.current.my,
      };
      const g = sim.graphRef.current;
      const nodes = sim.nodesRef.current;
      if (g && nodes.length > 0) sim.draw(canvasRef.current!, g, nodes);
    } else {
      const g = sim.graphRef.current;
      const nodes = sim.nodesRef.current;
      if (g && nodes.length > 0) sim.draw(canvasRef.current!, g, nodes);
    }
  }

  function onMouseUp(e: React.MouseEvent) {
    if (dragRef.current && !dragRef.current.moved) {
      const { x, y } = canvasPoint(e);
      const hit = hitTest(x, y);
      if (hit !== null) {
        const n = sim.nodesRef.current[hit];
        if (n.type === "skill") onOpenSkill(n.id.replace(/^skill:/, ""));
        else if (n.type === "session") onSelectSession(n.id.replace(/^session:/, ""));
      }
    }
    dragRef.current = null;
    panRef.current = null;
  }

  function onDoubleClick() {
    const g = sim.graphRef.current;
    const canvas = canvasRef.current;
    if (g && canvas) sim.initSim(g, canvas);
  }

  function onWheel(e: React.WheelEvent) {
    e.preventDefault();
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const { x: ox, y: oy } = offsetRef.current;
    const sc = scaleRef.current;
    const newSc = Math.max(0.1, Math.min(10, sc * factor));
    offsetRef.current = {
      x: mx - (mx - ox) * (newSc / sc),
      y: my - (my - oy) * (newSc / sc),
    };
    scaleRef.current = newSc;

    const g = sim.graphRef.current;
    const nodes = sim.nodesRef.current;
    if (g && nodes.length > 0) sim.draw(canvas, g, nodes);
  }

  function fitToView() {
    const canvas = canvasRef.current;
    const nodes = sim.nodesRef.current;
    if (!canvas || nodes.length === 0) return;

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const n of nodes) {
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
    }
    const pad = 40;
    const bw = maxX - minX + pad * 2;
    const bh = maxY - minY + pad * 2;
    const sc = Math.min(canvas.width / bw, canvas.height / bh, 2);
    scaleRef.current = sc;
    offsetRef.current = {
      x: (canvas.width - bw * sc) / 2 + (pad - minX) * sc,
      y: (canvas.height - bh * sc) / 2 + (pad - minY) * sc,
    };

    const g = sim.graphRef.current;
    if (g) sim.draw(canvas, g, nodes);
  }

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;

  return (
    <div className="agent-graph-view">
      <div className="agent-graph-toolbar">
        <button className="agent-graph-toolbar-btn" onClick={fitToView}>Fit to view</button>
        <button className="agent-graph-toolbar-btn" onClick={fetchGraph}>Refresh</button>
        <span className="agent-graph-toolbar-stat">{nodeCount} nodes</span>
        <span className="agent-graph-toolbar-stat">{edgeCount} edges</span>
      </div>

      {error && <div className="agent-graph-error">{error}</div>}

      <canvas
        ref={canvasRef}
        className="agent-graph-canvas"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onDoubleClick={onDoubleClick}
        onWheel={onWheel}
      />
    </div>
  );
}
