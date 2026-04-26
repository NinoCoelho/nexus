/**
 * AgentGraphView — agent/skill/session graph rendered in 3D.
 *
 * Shows the Nexus agent node, all registered skills, and recent sessions.
 * Edges connect sessions to the skills they used during execution.
 *
 * Interactions:
 *   - Skill node click → opens the SkillDrawer
 *   - Session node click → navigates to that chat session
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph3D from "react-force-graph-3d";
import * as THREE from "three";
import { getAgentGraph, type AgentGraphData, type AgentGraphNode } from "../../api";
import "../AgentGraphView.css";

interface Props {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
}

type FgInstance = {
  zoomToFit?: (ms?: number, padding?: number) => void;
  pauseAnimation?: () => void;
  renderer?: () => { forceContextLoss?: () => void; dispose?: () => void } | undefined;
  scene?: () => { clear?: () => void } | undefined;
};

interface Node3D extends AgentGraphNode {
  size: number;
}

interface Link3D {
  source: string;
  target: string;
  label: string;
}

const TYPE_COLOR: Record<AgentGraphNode["type"], string> = {
  agent: "#c9a84c",
  skill: "#5e8a9e",
  session: "#7a5e9e",
};

function radiusFor(type: AgentGraphNode["type"]): number {
  if (type === "agent") return 6;
  if (type === "skill") return 3.5;
  return 2.5;
}

export default function AgentGraphView({ onOpenSkill, onSelectSession }: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<FgInstance | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 800, h: 600 });
  const [graph, setGraph] = useState<AgentGraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Dispose the WebGL context on unmount. Sandboxed webviews cap concurrent
  // WebGL contexts; without explicit cleanup the next 3D view fails to allocate.
  useEffect(() => {
    return () => {
      const fg = fgRef.current;
      try {
        fg?.pauseAnimation?.();
        fg?.scene?.()?.clear?.();
        const r = fg?.renderer?.();
        r?.forceContextLoss?.();
        r?.dispose?.();
      } catch { /* ignore */ }
    };
  }, []);

  const fetchGraph = useCallback(() => {
    setError(null);
    getAgentGraph()
      .then(setGraph)
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load graph");
      });
  }, []);

  useEffect(() => { fetchGraph(); }, [fetchGraph]);

  const data = useMemo<{ nodes: Node3D[]; links: Link3D[] }>(() => {
    if (!graph) return { nodes: [], links: [] };
    const nodes: Node3D[] = graph.nodes.map((n) => ({ ...n, size: radiusFor(n.type) }));
    const ids = new Set(nodes.map((n) => n.id));
    const links: Link3D[] = graph.edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, label: e.label }));
    return { nodes, links };
  }, [graph]);

  const fitToView = useCallback(() => {
    fgRef.current?.zoomToFit?.(600, 60);
  }, []);

  useEffect(() => {
    if (!data.nodes.length) return;
    const t = setTimeout(fitToView, 1200);
    return () => clearTimeout(t);
  }, [data.nodes.length, fitToView]);

  const nodeThreeObject = useCallback((raw: object) => {
    const node = raw as Node3D;
    const isSelected = node.id === selectedId;
    const baseColor = TYPE_COLOR[node.type] ?? "#888";
    const color = isSelected ? "#ffd06a" : baseColor;
    const radius = node.size + (isSelected ? 1 : 0);
    const geom = node.type === "agent"
      ? new THREE.IcosahedronGeometry(radius)
      : node.type === "skill"
        ? new THREE.SphereGeometry(radius, 16, 16)
        : new THREE.BoxGeometry(radius * 1.5, radius * 1.5, radius * 1.5);
    const mesh = new THREE.Mesh(
      geom,
      new THREE.MeshLambertMaterial({
        color,
        emissive: color,
        emissiveIntensity: isSelected ? 0.8 : 0,
      }),
    );
    const group = new THREE.Group();
    group.add(mesh);
    const label = node.label.length > 28 ? node.label.slice(0, 27) + "…" : node.label;
    const sprite = makeTextSprite(label, isSelected);
    sprite.position.set(0, radius + 1.5, 0);
    group.add(sprite);
    return group;
  }, [selectedId]);

  const onNodeClick = useCallback((raw: object) => {
    const n = raw as Node3D;
    setSelectedId(n.id);
    if (n.type === "skill") onOpenSkill(n.id.replace(/^skill:/, ""));
    else if (n.type === "session") onSelectSession(n.id.replace(/^session:/, ""));
  }, [onOpenSkill, onSelectSession]);

  const nodeCount = graph?.nodes.length ?? 0;
  const edgeCount = graph?.edges.length ?? 0;

  return (
    <div className="agent-graph-view" ref={wrapRef}>
      <div className="agent-graph-toolbar">
        <button className="agent-graph-toolbar-btn" onClick={fitToView}>Fit to view</button>
        <button className="agent-graph-toolbar-btn" onClick={fetchGraph}>Refresh</button>
        <span className="agent-graph-toolbar-stat">{nodeCount} nodes</span>
        <span className="agent-graph-toolbar-stat">{edgeCount} edges</span>
      </div>

      {error && <div className="agent-graph-error">{error}</div>}

      <ForceGraph3D
        ref={fgRef as unknown as React.MutableRefObject<undefined> | undefined}
        width={size.w}
        height={size.h}
        graphData={data}
        backgroundColor="rgba(0,0,0,0)"
        showNavInfo={false}
        nodeThreeObject={nodeThreeObject}
        linkColor={() => "rgba(180,180,180,0.45)"}
        linkOpacity={0.5}
        linkWidth={0.4}
        linkCurvature={0.1}
        linkDirectionalParticles={1}
        linkDirectionalParticleSpeed={0.005}
        enableNodeDrag
        onNodeClick={onNodeClick}
        onBackgroundClick={() => setSelectedId(null)}
        onEngineStop={fitToView}
      />
    </div>
  );
}

function makeTextSprite(text: string, highlighted: boolean): THREE.Sprite {
  const padding = 6;
  const fontSize = 22;
  const measure = document.createElement("canvas").getContext("2d")!;
  measure.font = `${fontSize}px system-ui, sans-serif`;
  const textWidth = measure.measureText(text).width;
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(textWidth + padding * 2);
  canvas.height = fontSize + padding * 2;
  const ctx = canvas.getContext("2d")!;
  ctx.font = `${fontSize}px system-ui, sans-serif`;
  ctx.fillStyle = "rgba(29, 32, 37, 0.85)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = highlighted ? "#ffffff" : "#ece8e1";
  ctx.textBaseline = "top";
  ctx.fillText(text, padding, padding);
  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(material);
  const scale = 0.05;
  sprite.scale.set(canvas.width * scale, canvas.height * scale, 1);
  return sprite;
}
