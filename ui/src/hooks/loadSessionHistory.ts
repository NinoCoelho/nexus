/**
 * Loads and hydrates session history from the backend into the chat state map.
 * Extracted from useChatSession to keep that file under the 300 LOC limit.
 */
import type { Message } from "../components/ChatView";
import { getSession, HIDDEN_SEED_MARKER, type TraceEvent } from "../api";
import { parseHistoryTimestamp, type ChatState } from "../types/chat";

type SetChatStates = React.Dispatch<React.SetStateAction<Map<string, ChatState>>>;

export async function loadSessionHistory(
  id: string,
  setChatStates: SetChatStates,
  computeSeedModel: () => string,
  patchState: (key: string, patch: Partial<ChatState>) => void,
): Promise<void> {
  try {
    const detail = await getSession(id);
    // Hydrate badges from persisted tool_calls + tool result messages.
    // Assistant messages with tool_calls are followed by role="tool"
    // entries carrying the result; pair them so the UI can render the
    // same timeline it showed live.
    const raw = detail.messages;
    const msgs: Message[] = [];
    for (let i = 0; i < raw.length; i++) {
      const m = raw[i];
      if (m.role === "user") {
        const content = m.content ?? "";
        if (content.trim().length === 0) continue;
        // Hidden seeds are persisted so the agent has context on reload,
        // but they shouldn't show up as chat bubbles.
        if (content.startsWith(HIDDEN_SEED_MARKER)) continue;
        msgs.push({ role: "user", content, timestamp: parseHistoryTimestamp(m.created_at), seq: m.seq });
        continue;
      }
      if (m.role !== "assistant") continue;
      const toolCalls = Array.isArray(m.tool_calls)
        ? (m.tool_calls as Array<{ id?: string; name?: string; arguments?: unknown }>)
        : [];
      // Assistants whose content was stamped with a partial-status prefix
      // ([interrupted], [cancelled], [iteration_limit], [empty_response],
      // [llm_error], [crashed]) had their turn aborted mid-flight — their
      // tool calls without a paired result are genuinely unfinished.
      // Everything else is a completed turn: default tools to "done" so
      // the detail modal doesn't show a forever-running indicator.
      const rawContent = m.content ?? "";
      const partialMatch = rawContent.match(/^\[(interrupted|cancelled|iteration_limit|empty_response|llm_error|crashed)\]\s*/);
      const isPartial = partialMatch != null;
      const partialStatus = (partialMatch?.[1] ?? "interrupted") as NonNullable<Message["partial"]>["status"];
      const content = isPartial ? rawContent.slice(partialMatch![0].length) : rawContent;
      // Collect paired tool results that follow this assistant message,
      // keyed by tool_call_id if available, otherwise by position.
      const resultsById = new Map<string, string>();
      const resultsByPos: string[] = [];
      let j = i + 1;
      while (j < raw.length && raw[j].role === "tool") {
        const preview = (raw[j].content ?? "").slice(0, 200);
        const tid = raw[j].tool_call_id ?? "";
        if (tid) resultsById.set(tid, preview);
        resultsByPos.push(preview);
        j++;
      }
      const timeline: NonNullable<Message["timeline"]> = [];
      const trace: TraceEvent[] = [];
      if (content.length > 0) {
        timeline.push({ id: `h-t-${msgs.length}`, type: "text", text: content });
      }
      toolCalls.forEach((tc, tcIdx) => {
        const name = tc.name ?? "";
        if (!name) return;
        const args = tc.arguments;
        const preview = resultsById.get(tc.id ?? "") ?? resultsByPos[tcIdx];
        const status: "pending" | "done" = preview != null ? "done" : isPartial ? "pending" : "done";
        timeline.push({ id: `h-c-${msgs.length}-${timeline.length}`, type: "tool", tool: name, args, result_preview: preview, status });
        trace.push({ iter: 0, tool: name, args, result: preview } as TraceEvent);
      });
      // Skip fully-empty assistant messages (no text + no tool calls).
      if ((m.content ?? "").trim().length === 0 && timeline.length === 0) continue;
      msgs.push({
        role: "assistant",
        content,
        timestamp: parseHistoryTimestamp(m.created_at),
        timeline: timeline.length > 0 ? timeline : undefined,
        trace: trace.length > 0 ? trace : undefined,
        seq: m.seq,
        feedback: m.feedback ?? null,
        ...(isPartial ? { partial: { status: partialStatus } } : {}),
      });
      i = j - 1;
    }
    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(id);
      // Don't clobber in-flight state: if thinking or local-only messages
      // exist for this session already, preserve them; only seed history
      // for sessions we haven't loaded yet.
      if (cur && cur.historyLoaded) return prev;
      const seedModel = cur?.selectedModel || computeSeedModel();
      next.set(id, {
        messages: msgs,
        thinking: cur?.thinking ?? false,
        input: cur?.input ?? "",
        historyLoaded: true,
        attachments: cur?.attachments ?? [],
        selectedModel: seedModel,
      });
      return next;
    });
  } catch {
    patchState(id, { historyLoaded: true });
  }
}
