/**
 * Pure state-updater factories for chatStream event types.
 * Each function returns a React state updater for setChatStates.
 * Extracted to keep useChatSession.ts under 300 LOC.
 */
import type { Message } from "../components/ChatView";
import type { TraceEvent } from "../api";
import { emptyState, prettifyStreamError, NEW_KEY, type ChatState } from "../types/chat";

type SetChatStates = React.Dispatch<React.SetStateAction<Map<string, ChatState>>>;

export function applyDeltaEvent(
  setChatStates: SetChatStates,
  key: string,
  text: string,
) {
  setChatStates((prev) => {
    const next = new Map(prev);
    const cur = next.get(key) ?? emptyState();
    const msgs = [...cur.messages];
    const lastIdx = msgs.length - 1;
    if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
      const last = msgs[lastIdx];
      const tl = [...(last.timeline ?? [])];
      if (tl.length > 0 && tl[tl.length - 1].type === "text") {
        tl[tl.length - 1] = { ...tl[tl.length - 1], text: (tl[tl.length - 1].text ?? "") + text };
      } else {
        tl.push({ id: `t${tl.length}`, type: "text", text });
      }
      msgs[lastIdx] = { ...last, content: last.content + text, timeline: tl };
    }
    next.set(key, { ...cur, messages: msgs });
    return next;
  });
}

export function applyToolEvent(
  setChatStates: SetChatStates,
  key: string,
  event: { name: string; args?: unknown; result_preview?: string | null },
) {
  setChatStates((prev) => {
    const next = new Map(prev);
    const cur = next.get(key) ?? emptyState();
    const msgs = [...cur.messages];
    const lastIdx = msgs.length - 1;
    if (lastIdx < 0 || msgs[lastIdx].role !== "assistant") {
      next.set(key, { ...cur, messages: msgs });
      return next;
    }
    const prevMsg = msgs[lastIdx];
    const prevTrace = prevMsg.trace ?? [];
    let newTrace: TraceEvent[];
    if (event.result_preview != null) {
      const matchIdx = [...prevTrace].reverse().findIndex(
        (e) => e.tool === event.name && e.result == null,
      );
      if (matchIdx !== -1) {
        const realIdx = prevTrace.length - 1 - matchIdx;
        newTrace = prevTrace.map((e, i) => i === realIdx ? { ...e, result: event.result_preview } : e);
      } else {
        newTrace = [...prevTrace, { iter: 0, tool: event.name, args: event.args, result: event.result_preview } as TraceEvent];
      }
    } else {
      newTrace = [...prevTrace, { iter: 0, tool: event.name, args: event.args } as TraceEvent];
    }
    const tl = [...(prevMsg.timeline ?? [])];
    if (event.result_preview != null) {
      const toolIdx = [...tl].reverse().findIndex(
        (s) => s.type === "tool" && s.tool === event.name && s.status === "pending",
      );
      if (toolIdx !== -1) {
        const realIdx = tl.length - 1 - toolIdx;
        tl[realIdx] = { ...tl[realIdx], result: event.result_preview, result_preview: typeof event.result_preview === "string" ? event.result_preview : undefined, status: "done" as const };
      } else {
        tl.push({ id: `t${tl.length}`, type: "tool", tool: event.name, args: event.args, result: event.result_preview, result_preview: typeof event.result_preview === "string" ? event.result_preview : undefined, status: "done" });
      }
    } else {
      tl.push({ id: `t${tl.length}`, type: "tool", tool: event.name, args: event.args, status: "pending" });
    }
    msgs[lastIdx] = { ...prevMsg, trace: newTrace, timeline: tl };
    next.set(key, { ...cur, messages: msgs });
    return next;
  });
}

export function applyDoneEvent(
  setChatStates: SetChatStates,
  setActiveSession: (id: string) => void,
  setSessionsRevision: React.Dispatch<React.SetStateAction<number>>,
  persistUsedModel: (model: string) => void,
  key: string,
  activeSession: string | null,
  selectedModel: string | undefined,
  event: { session_id: string; reply: string; model?: string; routed_by?: string; trace?: TraceEvent[] },
) {
  const routedModel = event.model;
  const routedBy = (event.routed_by ?? "user") as "user" | "auto";
  const usedModel = (routedModel && routedModel !== "auto")
    ? routedModel
    : (selectedModel && selectedModel !== "auto" ? selectedModel : "");
  if (usedModel) persistUsedModel(usedModel);

  if (!activeSession) {
    // First message — migrate __new__ to the real session id.
    setChatStates((prev) => {
      const next = new Map(prev);
      const fresh = next.get(NEW_KEY) ?? emptyState();
      const lastMsg = fresh.messages[fresh.messages.length - 1];
      const preservedTimeline = lastMsg?.timeline?.map((s) =>
        s.type === "tool" && s.status === "pending" ? { ...s, status: "done" as const } : s,
      );
      const finalAsst: Message = {
        role: "assistant",
        content: event.reply,
        trace: event.trace?.length ? event.trace : undefined,
        timeline: preservedTimeline,
        timestamp: new Date(),
        streaming: false,
        model: routedModel,
        routedBy,
      };
      const msgs = fresh.messages.slice(0, -1).concat(finalAsst);
      next.set(event.session_id, { messages: msgs, thinking: false, input: "", historyLoaded: true, attachments: [], selectedModel: fresh.selectedModel });
      next.set(NEW_KEY, { ...emptyState(), selectedModel: fresh.selectedModel });
      return next;
    });
    setActiveSession(event.session_id);
  } else {
    // Replace last assistant message with authoritative reply.
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(key) ?? emptyState();
      const msgs = [...cur.messages];
      const lastIdx = msgs.length - 1;
      if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
        const lastMsg = msgs[lastIdx];
        const preservedTimeline = lastMsg?.timeline?.map((s) =>
          s.type === "tool" && s.status === "pending" ? { ...s, status: "done" as const } : s,
        );
        msgs[lastIdx] = { role: "assistant", content: event.reply, trace: event.trace?.length ? event.trace : undefined, timeline: preservedTimeline, timestamp: new Date(), streaming: false, model: routedModel };
      }
      next.set(key, { ...cur, messages: msgs, thinking: false });
      return next;
    });
  }
  setSessionsRevision((r) => r + 1);
}

export function applyLimitReachedEvent(
  setChatStates: SetChatStates,
  key: string,
  iterations: number,
) {
  const banner: Message = { role: "assistant", content: "", kind: "limit", limitIterations: iterations, timestamp: new Date() };
  setChatStates((prev) => {
    const next = new Map(prev);
    const cur = next.get(key) ?? emptyState();
    const msgs = cur.messages.slice();
    const lastIdx = msgs.length - 1;
    if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
      msgs[lastIdx] = banner;
    } else {
      msgs.push(banner);
    }
    next.set(key, { ...cur, messages: msgs, thinking: false });
    return next;
  });
}

export function applyErrorEvent(
  setChatStates: SetChatStates,
  key: string,
  reason: string | undefined,
  detail: string,
) {
  const knownStatuses: NonNullable<Message["partial"]>["status"][] = [
    "interrupted", "cancelled", "iteration_limit",
    "empty_response", "llm_error", "crashed",
    "length", "upstream_timeout",
  ];
  const mapped = reason && (knownStatuses as string[]).includes(reason)
    ? (reason as NonNullable<Message["partial"]>["status"])
    : "llm_error";
  setChatStates((prev) => {
    const next = new Map(prev);
    const cur = next.get(key) ?? emptyState();
    const msgs = [...cur.messages];
    const lastIdx = msgs.length - 1;
    // Attach partial to the existing (possibly streaming) assistant
    // placeholder so its partial content + badges stay visible.
    if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
      msgs[lastIdx] = { ...msgs[lastIdx], streaming: false, partial: { status: mapped, detail: prettifyStreamError(detail) } };
    } else {
      msgs.push({ role: "assistant", content: "", timestamp: new Date(), partial: { status: mapped, detail: prettifyStreamError(detail) } });
    }
    next.set(key, { ...cur, messages: msgs, thinking: false });
    return next;
  });
}
