import { useEffect, useRef, useState, useCallback } from "react";

interface StepRef {
  slug: string;
  name: string;
  type: string;
}

interface SchemaField {
  slug: string;
  keys?: string[];
  types?: Record<string, string>;
}

interface Props {
  value: string;
  onChange: (val: string) => void;
  steps: StepRef[];
  stepSchemas?: SchemaField[];
  placeholder?: string;
  multiline?: boolean;
  minLines?: number;
}

interface CompletionItem {
  label: string;
  detail?: string;
  insert: string;
}

function getCompletions(
  partial: string,
  steps: StepRef[],
  stepSchemas?: SchemaField[],
): CompletionItem[] {
  const parts = partial.split(".");

  if (parts.length <= 1) {
    const prefix = parts[0].toLowerCase();
    return steps
      .filter((s) => s.slug.toLowerCase().startsWith(prefix) && prefix.length > 0)
      .map((s) => ({
        label: s.slug,
        detail: s.name,
        insert: s.slug + ".",
      }));
  }

  const slug = parts[0];
  const afterSlug = parts.slice(1).join(".");

  if (afterSlug === "") {
    return [
      { label: "result", detail: "step output", insert: "result}}" },
    ];
  }

  const schema = stepSchemas?.find((s) => s.slug === slug);
  if (schema?.keys && afterSlug.length > 0) {
    const pathParts = afterSlug.split(".");
    const lastPart = pathParts[pathParts.length - 1].toLowerCase();

    if (pathParts.length === 1) {
      const matchingKeys = schema.keys.filter((k) => k.toLowerCase().startsWith(lastPart));
      if (matchingKeys.length > 0) {
        return matchingKeys.map((k) => ({
          label: k,
          detail: schema.types?.[k] || "",
          insert: k + "}}",
        }));
      }
    }

    if (pathParts.length === 1 && lastPart === "" && schema.keys.length > 0) {
      return schema.keys.map((k) => ({
        label: k,
        detail: schema.types?.[k] || "",
        insert: k + "}}",
      }));
    }
  }

  if (afterSlug.toLowerCase().startsWith("r")) {
    return [{ label: "result", detail: "step output", insert: "result}}" }];
  }

  return [];
}

export default function TemplateInput({ value, onChange, steps, stepSchemas, placeholder, multiline, minLines }: Props) {
  const ref = useRef<HTMLTextAreaElement | HTMLInputElement>(null);
  const [items, setItems] = useState<CompletionItem[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [triggerInfo, setTriggerInfo] = useState<{ start: number; partial: string } | null>(null);

  const findTrigger = useCallback((text: string, cursor: number): { start: number; partial: string } | null => {
    const before = text.slice(0, cursor);
    const match = before.match(/\{\{steps\.([a-zA-Z0-9._]*)$/);
    if (!match) return null;
    return { start: cursor - match[1].length, partial: match[1] };
  }, []);

  const updateCompletions = useCallback((text: string, cursor: number) => {
    const trigger = findTrigger(text, cursor);
    if (!trigger) {
      setItems([]);
      setTriggerInfo(null);
      return;
    }
    setTriggerInfo(trigger);
    const completions = getCompletions(trigger.partial, steps, stepSchemas);
    setItems(completions);
    setSelectedIdx(0);
  }, [steps, stepSchemas, findTrigger]);

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) {
    const val = e.target.value;
    onChange(val);
    const cursor = (e.target as HTMLTextAreaElement).selectionStart ?? val.length;
    updateCompletions(val, cursor);
  }

  function applyItem(item: CompletionItem) {
    const el = ref.current;
    if (!el || !triggerInfo) return;
    const cursor = el.selectionStart ?? value.length;
    const before = value.slice(0, cursor);
    const after = value.slice(cursor);
    const partial = triggerInfo.partial;
    const lastDot = partial.lastIndexOf(".");
    const prefix = lastDot >= 0 ? partial.slice(0, lastDot + 1) : "";
    const newVal = before + item.insert.slice(prefix.length + (partial.length - prefix.length)) + after;

    if (newVal === value && item.insert.endsWith("}}")) {
      const fixed = before.slice(0, before.length - partial.length) + partial.split(".")[0] + "." + item.insert + after;
      onChange(fixed);
    } else {
      onChange(newVal);
    }

    setItems([]);
    setTriggerInfo(null);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (items.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIdx((i) => (i + 1) % items.length);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIdx((i) => (i - 1 + items.length) % items.length);
      return;
    }
    if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      applyItem(items[selectedIdx]);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setItems([]);
      setTriggerInfo(null);
      return;
    }
  }

  useEffect(() => {
    if (ref.current) {
      const cursor = ref.current.selectionStart ?? value.length;
      const trigger = findTrigger(value, cursor);
      if (!trigger) {
        setItems([]);
        setTriggerInfo(null);
      }
    }
  }, [value, findTrigger]);

  const shared = {
    ref: ref as React.RefObject<HTMLInputElement & HTMLTextAreaElement>,
    value,
    onChange: handleChange,
    onKeyDown: handleKeyDown,
    placeholder,
    className: "wf-template-input",
    autoComplete: "off" as const,
  };

  const dropdown = items.length > 0 && (
    <div className="wf-autocomplete-dropdown">
      {items.map((item, i) => (
        <div
          key={item.label}
          className={`wf-autocomplete-item${i === selectedIdx ? " selected" : ""}`}
          onMouseDown={(e) => { e.preventDefault(); applyItem(item); }}
          onMouseEnter={() => setSelectedIdx(i)}
        >
          <span className="wf-ac-label">{item.label}</span>
          {item.detail && <span className="wf-ac-detail">{item.detail}</span>}
        </div>
      ))}
    </div>
  );

  if (multiline) {
    return (
      <div className="wf-template-wrap">
        <textarea {...shared} style={{ minHeight: minLines ? minLines * 20 : undefined }} />
        {dropdown}
      </div>
    );
  }

  return (
    <div className="wf-template-wrap">
      <input {...shared} />
      {dropdown}
    </div>
  );
}
