import { useRef, useState, useCallback } from "react";

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
  triggerKeys?: string[];
  varNames?: string[];
  placeholder?: string;
  multiline?: boolean;
  minLines?: number;
  className?: string;
  style?: React.CSSProperties;
}

interface CompletionItem {
  label: string;
  detail?: string;
  insert: string;
}

const RUNTIME_VARS: CompletionItem[] = [
  { label: "now", detail: "ISO timestamp (2026-05-31T12:00:00+00:00)", insert: "now}}" },
  { label: "date", detail: "Current date (2026-05-31)", insert: "date}}" },
  { label: "time", detail: "Current time (12-00-00)", insert: "time}}" },
  { label: "uuid", detail: "Random UUID", insert: "uuid}}" },
  { label: "timestamp", detail: "Unix timestamp (1748689200)", insert: "timestamp}}" },
  { label: "trigger.", detail: "Trigger payload", insert: "trigger." },
  { label: "steps.", detail: "Step outputs", insert: "steps." },
  { label: "vars.", detail: "Workflow variables", insert: "vars." },
];

function getCompletions(
  partial: string,
  prefix: string,
  steps: StepRef[],
  stepSchemas?: SchemaField[],
  triggerKeys?: string[],
  varNames?: string[],
): CompletionItem[] {
  if (prefix === "") {
    const lower = partial.toLowerCase();
    const matchingRuntime = RUNTIME_VARS.filter(
      (v) => v.label.toLowerCase().startsWith(lower),
    );
    const matchingSteps = steps
      .filter((s) => s.slug.toLowerCase().startsWith(lower) && lower.length > 0)
      .map((s) => ({
        label: s.slug,
        detail: s.name,
        insert: s.slug + ".",
      }));
    return [...matchingRuntime, ...matchingSteps];
  }

  if (prefix === "trigger") {
    if (!triggerKeys || triggerKeys.length === 0) {
      return [{ label: "(payload)", detail: "trigger payload object", insert: partial + "}}" }];
    }
    const afterDot = partial.split(".").slice(1).join(".");
    const lastPart = afterDot.toLowerCase();
    if (!afterDot) {
      return triggerKeys.map((k) => ({ label: k, detail: "trigger field", insert: k + "}}" }));
    }
    return triggerKeys
      .filter((k) => k.toLowerCase().startsWith(lastPart))
      .map((k) => ({ label: k, detail: "trigger field", insert: k + "}}" }));
  }

  if (prefix === "vars") {
    if (!varNames || varNames.length === 0) {
      return [{ label: "(variables)", detail: "workflow variables", insert: partial + "}}" }];
    }
    const afterDot = partial.split(".").slice(1).join(".");
    const lastPart = afterDot.toLowerCase();
    if (!afterDot) {
      return varNames.map((v) => ({ label: v, detail: "variable", insert: v + "}}" }));
    }
    return varNames
      .filter((v) => v.toLowerCase().startsWith(lastPart))
      .map((v) => ({ label: v, detail: "variable", insert: v + "}}" }));
  }

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

export default function TemplateInput({ value, onChange, steps, stepSchemas, triggerKeys, varNames, placeholder, multiline, minLines, className, style }: Props) {
  const ref = useRef<HTMLTextAreaElement | HTMLInputElement>(null);
  const cursorRef = useRef(0);
  const [items, setItems] = useState<CompletionItem[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [triggerInfo, setTriggerInfo] = useState<{ start: number; partial: string; prefix: string } | null>(null);

  const findTrigger = useCallback((text: string, cursor: number): { start: number; partial: string; prefix: string } | null => {
    const before = text.slice(0, cursor);
    const match = before.match(/\{\{((?:trigger|steps|vars)\.([a-zA-Z0-9_.]*))$/);
    if (match) {
      const fullPartial = match[1];
      const prefix = fullPartial.split(".")[0];
      return { start: cursor - fullPartial.length, partial: fullPartial.slice(prefix.length + 1), prefix };
    }
    const bareMatch = before.match(/\{\{([a-zA-Z0-9_]*)$/);
    if (bareMatch) {
      const partial = bareMatch[1];
      return { start: cursor - partial.length, partial, prefix: "" };
    }
    return null;
  }, []);

  const updateCompletions = useCallback((text: string, cursor: number) => {
    cursorRef.current = cursor;
    const trigger = findTrigger(text, cursor);
    if (!trigger) {
      setItems([]);
      setTriggerInfo(null);
      return;
    }
    setTriggerInfo(trigger);
    const completions = getCompletions(trigger.partial, trigger.prefix, steps, stepSchemas, triggerKeys, varNames);
    setItems(completions);
    setSelectedIdx(0);
  }, [steps, stepSchemas, triggerKeys, varNames, findTrigger]);

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) {
    const val = e.target.value;
    const cursor = (e.target as HTMLTextAreaElement).selectionStart ?? val.length;
    onChange(val);
    updateCompletions(val, cursor);
  }

  function applyItem(item: CompletionItem) {
    console.log("[ac] applyItem", { item, triggerInfo, cursorRef: cursorRef.current, value });
    if (!triggerInfo) return;
    const cursor = cursorRef.current;
    const after = value.slice(cursor);
    const partialStart = cursor - triggerInfo.partial.length;
    const next = value.slice(0, partialStart) + item.insert + after;
    console.log("[ac] next value:", next);
    onChange(next);
    setItems([]);
    setTriggerInfo(null);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    console.log("[ac] keydown", e.key, { itemsLen: items.length, selectedIdx });
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

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    const text = e.dataTransfer.getData("text/plain");
    if (!text) return;
    const el = ref.current;
    if (!el) {
      onChange(value + text);
      return;
    }
    const cursor = el.selectionStart ?? value.length;
    onChange(value.slice(0, cursor) + text + value.slice(cursor));
    setItems([]);
    setTriggerInfo(null);
  }

  const shared = {
    ref: ref as React.RefObject<HTMLInputElement & HTMLTextAreaElement>,
    value,
    onChange: handleChange,
    onKeyDown: handleKeyDown,
    onBlur: () => { setItems([]); setTriggerInfo(null); },
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); (e.currentTarget as HTMLElement).focus(); },
    onDrop: handleDrop,
    placeholder,
    className: className || "wf-template-input",
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

  const wrapStyle = multiline && style ? { ...style, display: "flex", flexDirection: "column" as const } : undefined;

  if (multiline) {
    return (
      <div className="wf-template-wrap" style={wrapStyle}>
        <textarea {...shared} style={multiline && !className ? { minHeight: minLines ? minLines * 20 : undefined } : undefined} />
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
