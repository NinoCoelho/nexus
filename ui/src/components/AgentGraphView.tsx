import { useCallback, useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { getAgentGraph, type AgentGraphData } from "../api";
import "./AgentGraphView.css";

interface Props {
  onOpenSkill: (name: string) => void;
  onSelectSession: (id: string) => void;
}

export default function AgentGraphView({ onOpenSkill, onSelectSession }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [status, setStatus] = useState<"loading" | "error" | "ok">("loading");
  const [data, setData] = useState<AgentGraphData | null>(null);

  const fetchData = useCallback(async () => {
    setStatus("loading");
    try {
      const graph = await getAgentGraph();
      setData(graph);
      setStatus("ok");
    } catch {
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  // Initialize or re-render Cytoscape when data is ready and container is mounted
  useEffect(() => {
    if (!data || !containerRef.current) return;

    // Read CSS vars for colours
    const style = getComputedStyle(document.documentElement);
    const accent = style.getPropertyValue("--accent").trim() || "#d4855c";
    const sage = style.getPropertyValue("--sage").trim() || "#8ba888";
    const border = style.getPropertyValue("--border").trim() || "#2c3037";
    const borderSoft = style.getPropertyValue("--border-soft").trim() || "#23272e";
    const fg = style.getPropertyValue("--fg").trim() || "#ece8e1";
    const fgDim = style.getPropertyValue("--fg-dim").trim() || "#a39d92";
    const bg = style.getPropertyValue("--bg-panel").trim() || "#1d2025";

    // Destroy previous instance
    if (cyRef.current) {
      cyRef.current.destroy();
      cyRef.current = null;
    }

    const elements: cytoscape.ElementDefinition[] = [
      ...data.nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label,
          type: n.type,
          ...n.meta,
        },
      })),
      ...data.edges.map((e, i) => ({
        data: {
          id: `edge-${i}`,
          source: e.source,
          target: e.target,
          label: e.label,
        },
      })),
    ];

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)" as unknown as string,
            "text-valign": "bottom" as const,
            "text-halign": "center" as const,
            "font-size": 11,
            color: fgDim,
            "background-color": borderSoft,
            "border-width": 1.5,
            "border-color": border,
            "text-margin-y": 4,
          },
        },
        {
          selector: "node[type = 'agent']",
          style: {
            width: 48,
            height: 48,
            "background-color": accent,
            "border-color": accent,
            "font-size": 12,
            "font-weight": "bold" as const,
            color: fg,
          },
        },
        {
          selector: "node[type = 'skill']",
          style: {
            width: 32,
            height: 32,
            "background-color": sage,
            "border-color": sage,
            color: fg,
          },
        },
        {
          selector: "node[type = 'session']",
          style: {
            width: 20,
            height: 20,
            "background-color": bg,
            "border-color": borderSoft,
            color: fgDim,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1,
            "line-color": border,
            "target-arrow-color": border,
            "target-arrow-shape": "triangle" as const,
            "curve-style": "bezier" as const,
            opacity: 0.6,
          },
        },
        {
          selector: "node.highlighted",
          style: {
            "border-width": 2.5,
            "border-color": accent,
          },
        },
        {
          selector: "edge.highlighted",
          style: {
            "line-color": accent,
            "target-arrow-color": accent,
            opacity: 1,
            width: 2,
          },
        },
        {
          selector: "node.faded, edge.faded",
          style: {
            opacity: 0.2,
          },
        },
      ],
      layout: {
        name: "cose",
        animate: false,
        padding: 40,
        nodeRepulsion: () => 8000,
        idealEdgeLength: () => 80,
        edgeElasticity: () => 0.45,
        gravity: 0.25,
        numIter: 1000,
        coolingFactor: 0.99,
        minTemp: 1.0,
      } as cytoscape.LayoutOptions,
      userZoomingEnabled: true,
      userPanningEnabled: true,
      boxSelectionEnabled: false,
    });

    // Hover interactions: highlight connected edges
    cy.on("mouseover", "node", (evt) => {
      const node = evt.target;
      cy.elements().addClass("faded");
      node.removeClass("faded").addClass("highlighted");
      node.connectedEdges().removeClass("faded").addClass("highlighted");
      node.connectedEdges().connectedNodes().removeClass("faded").addClass("highlighted");
    });

    cy.on("mouseout", "node", () => {
      cy.elements().removeClass("faded highlighted");
    });

    // Click interactions
    cy.on("tap", "node[type = 'skill']", (evt) => {
      const nodeId: string = evt.target.id() as string;
      const skillName = nodeId.replace("skill:", "");
      onOpenSkill(skillName);
    });

    cy.on("tap", "node[type = 'session']", (evt) => {
      const nodeId: string = evt.target.id() as string;
      const sessionId = nodeId.replace("session:", "");
      onSelectSession(sessionId);
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [data, onOpenSkill, onSelectSession]);

  const handleFit = () => {
    cyRef.current?.fit(undefined, 40);
  };

  const handleRefresh = () => {
    void fetchData();
  };

  return (
    <div className="agent-graph-view">
      <div className="agent-graph-toolbar">
        <span className="agent-graph-toolbar-title">Agent Graph</span>
        <button className="agent-graph-btn" onClick={handleFit} title="Fit graph to view">
          Fit
        </button>
        <button className="agent-graph-btn" onClick={handleRefresh} title="Reload graph data">
          Refresh
        </button>
      </div>

      {status === "loading" && (
        <div className="agent-graph-status">Loading graph…</div>
      )}
      {status === "error" && (
        <div className="agent-graph-status">Failed to load graph — is the server running?</div>
      )}

      <div
        ref={containerRef}
        className="agent-graph-container"
        style={{ display: status === "ok" ? "block" : "none" }}
      />
    </div>
  );
}
