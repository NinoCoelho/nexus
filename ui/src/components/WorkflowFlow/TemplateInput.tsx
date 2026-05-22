import { useEffect, useRef, useState } from "react";

interface StepRef {
  slug: string;
  name: string;
  type: string;
}

interface Props {
  value: string;
  onChange: (val: string) => void;
  steps: StepRef[];
  placeholder?: string;
  multiline?: boolean;
  minLines?: number;
}

export default function TemplateInput({ value, onChange, steps, placeholder, multiline, minLines }: Props) {
  const ref = useRef<HTMLTextAreaElement | HTMLInputElement>(null);
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [suggestionSlug, setSuggestionSlug] = useState("");

  function findTrigger(text: string, cursor: number): { start: number; partial: string } | null {
    const before = text.slice(0, cursor);
    const match = before.match(/\{\{steps\.([a-zA-Z0-9]*)$/);
    if (!match) return null;
    return { start: cursor - match[0].length, partial: match[1] };
  }

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) {
    const val = e.target.value;
    onChange(val);

    const cursor = (e.target as HTMLTextAreaElement).selectionStart ?? val.length;
    const trigger = findTrigger(val, cursor);
    if (trigger) {
      const partial = trigger.partial.toLowerCase();
      const match = steps.find((s) => s.slug.toLowerCase().startsWith(partial) && partial.length > 0);
      if (match) {
        setSuggestionSlug(match.slug);
        setSuggestion(match.slug.slice(partial.length));
      } else {
        setSuggestion(null);
      }
    } else {
      setSuggestion(null);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Tab" && suggestion !== null) {
      e.preventDefault();
      const el = ref.current;
      if (!el) return;
      const cursor = el.selectionStart ?? value.length;
      const before = value.slice(0, cursor);
      const after = value.slice(cursor);
      const newVal = before + suggestion + `.result}}` + after;
      onChange(newVal);
      setSuggestion(null);

      requestAnimationFrame(() => {
        const newCursor = before.length + suggestion.length + `.result}}`.length;
        el.setSelectionRange(newCursor, newCursor);
      });
    }
  }

  useEffect(() => {
    if (suggestion !== null && ref.current) {
      const cursor = ref.current.selectionStart ?? value.length;
      const trigger = findTrigger(value, cursor);
      if (!trigger) setSuggestion(null);
    }
  }, [value, suggestion]);

  const shared = {
    ref: ref as React.RefObject<HTMLInputElement & HTMLTextAreaElement>,
    value,
    onChange: handleChange,
    onKeyDown: handleKeyDown,
    placeholder,
    className: "wf-template-input",
    autoComplete: "off" as const,
  };

  if (multiline) {
    return (
      <div className="wf-template-wrap">
        <textarea {...shared} style={{ minHeight: minLines ? minLines * 20 : undefined }} />
        {suggestion !== null && (
          <div className="wf-template-hint">
            Tab to accept: <code>{suggestionSlug}</code>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="wf-template-wrap">
      <input {...shared} />
      {suggestion !== null && (
        <div className="wf-template-hint">
          Tab to accept: <code>{suggestionSlug}</code>
        </div>
      )}
    </div>
  );
}
