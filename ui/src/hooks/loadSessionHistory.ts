/**
 * Loads and hydrates session history from the backend into the chat state map.
 *
 * The persistence layer writes one ``assistant`` row per tool-call iteration,
 * so a single logical turn becomes N+1 rows in the database. The live UI
 * accumulates those iterations into one bubble; this loader does the same on
 * reload so the post-refresh view matches the live-stream view.
 */
import type { Message, TimelineStep } from "../components/ChatView";
import { getSession, HIDDEN_SEED_MARKER, type TraceEvent } from "../api";
import { parseHistoryTimestamp, type ChatState } from "../types/chat";

type SetChatStates = React.Dispatch<React.SetStateAction<Map<string, ChatState>>>;

const PARTIAL_PREFIX_RE = /^\[(interrupted|cancelled|iteration_limit|empty_response|llm_error|crashed|budget_exceeded)\]\s*/;

export async function loadSessionHistory(
  id: string,
  setChatStates: SetChatStates,
  computeSeedModel: (preferred?: string) => string,
  patchState: (key: string, patch: Partial<ChatState>) => void,
  forceRefresh?: boolean,
): Promise<void> {
  try {
    const detail = await getSession(id);
    const raw = detail.messages;
    const msgs: Message[] = [];

    // Bubble accumulator: a single assistant Message that absorbs every
    // assistant row (and its paired tool-result rows) until the next visible
    // user message. Hidden-seed user messages (continue / retry) don't break
    // the grouping — they're just continuations of the same logical turn.
    let bubble: Message | null = null;
    const flush = () => {
      if (bubble) {
        msgs.push(bubble);
        bubble = null;
      }
    };

    for (let i = 0; i < raw.length; i++) {
      const m = raw[i];

      if (m.role === "user") {
        // Server may serialize ``content`` as a plain string OR a list of
        // ContentPart dicts when the turn carried image/audio/document
        // attachments. Split a multipart payload into a text body + chip
        // list so the bubble renders the same way it did during streaming.
        // The TS type narrows to ``string`` for all the common paths; cast
        // through ``unknown`` here to accept the array shape at runtime.
        const rawContent = m.content as unknown;
        let content = "";
        let attachments: { name: string; vaultPath: string }[] | undefined;
        if (typeof rawContent === "string") {
          content = rawContent;
        } else if (Array.isArray(rawContent)) {
          const texts: string[] = [];
          const atts: { name: string; vaultPath: string }[] = [];
          for (const p of rawContent as Array<{
            kind?: string; text?: string; vault_path?: string;
          }>) {
            if (p?.kind === "text" && typeof p.text === "string") {
              texts.push(p.text);
            } else if (typeof p?.vault_path === "string" && p.vault_path) {
              atts.push({
                name: p.vault_path.split("/").pop() ?? p.vault_path,
                vaultPath: p.vault_path,
              });
            }
          }
          content = texts.join("\n\n");
          if (atts.length > 0) attachments = atts;
        }
        if (content.trim().length === 0 && !attachments) continue;
        if (content.startsWith(HIDDEN_SEED_MARKER)) continue;
        flush();
        msgs.push({
          role: "user",
          content,
          timestamp: parseHistoryTimestamp(m.created_at),
          seq: m.seq,
          pinned: m.pinned ?? false,
          ...(attachments ? { attachments } : {}),
        });
        continue;
      }

      if (m.role !== "assistant") continue;

      const toolCalls = Array.isArray(m.tool_calls)
        ? (m.tool_calls as Array<{ id?: string; name?: string; arguments?: unknown }>)
        : [];

      const rawContent = m.content ?? "";
      const partialMatch = rawContent.match(PARTIAL_PREFIX_RE);
      const isPartial = partialMatch != null;
      const partialStatus = (partialMatch?.[1] ?? "interrupted") as NonNullable<Message["partial"]>["status"];
      const content = isPartial ? rawContent.slice(partialMatch![0].length) : rawContent;

      // Pair tool-result rows immediately following this assistant.
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

      if (bubble == null) {
        bubble = {
          role: "assistant",
          content: "",
          timeline: [],
          trace: [],
          timestamp: parseHistoryTimestamp(m.created_at),
          seq: m.seq,
          feedback: m.feedback ?? null,
          pinned: m.pinned ?? false,
        };
      }

      const timeline: TimelineStep[] = bubble.timeline ?? [];
      const trace: TraceEvent[] = bubble.trace ?? [];
      const bubbleIdx = msgs.length;

      if (content.length > 0) {
        const sep = bubble.content && bubble.content.length > 0 ? "\n\n" : "";
        bubble.content = bubble.content + sep + content;
        timeline.push({
          id: `h-t-${bubbleIdx}-${timeline.length}`,
          type: "text",
          text: content,
        });
      }

      toolCalls.forEach((tc, tcIdx) => {
        const name = tc.name ?? "";
        if (!name) return;
        const args = tc.arguments;
        const preview = resultsById.get(tc.id ?? "") ?? resultsByPos[tcIdx];
        const status: "pending" | "done" = preview != null ? "done" : isPartial ? "pending" : "done";
        timeline.push({
          id: `h-c-${bubbleIdx}-${timeline.length}`,
          type: "tool",
          tool: name,
          args,
          result_preview: preview,
          status,
        });
        trace.push({ iter: 0, tool: name, args, result: preview } as TraceEvent);
      });

      bubble.timeline = timeline;
      bubble.trace = trace;
      // Anchor metadata on the latest assistant row in the bubble — that's
      // where the visible reply lands and where feedback/pin/timestamp belong.
      bubble.timestamp = parseHistoryTimestamp(m.created_at);
      bubble.seq = m.seq;
      bubble.feedback = m.feedback ?? null;
      bubble.pinned = m.pinned ?? false;
      // A non-partial latest row means the turn finished cleanly even if an
      // earlier iteration was marked partial — drop the flag so the
      // Retry/Continue banner doesn't appear on a completed bubble.
      bubble.partial = isPartial ? { status: partialStatus } : undefined;

      i = j - 1;
    }

    flush();

    const filtered = msgs.filter((mm) => {
      if (mm.role !== "assistant") return true;
      const hasText = (mm.content ?? "").trim().length > 0;
      const hasTimeline = (mm.timeline ?? []).length > 0;
      const hasPartial = mm.partial != null;
      return hasText || hasTimeline || hasPartial;
    });

    setChatStates((prev) => {
      const next = new Map(prev);
      const cur = next.get(id);
      // Don't clobber in-flight state: if thinking or local-only messages
      // exist for this session already, preserve them; only seed history
      // for sessions we haven't loaded yet.
      if (!forceRefresh && cur && cur.historyLoaded) return prev;
      // If history shows the last message is from the user with no assistant
      // reply after it, the turn never completed on the server. Force thinking
      // off so the UI doesn't show a stuck spinner after a server restart.
      const lastFiltered = filtered[filtered.length - 1];
      const neverReplied = lastFiltered?.role === "user";
      const seedModel = computeSeedModel(cur?.selectedModel);
      next.set(id, {
        messages: filtered,
        thinking: neverReplied ? false : (cur?.thinking ?? false),
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
