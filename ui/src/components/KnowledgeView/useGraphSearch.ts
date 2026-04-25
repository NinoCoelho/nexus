// Graph-search state and handler logic for KnowledgeView.

import { useState } from "react";
import { drawCanvas } from "./useSubgraphSim";
import type { SubgraphSimRefs } from "./useSubgraphSim";

export function useGraphSearch(simRefs: SubgraphSimRefs) {
  const [graphSearch, setGraphSearch] = useState("");
  const [graphSearchCount, setGraphSearchCount] = useState(0);

  function triggerRedraw() {
    const sg = simRefs.subgraphRef.current;
    const canvas = simRefs.canvasRef.current;
    if (sg && canvas) drawCanvas(canvas, sg, simRefs.simNodesRef.current, simRefs);
  }

  function onGraphSearchChange(value: string) {
    setGraphSearch(value);
    const nodes = simRefs.simNodesRef.current;
    if (!value.trim()) {
      simRefs.highlightNodesRef.current = new Set();
      setGraphSearchCount(0);
    } else {
      const q = value.toLowerCase();
      const matched = new Set<number>();
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].name.toLowerCase().includes(q)) matched.add(i);
      }
      simRefs.highlightNodesRef.current = matched;
      setGraphSearchCount(matched.size);
    }
    // Trigger redraw via subgraphRef
    triggerRedraw();
  }

  function clearGraphSearch() {
    setGraphSearch("");
    simRefs.highlightNodesRef.current = new Set();
    setGraphSearchCount(0);
    triggerRedraw();
  }

  return { graphSearch, graphSearchCount, onGraphSearchChange, clearGraphSearch };
}
