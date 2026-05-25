import { useEffect, useRef, useState } from "react";
import {
  getConfig,
  getGraphragIndexStatus,
  pingHealth,
} from "../api";
import i18n, { normalizeLanguage } from "../i18n";
import type { ToastAPI } from "../toast/ToastProvider";
import { subscribeGlobalNotifications } from "../api/chat";
import { adminAllPending, type AdminPendingItem } from "../api/auth";

interface UseGlobalSubscriptionsParams {
  backendDownThreshold?: number;
  isAdmin: boolean;
  userId?: string;
  focusRequest: (rid: string) => void;
  pendingFocusRequestId: string | null;
  clearPendingFocus: () => void;
  ackPlayer: { handle: (sessionId: string, data: any) => void };
  toast: ToastAPI;
  tSettings: (key: string) => string;
  bumpSettingsRevision: () => void;
  pendingGraphIndex: string | null;
  setPendingGraphIndex: (path: string | null) => void;
  handleViewEntityGraph: (mode: "file" | "folder", path: string) => void;
  indexingToastIdRef: React.MutableRefObject<string | null>;
}

export function useGlobalSubscriptions({
  backendDownThreshold = 3,
  isAdmin,
  userId,
  focusRequest,
  pendingFocusRequestId,
  clearPendingFocus,
  ackPlayer,
  toast,
  tSettings,
  bumpSettingsRevision,
  pendingGraphIndex,
  setPendingGraphIndex,
  handleViewEntityGraph,
  indexingToastIdRef,
}: UseGlobalSubscriptionsParams) {
  const [backendUp, setBackendUp] = useState<boolean | null>(null);
  const consecutiveDownRef = useRef(0);
  const [teamPending, setTeamPending] = useState<AdminPendingItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      void pingHealth().then((ok) => {
        if (cancelled) return;
        if (ok) {
          consecutiveDownRef.current = 0;
          setBackendUp(true);
        } else {
          consecutiveDownRef.current += 1;
          if (consecutiveDownRef.current >= backendDownThreshold) {
            setBackendUp(false);
          }
        }
      });
    tick();
    const id = setInterval(tick, 10000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [backendDownThreshold]);

  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((cfg) => {
        if (cancelled) return;
        const lang = normalizeLanguage(cfg.ui?.language);
        (window as any).__nexusLanguage = lang;
        if (i18n.language !== lang) void i18n.changeLanguage(lang);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const items = await adminAllPending();
        if (!cancelled) {
          const ownItems = items.filter(
            (i) => i.user_id && i.user_id !== userId,
          );
          setTeamPending(ownItems);
        }
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [isAdmin, userId]);

  useEffect(() => {
    if (!pendingFocusRequestId) return;
    focusRequest(pendingFocusRequestId);
    clearPendingFocus();
  }, [pendingFocusRequestId, focusRequest, clearPendingFocus]);

  useEffect(() => {
    const sub = subscribeGlobalNotifications((sessionId, event) => {
      if (event.kind === "voice_ack") {
        ackPlayer.handle(sessionId, event.data);
      } else if (event.kind === "nexus_tier_changed") {
        const upgraded =
          !event.data.from_models.includes("nexus")
          && event.data.to_models.includes("nexus");
        const downgraded =
          event.data.from_models.includes("nexus")
          && !event.data.to_models.includes("nexus");
        if (upgraded) {
          toast.success(tSettings("settings:nexus.tierChanged.upgraded"));
        } else if (downgraded) {
          toast.info(tSettings("settings:nexus.tierChanged.downgraded"));
        }
        bumpSettingsRevision();
      } else if (event.kind === "features_changed") {
        bumpSettingsRevision();
      }
    });
    return () => sub.close();
  }, [ackPlayer, toast, tSettings, bumpSettingsRevision]);

  useEffect(() => {
    if (!pendingGraphIndex) return;
    let active = true;
    const capturedPath = pendingGraphIndex;
    const interval = setInterval(() => {
      getGraphragIndexStatus(capturedPath)
        .then((res) => {
          if (!active) return;
          const name = capturedPath.split("/").pop() ?? capturedPath;
          if (res.status === "indexing") {
            const total = res.total_chunks ?? 0;
            const done = res.processed_chunks ?? 0;
            const pct = total > 0 ? Math.round((done / total) * 100) : null;
            const detail = total > 0
              ? `${done} / ${total} chunks${pct !== null ? ` (${pct}%)` : ""}`
              : "Chunking\u2026";
            if (indexingToastIdRef.current) {
              toast.update(indexingToastIdRef.current, { detail });
            }
          } else if (res.status === "done") {
            const n = res.node_count ?? res.nodes?.length ?? 0;
            if (indexingToastIdRef.current) { toast.dismiss(indexingToastIdRef.current); indexingToastIdRef.current = null; }
            setPendingGraphIndex(null);
            toast.success(`Indexing complete \u2014 ${n} entit${n === 1 ? "y" : "ies"} found for ${name}`, {
              duration: 8000,
              action: { label: "View graph", onClick: () => handleViewEntityGraph("file", capturedPath) },
            });
          } else if (res.status === "cancelled") {
            if (indexingToastIdRef.current) { toast.dismiss(indexingToastIdRef.current); indexingToastIdRef.current = null; }
            setPendingGraphIndex(null);
            toast.info(`Indexing cancelled for ${name}`);
          } else if (res.status === "error") {
            if (indexingToastIdRef.current) { toast.dismiss(indexingToastIdRef.current); indexingToastIdRef.current = null; }
            setPendingGraphIndex(null);
            toast.error("Indexing failed", { detail: res.detail });
          }
        })
        .catch(() => {});
    }, 3000);
    return () => { active = false; clearInterval(interval); };
  }, [pendingGraphIndex, handleViewEntityGraph, toast, setPendingGraphIndex, indexingToastIdRef]);

  return { backendUp, teamPending, setTeamPending };
}
