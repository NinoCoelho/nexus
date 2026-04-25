import { useCallback, useEffect, useRef, useState } from "react";
import { searchVaultMentions, type VaultNode } from "../../api";

export interface MentionState {
  start: number;
  query: string;
}

export function useMentionPicker(value: string, onChange: (v: string) => void) {
  const [mentionResults, setMentionResults] = useState<VaultNode[]>([]);
  const [mentionLoading, setMentionLoading] = useState(false);
  const [mention, setMention] = useState<MentionState | null>(null);
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

  const insertMention = useCallback((node: VaultNode, textareaRef: React.RefObject<HTMLTextAreaElement | null>) => {
    if (!mention) return;
    const el = textareaRef.current;
    const text = value;
    const caret = el?.selectionStart ?? text.length;
    const name = node.path.split("/").pop() || node.path;
    const link = `[${name}](vault://${node.path}) `;
    const next = text.slice(0, mention.start) + link + text.slice(caret);
    const newCaret = mention.start + link.length;
    onChange(next);
    setMention(null);
    requestAnimationFrame(() => {
      const e = textareaRef.current;
      if (e) {
        e.focus();
        e.setSelectionRange(newCaret, newCaret);
        e.style.height = "auto";
        e.style.height = `${Math.min(e.scrollHeight, 144)}px`;
      }
    });
  }, [mention, value, onChange]);

  return { mention, setMention, mentionResults, mentionLoading, detectMention, insertMention };
}
