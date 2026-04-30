import { useEffect, useState } from "react";

interface Props {
  value: number;
  defaultValue: number;
  onCommit: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
}

export default function NumberFieldWithDefault({
  value,
  defaultValue,
  onCommit,
  min,
  max,
  step = 1,
  disabled,
}: Props) {
  const [text, setText] = useState(String(value));

  useEffect(() => {
    setText(String(value));
  }, [value]);

  const commit = () => {
    const parsed = Number(text);
    if (Number.isNaN(parsed)) {
      setText(String(value));
      return;
    }
    let next = parsed;
    if (typeof min === "number") next = Math.max(min, next);
    if (typeof max === "number") next = Math.min(max, next);
    if (next !== value) onCommit(next);
    setText(String(next));
  };

  const reset = () => {
    if (defaultValue !== value) onCommit(defaultValue);
    setText(String(defaultValue));
  };

  const isDefault = Number(text) === defaultValue;

  return (
    <div className="s-number">
      <input
        className="s-number__input"
        type="number"
        value={text}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
      <button
        type="button"
        className="s-number__reset"
        onClick={reset}
        disabled={isDefault || disabled}
        title={`Restore default (${defaultValue})`}
      >
        Restore default
      </button>
    </div>
  );
}
