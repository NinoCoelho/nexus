import { useCallback, useEffect, useRef, useState } from "react";
import {
  addOperation,
  deleteOperation,
  runOperation,
  planOperation,
  executeOperation,
  fetchRunHistory,
  type Dashboard,
  type DashboardOperation,
} from "../../api/dashboard";
import { deleteSession } from "../../api/sessions";
import { subscribeSessionEvents } from "../../api/chat";
import type { ToastAPI } from "../../toast/ToastProvider";
import type { DataTable } from "../../api/datatable";
import { getVaultDataTable } from "../../api/datatable";
import type { OpRunState } from "./OperationChips";

interface UseDashboardOperationsParams {
  folder: string;
  setDashboard: React.Dispatch<React.SetStateAction<Dashboard | null>>;
  toast: ToastAPI;
  onFormOp: (op: DashboardOperation, table: DataTable) => void;
}

export function useDashboardOperations({
  folder,
  setDashboard,
  toast,
  onFormOp,
}: UseDashboardOperationsParams) {
  const [runState, setRunState] = useState<Record<string, OpRunState>>({});
  const [openRun, setOpenRun] = useState<{ op: DashboardOperation; state: OpRunState } | null>(null);
  const [pendingRemoval, setPendingRemoval] = useState<DashboardOperation | null>(null);
  const [planReview, setPlanReview] = useState<
    { op: DashboardOperation; sessionId: string } | null
  >(null);
  const [showAddOp, setShowAddOp] = useState(false);
  const [showOpWizard, setShowOpWizard] = useState(false);

  const fadeTimers = useRef<Record<string, number>>({});
  const runSubs = useRef<Record<string, { close: () => void }>>({});

  useEffect(() => () => {
    Object.values(fadeTimers.current).forEach((id) => window.clearTimeout(id));
    Object.values(runSubs.current).forEach((s) => s.close());
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const history = await fetchRunHistory(folder);
        if (cancelled) return;
        const next: Record<string, OpRunState> = {};
        for (const r of history.runs) {
          if (r.status === "failed") {
            next[r.op_id] = {
              sessionId: r.session_id,
              status: "failed",
              error: r.error ?? undefined,
            };
          } else {
            void deleteSession(r.session_id).catch(() => {});
          }
        }
        setRunState((prev) => {
          const merged: Record<string, OpRunState> = { ...next };
          for (const [opId, state] of Object.entries(prev)) {
            if (state.status === "running") merged[opId] = state;
          }
          return merged;
        });
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [folder]);

  const trackChatOpSession = useCallback((op: DashboardOperation, sessionId: string) => {
    const prevTimer = fadeTimers.current[op.id];
    if (prevTimer) {
      window.clearTimeout(prevTimer);
      delete fadeTimers.current[op.id];
    }
    const prevSub = runSubs.current[op.id];
    if (prevSub) {
      prevSub.close();
      delete runSubs.current[op.id];
    }
    setRunState((s) => ({ ...s, [op.id]: { sessionId, status: "running" } }));
    const sub = subscribeSessionEvents(sessionId, (event) => {
      if (event.kind !== "op_done") return;
      const ok = event.data.status === "done";
      const finalStatus: OpRunState["status"] = ok ? "done" : "failed";
      setRunState((s) =>
        s[op.id]?.sessionId === sessionId
          ? {
              ...s,
              [op.id]: {
                sessionId,
                status: finalStatus,
                error: ok ? undefined : event.data.error ?? undefined,
              },
            }
          : s,
      );
      setOpenRun({
        op,
        state: { sessionId, status: finalStatus, error: ok ? undefined : event.data.error ?? undefined },
      });
      const current = runSubs.current[op.id];
      if (current) {
        current.close();
        delete runSubs.current[op.id];
      }
    });
    runSubs.current[op.id] = sub;
  }, []);

  const handleRunOperation = useCallback(async (op: DashboardOperation) => {
    if (op.kind === "chat") {
      if (op.preview) {
        try {
          const { session_id } = await planOperation(folder, op.id);
          setPlanReview({ op, sessionId: session_id });
        } catch (e) {
          toast.error("Couldn't build a plan.", { detail: (e as Error).message });
        }
        return;
      }
      try {
        const result = await runOperation(folder, op.id);
        trackChatOpSession(op, result.session_id);
      } catch (e) {
        toast.error("Couldn't start action.", { detail: (e as Error).message });
        setRunState((s) => {
          const { [op.id]: _gone, ...rest } = s;
          void _gone;
          return rest;
        });
      }
      return;
    }
    if (!op.table) {
      toast.error("This operation has no target table.");
      return;
    }
    try {
      const tbl = await getVaultDataTable(op.table);
      onFormOp(op, tbl);
    } catch (e) {
      toast.error("Couldn't load the target table.", { detail: (e as Error).message });
    }
  }, [folder, toast, trackChatOpSession, onFormOp]);

  const handleApprovePlan = useCallback(async (op: DashboardOperation, approvedPlan: string) => {
    setPlanReview(null);
    try {
      const result = await executeOperation(folder, op.id, approvedPlan);
      trackChatOpSession(op, result.session_id);
    } catch (e) {
      toast.error("Couldn't start the approved run.", { detail: (e as Error).message });
    }
  }, [folder, toast, trackChatOpSession]);

  const handleOpenRun = useCallback((op: DashboardOperation) => {
    const state = runState[op.id];
    if (!state) return;
    setOpenRun({ op, state });
  }, [runState]);

  const handleAddOperation = useCallback(async (op: DashboardOperation) => {
    try {
      const next = await addOperation(folder, op);
      setDashboard(next);
      setShowAddOp(false);
      toast.success(`Added "${op.label}"`);
    } catch (e) {
      toast.error("Couldn't add operation", { detail: (e as Error).message });
    }
  }, [folder, toast, setDashboard]);

  const handleRemoveOperation = useCallback(async (opId: string) => {
    try {
      const next = await deleteOperation(folder, opId);
      setDashboard(next);
    } catch (e) {
      toast.error("Couldn't remove operation", { detail: (e as Error).message });
    }
  }, [folder, toast, setDashboard]);

  return {
    runState,
    setRunState,
    openRun,
    setOpenRun,
    pendingRemoval,
    setPendingRemoval,
    planReview,
    setPlanReview,
    showAddOp,
    setShowAddOp,
    showOpWizard,
    setShowOpWizard,
    handleRunOperation,
    handleApprovePlan,
    handleOpenRun,
    handleAddOperation,
    handleRemoveOperation,
  };
}
