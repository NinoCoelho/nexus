import { useCallback, useEffect, useRef, useState } from "react";
import { getBots, searchVaultMentions, type Bot, type VaultNode } from "../../api";

export interface MentionState {
  start: number;
  query: string;
}

/** Lightweight bot reference for the mention picker. */
export interface BotMentionRef {
  id: string;
  handle: string;
  name: string;
  kind: Bot["kind"];
}

// Bots change rarely — one fetch per tab, shared across InputBar mounts.
let botsCache: BotMentionRef[] | null = null;
let botsPromise: Promise<BotMentionRef[]> | null = null;

function fetchBotMentions(): Promise<BotMentionRef[]> {
  if (botsCache) return Promise.resolve(botsCache);
  if (!botsPromise) {
    botsPromise = getBots()
      .then((bots) => {
        botsCache = bots.map((b) => ({ id: b.id, handle: b.handle || b.id, name: b.name, kind: b.kind }));
        return botsCache;
      })
      .catch(() => {
        botsPromise = null;
        return [] as BotMentionRef[];
      });
  }
  return botsPromise;
}

export function useMentionPicker(value: string, onChange: (v: string) => void) {
  const [mentionResults, setMentionResults] = useState<VaultNode[]>([]);
  const [mentionLoading, setMentionLoading] = useState(false);
  const [mention, setMention] = useState<MentionState | null>(null);
  const [bots, setBots] = useState<BotMentionRef[]>(botsCache ?? []);
  const mentionFetchSeq = useRef(0);
  const mentionDebounceRef = useRef<number | null>(null);

  /** Inspect the text around the cursor to detect an active `@query` token. */
  const detectMention = useCallback((text: string, caret: number): MentionState | null => {
    // Walk back from caret to find an `@` preceded by start-of-string or whitespace.
    let i = caret - 1;
    while (i >= 0) {
      const ch = text[i];
      if (ch === "@") {
        const prev = i === 0 ? " " : text[i - 1];
        if (/\s/.test(prev) || i === 0) {
          const query = text.slice(i + 1, caret);
          // bail if query contains whitespace or newline — user moved on
          if (/\s/.test(query)) return null;
          return { start: i, query };
        }
        return null;
      }
      if (/\s/.test(ch)) return null;
      i--;
    }
    return null;
  }, []);

  // Load the bot list once, lazily, on the first `@` mention.
  useEffect(() => {
    if (!mention || bots.length > 0) return;
    let cancelled = false;
    fetchBotMentions().then((list) => {
      if (!cancelled) setBots(list);
    });
    return () => { cancelled = true; };
  }, [mention, bots.length]);

  // Debounced server fetch keyed on the active mention query.
  useEffect(() => {
    if (!mention) {
      setMentionResults([]);
      setMentionLoading(false);
      return;
    }
    if (mentionDebounceRef.current) window.clearTimeout(mentionDebounceRef.current);
    setMentionLoading(true);
    const seq = ++mentionFetchSeq.current;
    mentionDebounceRef.current = window.setTimeout(async () => {
      try {
        const results = await searchVaultMentions(mention.query, 8);
        if (seq === mentionFetchSeq.current) {
          setMentionResults(results);
          setMentionLoading(false);
        }
      } catch {
        if (seq === mentionFetchSeq.current) {
          setMentionResults([]);
          setMentionLoading(false);
        }
      }
    }, 80);
    return () => {
      if (mentionDebounceRef.current) window.clearTimeout(mentionDebounceRef.current);
    };
  }, [mention]);

  const focusCaret = (textareaRef: React.RefObject<HTMLTextAreaElement | null>, caret: number) => {
    requestAnimationFrame(() => {
      const e = textareaRef.current;
      if (e) {
        e.focus();
        e.setSelectionRange(caret, caret);
        e.style.height = "auto";
        e.style.height = `${Math.min(e.scrollHeight, 144)}px`;
      }
    });
  };

  const insertMention = useCallback((node: VaultNode, textareaRef: React.RefObject<HTMLTextAreaElement | null>) => {
    if (!mention) return;
    const el = textareaRef.current;
    const text = value;
    const caret = el?.selectionStart ?? text.length;
    const name = node.path.split("/").pop() || node.path;
    const link = `[${name}](vault://${node.path}) `;
    const next = text.slice(0, mention.start) + link + text.slice(caret);
    onChange(next);
    setMention(null);
    focusCaret(textareaRef, mention.start + link.length);
  }, [mention, value, onChange]);

  /** Accept a bot from the picker — completes to `@handle ` (plain text;
   * the backend routes the mention server-side). */
  const insertBotMention = useCallback((bot: BotMentionRef, textareaRef: React.RefObject<HTMLTextAreaElement | null>) => {
    if (!mention) return;
    const el = textareaRef.current;
    const text = value;
    const caret = el?.selectionStart ?? text.length;
    const token = `@${bot.handle} `;
    const next = text.slice(0, mention.start) + token + text.slice(caret);
    onChange(next);
    setMention(null);
    focusCaret(textareaRef, mention.start + token.length);
  }, [mention, value, onChange]);

  return { mention, setMention, mentionResults, mentionLoading, bots, detectMention, insertMention, insertBotMention };
}
