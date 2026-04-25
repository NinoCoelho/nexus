/**
 * @file Entity search hook for the KnowledgeView subgraph.
 *
 * Tracks the search term and the set of highlighted nodes (`highlightNodesRef`),
 * applying the filter directly to the simulation refs and triggering a canvas
 * redraw without causing unnecessary re-renders in the parent component.
 */

import { useState } from "react";
import { drawCanvas } from "./useSubgraphSim";
import type { SubgraphSimRefs } from "./useSubgraphSim";

/**
 * Hook that manages entity name search within the KnowledgeView subgraph.
 *
 * Updates `simRefs.highlightNodesRef` with the indices of nodes matching
 * the typed term and triggers a canvas redraw. The search is case-insensitive
 * and substring-based (`includes`).
 *
 * @param simRefs - Shared simulation refs for the subgraph.
 * @returns
 *   - `graphSearch` — current search term.
 *   - `graphSearchCount` — number of nodes matching the term.
 *   - `onGraphSearchChange` — handler for the search input.
 *   - `clearGraphSearch` — clears the term and removes all highlights.
 */
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
