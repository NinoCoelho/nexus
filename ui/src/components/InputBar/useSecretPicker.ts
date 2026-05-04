import { useCallback, useEffect, useRef, useState } from "react";
import { listCredentials } from "../../api";

export interface SecretState {
  start: number;
  query: string;
}

export function useSecretPicker(value: string, onChange: (v: string) => void) {
  const [secretResults, setSecretResults] = useState<string[]>([]);
  const [secret, setSecret] = useState<SecretState | null>(null);
  const cacheRef = useRef<string[] | null>(null);
  const fetchSeqRef = useRef(0);

  const detectSecret = useCallback(
    (text: string, caret: number): SecretState | null => {
      let i = caret - 1;
      while (i >= 0) {
        const ch = text[i];
        if (ch === "$") {
          const prev = i === 0 ? " " : text[i - 1];
          if (/\s/.test(prev) || i === 0) {
            const query = text.slice(i + 1, caret);
            if (/\s/.test(query)) return null;
            return { start: i, query };
          }
          return null;
        }
        if (/\s/.test(ch)) return null;
        i--;
      }
      return null;
    },
    [],
  );

  useEffect(() => {
    if (!secret) {
      setSecretResults([]);
      return;
    }

    const applyFilter = () => {
      const names = cacheRef.current ?? [];
      const q = secret.query.toUpperCase();
      setSecretResults(q ? names.filter((n) => n.includes(q)) : names);
    };

    if (cacheRef.current === null) {
      const seq = ++fetchSeqRef.current;
      listCredentials()
        .then((creds) => {
          if (seq !== fetchSeqRef.current) return;
          cacheRef.current = creds.map((c) => c.name);
          applyFilter();
        })
        .catch(() => {
          if (seq !== fetchSeqRef.current) return;
          cacheRef.current = [];
          setSecretResults([]);
        });
    } else {
      applyFilter();
    }
  }, [secret]);

  const insertSecret = useCallback(
    (name: string, textareaRef: React.RefObject<HTMLTextAreaElement | null>) => {
      if (!secret) return;
      const el = textareaRef.current;
      const text = value;
      const caret = el?.selectionStart ?? text.length;
      const replacement = `$${name} `;
      const next = text.slice(0, secret.start) + replacement + text.slice(caret);
      const newCaret = secret.start + replacement.length;
      onChange(next);
      setSecret(null);
      requestAnimationFrame(() => {
        const e = textareaRef.current;
        if (e) {
          e.focus();
          e.setSelectionRange(newCaret, newCaret);
          e.style.height = "auto";
          e.style.height = `${Math.min(e.scrollHeight, 144)}px`;
        }
      });
    },
    [secret, value, onChange],
  );

  return { secret, setSecret, secretResults, detectSecret, insertSecret };
}
