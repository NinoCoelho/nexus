import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FieldSchema } from "../../types/form";
import { useRefOptions, type RefOption } from "../datatable/refOptions";
import RefSearchPopup from "./RefSearchPopup";
import "./Combobox.css";

interface RefComboboxProps {
  field: FieldSchema;
  hostPath: string;
  value: unknown;
  onChange: (v: unknown) => void;
  className?: string;
  autoFocus?: boolean;
  onBlur?: () => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
}

export default function RefCombobox({
  field,
  hostPath,
  value,
  onChange,
  className = "form-input",
  autoFocus = false,
  onBlur,
  onKeyDown,
}: RefComboboxProps) {
  const cardinality = field.cardinality ?? "one";
  const { options, error } = useRefOptions(field, hostPath);
  const [showSearch, setShowSearch] = useState(false);

  if (cardinality === "many") {
    return (
      <>
        <MultiRefCombobox
          field={field}
          hostPath={hostPath}
          value={value}
          onChange={onChange}
          options={options}
          error={error}
          className={className}
          autoFocus={autoFocus}
          onBlur={onBlur}
          onKeyDown={onKeyDown}
          onOpenSearch={() => setShowSearch(true)}
        />
        {showSearch && (
          <RefSearchPopup
            field={field}
            hostPath={hostPath}
            onSelect={(id) => {
              const arr = Array.isArray(value) ? [...(value as string[])] : value ? [String(value)] : [];
              if (!arr.includes(id)) arr.push(id);
              onChange(arr);
            }}
            onClose={() => setShowSearch(false)}
          />
        )}
      </>
    );
  }

  return (
    <>
      <SingleRefCombobox
        options={options}
        error={error}
        value={String(value ?? "")}
        onChange={onChange}
        className={className}
        autoFocus={autoFocus}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        onOpenSearch={() => setShowSearch(true)}
      />
      {showSearch && (
        <RefSearchPopup
          field={field}
          hostPath={hostPath}
          onSelect={(id) => {
            onChange(id);
            setShowSearch(false);
          }}
          onClose={() => setShowSearch(false)}
        />
      )}
    </>
  );
}

interface SingleRefComboboxProps {
  options: RefOption[] | null;
  error: string | null;
  value: string;
  onChange: (v: unknown) => void;
  className: string;
  autoFocus: boolean;
  onBlur?: () => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  onOpenSearch: () => void;
}

function SingleRefCombobox({
  options,
  error,
  value,
  onChange,
  className,
  autoFocus,
  onBlur,
  onKeyDown,
  onOpenSearch,
}: SingleRefComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const selectedLabel = useMemo(() => {
    if (!value || !options) return "";
    const opt = options.find((o) => o.id === value);
    return opt ? opt.label : value;
  }, [value, options]);

  const filtered = useMemo(() => {
    if (!options) return [];
    if (!query.trim()) return options;
    const q = query.toLowerCase();
    return options.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, query]);

  const displayValue = open ? query : selectedLabel;

  useEffect(() => {
    if (autoFocus && inputRef.current) inputRef.current.focus();
  }, [autoFocus]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  useEffect(() => {
    if (highlightIdx >= 0 && listRef.current) {
      const item = listRef.current.children[highlightIdx] as HTMLElement;
      item?.scrollIntoView({ block: "nearest" });
    }
  }, [highlightIdx]);

  const selectOption = useCallback(
    (opt: RefOption) => {
      onChange(opt.id);
      setOpen(false);
      setQuery("");
      setHighlightIdx(-1);
    },
    [onChange],
  );

  const clearValue = useCallback(() => {
    onChange("");
    inputRef.current?.focus();
  }, [onChange]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIdx((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIdx((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlightIdx >= 0 && filtered[highlightIdx]) {
        selectOption(filtered[highlightIdx]);
      } else if (open) {
        setOpen(false);
        setQuery("");
      } else if (onKeyDown) {
        onKeyDown(e);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (open) {
        setOpen(false);
        setQuery("");
      } else if (onKeyDown) {
        onKeyDown(e);
      }
    } else {
      onKeyDown?.(e);
    }
  }

  if (options === null) {
    return <input className={className} disabled value="Loading..." />;
  }

  if (error) {
    return (
      <input
        className={className}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        title={`Load failed: ${error}`}
        autoFocus={autoFocus}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      />
    );
  }

  return (
    <div className="cbx" ref={containerRef}>
      <div className="cbx-input-wrap">
        <input
          ref={inputRef}
          className={`${className} cbx-input`}
          value={displayValue}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
            setHighlightIdx(-1);
          }}
          onFocus={() => {
            setOpen(true);
            setQuery("");
          }}
          onBlur={() => {
            setTimeout(() => {
              setOpen(false);
              setQuery("");
              onBlur?.();
            }, 150);
          }}
          onKeyDown={handleKeyDown}
          placeholder={value ? "" : "Type to filter or search..."}
          autoComplete="off"
        />
        <div className="cbx-icons">
          {value && (
            <button
              type="button"
              className="cbx-icon-btn cbx-clear"
              onMouseDown={(e) => e.preventDefault()}
              onClick={clearValue}
              title="Clear"
            >
              ×
            </button>
          )}
          <button
            type="button"
            className="cbx-icon-btn cbx-search"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              setOpen(false);
              onOpenSearch();
            }}
            title="Search all fields"
          >
            ⊞
          </button>
        </div>
      </div>
      {open && filtered.length === 0 && query.trim() && (
        <ul className="cbx-list" ref={listRef}>
          <li className="cbx-empty">No matches — try the search button →</li>
        </ul>
      )}
      {open && (filtered.length > 0 || !query.trim()) && (
        <ul className="cbx-list" ref={listRef}>
          {!value && (
            <li
              className={`cbx-item${highlightIdx === -1 ? " cbx-active" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                onChange("");
                setOpen(false);
                setQuery("");
              }}
            >
              — none —
            </li>
          )}
          {filtered.map((opt, idx) => (
            <li
              key={opt.id}
              className={`cbx-item${opt.id === value ? " cbx-selected" : ""}${idx === highlightIdx ? " cbx-active" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => selectOption(opt)}
              onMouseEnter={() => setHighlightIdx(idx)}
            >
              {opt.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface MultiRefComboboxProps {
  field: FieldSchema;
  hostPath: string;
  value: unknown;
  onChange: (v: unknown) => void;
  options: RefOption[] | null;
  error: string | null;
  className: string;
  autoFocus: boolean;
  onBlur?: () => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  onOpenSearch: () => void;
}

function MultiRefCombobox({
  value,
  onChange,
  options,
  error,
  className,
  autoFocus,
  onBlur,
  onKeyDown,
  onOpenSearch,
}: MultiRefComboboxProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const listRef = useRef<HTMLUListElement>(null);

  const selected = useMemo(() => {
    const arr = Array.isArray(value) ? (value as unknown[]) : value ? [value] : [];
    return arr.map(String);
  }, [value]);

  const selectedLabels = useMemo((): { id: string; label: string }[] => {
    return selected.map((id) => {
      const opt = options?.find((o) => o.id === id);
      return { id, label: opt ? opt.label : id };
    });
  }, [selected, options]);

  const filtered = useMemo(() => {
    if (!options) return [];
    const unselected = options.filter((o) => !selected.includes(o.id));
    if (!query.trim()) return unselected.slice(0, 50);
    const q = query.toLowerCase();
    return unselected.filter((o) => o.label.toLowerCase().includes(q)).slice(0, 50);
  }, [options, selected, query]);

  useEffect(() => {
    if (autoFocus && inputRef.current) inputRef.current.focus();
  }, [autoFocus]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    if (open) document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  useEffect(() => {
    if (highlightIdx >= 0 && listRef.current) {
      const item = listRef.current.children[highlightIdx] as HTMLElement;
      item?.scrollIntoView({ block: "nearest" });
    }
  }, [highlightIdx]);

  const addRef = useCallback(
    (id: string) => {
      if (!selected.includes(id)) {
        onChange([...selected, id]);
      }
      setQuery("");
      inputRef.current?.focus();
    },
    [selected, onChange],
  );

  const removeRef = useCallback(
    (id: string) => {
      onChange(selected.filter((s) => s !== id));
    },
    [selected, onChange],
  );

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Backspace" && !query && selected.length > 0) {
      removeRef(selected[selected.length - 1]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIdx((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIdx((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter" && highlightIdx >= 0 && filtered[highlightIdx]) {
      e.preventDefault();
      addRef(filtered[highlightIdx].id);
      setHighlightIdx(-1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      setQuery("");
    } else {
      onKeyDown?.(e);
    }
  }

  if (options === null) {
    return <input className={className} disabled value="Loading..." />;
  }
  if (error) {
    return (
      <input
        className={className}
        value={selected.join(", ")}
        onChange={(e) => onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
        autoFocus={autoFocus}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        placeholder="comma-separated IDs"
      />
    );
  }

  return (
    <div className="cbx cbx-multi" ref={containerRef}>
      <div className="cbx-chips">
        {selectedLabels.map(({ id, label }) => (
          <span key={id} className="cbx-chip">
            {label}
            <button
              type="button"
              className="cbx-chip-remove"
              onClick={(e) => {
                e.stopPropagation();
                removeRef(id);
              }}
            >
              ×
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          className={`${className} cbx-chip-input`}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
            setHighlightIdx(-1);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            setTimeout(() => {
              setOpen(false);
              setQuery("");
              onBlur?.();
            }, 150);
          }}
          onKeyDown={handleKeyDown}
          placeholder={selected.length === 0 ? "Type to filter or search..." : ""}
          autoComplete="off"
        />
        <button
          type="button"
          className="cbx-icon-btn cbx-search"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            setOpen(false);
            onOpenSearch();
          }}
          title="Search all fields"
        >
          ⊞
        </button>
      </div>
      {open && filtered.length > 0 && (
        <ul className="cbx-list" ref={listRef}>
          {filtered.map((opt, idx) => (
            <li
              key={opt.id}
              className={`cbx-item${idx === highlightIdx ? " cbx-active" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => {
                addRef(opt.id);
                setHighlightIdx(-1);
              }}
              onMouseEnter={() => setHighlightIdx(idx)}
            >
              {opt.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
