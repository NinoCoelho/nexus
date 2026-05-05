/**
 * Orchestrator for one folder graph tab.
 *
 * Owns the per-tab phase machine:
 *   - loading       → fetching open() result on mount
 *   - needs-config  → no ontology saved → show OntologyConfigScreen
 *   - indexing      → first index running → show FolderIndexProgress
 *   - editing-ontology → user clicked "Edit ontology"
 *   - ready         → renders into the graph canvas via the parent's
 *                     useFolderKnowledgeMode hook (signalled by props).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteFolderGraph,
  getFolderStale,
  openFolderGraph,
  putFolderOntology,
  type FolderOntology,
  type FolderOpenResult,
  type FolderStaleResult,
} from "../../api/folderGraph";
import { useToast } from "../../toast/ToastProvider";
import { OntologyConfigScreen } from "./OntologyConfigScreen";
import { FolderIndexProgress } from "./FolderIndexProgress";
import { StalePrompt } from "./StalePrompt";
import "./FolderGraph.css";

type Phase =
  | { kind: "loading" }
  | { kind: "needs-config"; current: FolderOpenResult }
  | { kind: "indexing"; ontology: FolderOntology; full: boolean }
  | { kind: "editing-ontology"; current: FolderOpenResult }
  | { kind: "ready"; current: FolderOpenResult };

interface Props {
  folderPath: string;
  folderLabel: string;
  /** Bumped to force a refetch in the parent's folderKnowledge hook. */
  onReindexComplete: () => void;
  /** Triggered by a toolbar control in the parent UnifiedGraph. */
  externalEditOntology?: number;
  externalReindex?: number;
  externalReset?: number;
}

export function FolderGraphTab({
  folderPath,
  folderLabel,
  onReindexComplete,
  externalEditOntology = 0,
  externalReindex = 0,
  externalReset = 0,
}: Props) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [stale, setStale] = useState<FolderStaleResult | null>(null);
  // Hold toast in a ref so refresh's identity doesn't depend on provider
  // re-renders. Without this, a toast list mutation invalidates this hook,
  // which fires the useEffect, which can re-trigger errors → toast spam.
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;

  const refresh = useCallback(async () => {
    try {
      const open = await openFolderGraph(folderPath);
      if (!open.exists) {
        setPhase({ kind: "needs-config", current: open });
      } else {
        setPhase({ kind: "ready", current: open });
        // Stale check is best-effort; failure shouldn't block the graph.
        getFolderStale(folderPath).then(setStale).catch(() => {});
      }
    } catch (err) {
      toastRef.current.error("Could not open folder graph", {
        detail: err instanceof Error ? err.message : String(err),
      });
    }
  }, [folderPath]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // External "Edit ontology" trigger from the toolbar
  useEffect(() => {
    if (externalEditOntology > 0 && phase.kind === "ready") {
      setPhase({ kind: "editing-ontology", current: phase.current });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalEditOntology]);

  // External "Reindex" trigger from the toolbar
  useEffect(() => {
    if (externalReindex > 0 && phase.kind === "ready" && phase.current.ontology) {
      setPhase({ kind: "indexing", ontology: phase.current.ontology, full: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalReindex]);

  // External "Reset" trigger — delete index entirely and go back to needs-config
  useEffect(() => {
    if (externalReset > 0 && (phase.kind === "ready" || phase.kind === "editing-ontology")) {
      deleteFolderGraph(folderPath)
        .then(() => {
          setStale(null);
          setPhase({ kind: "needs-config", current: { path: folderPath, abs_path: "", exists: false, ontology: null, ontology_hash: null, embedder_id: null, extractor_id: null, file_count: 0, last_indexed_at: null } });
          onReindexComplete();
        })
        .catch((err) => {
          toastRef.current.error("Could not reset graph", {
            detail: err instanceof Error ? err.message : String(err),
          });
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalReset]);

  async function handleConfirmOntology(ont: FolderOntology, opts: { triggerIndex: boolean }) {
    try {
      await putFolderOntology(folderPath, ont);
    } catch (err) {
      toastRef.current.error("Failed to save ontology", {
        detail: err instanceof Error ? err.message : String(err),
      });
      return;
    }
    if (opts.triggerIndex) {
      setPhase({ kind: "indexing", ontology: ont, full: true });
    } else {
      await refresh();
    }
  }

  function handleIndexDone() {
    onReindexComplete();
    void refresh();
  }

  function handleIndexCancel() {
    void refresh();
  }

  function handleReindexFromBanner() {
    if (phase.kind !== "ready" || !phase.current.ontology) return;
    setPhase({ kind: "indexing", ontology: phase.current.ontology, full: true });
  }

  if (phase.kind === "loading") {
    return (
      <div className="fg-loading">
        <div className="fg-spinner" />
        <div>Opening graph for {folderLabel}…</div>
      </div>
    );
  }

  if (phase.kind === "needs-config") {
    return (
      <OntologyConfigScreen
        folderPath={folderPath}
        folderLabel={folderLabel}
        onConfirm={handleConfirmOntology}
        onCancel={() => void refresh()}
      />
    );
  }

  if (phase.kind === "editing-ontology") {
    return (
      <OntologyConfigScreen
        folderPath={folderPath}
        folderLabel={folderLabel}
        initial={phase.current.ontology}
        pendingReindex
        onConfirm={handleConfirmOntology}
        onCancel={() => setPhase({ kind: "ready", current: phase.current })}
      />
    );
  }

  if (phase.kind === "indexing") {
    return (
      <FolderIndexProgress
        folderPath={folderPath}
        folderLabel={folderLabel}
        ontology={phase.ontology}
        full={phase.full}
        onDone={handleIndexDone}
        onCancel={handleIndexCancel}
        onError={(detail) => toastRef.current.error("Indexing failed", { detail })}
      />
    );
  }

  // ready — the graph itself is rendered by the parent UnifiedGraph using the
  // folderKnowledge hook. We only render the stale banner overlay here.
  if (stale && (stale.added.length || stale.changed.length || stale.removed.length)) {
    return (
      <StalePrompt
        stale={stale}
        onReindex={handleReindexFromBanner}
        onDismiss={() => setStale(null)}
      />
    );
  }
  return null;
}
