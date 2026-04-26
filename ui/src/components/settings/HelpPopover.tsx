import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

interface Props {
  title: string;
  body: ReactNode;
}

export default function HelpPopover({ title, body }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const btnRef = useRef<HTMLButtonElement>(null);

  const toggle = useCallback(() => {
    if (open) {
      setOpen(false);
      return;
    }
    const r = btnRef.current?.getBoundingClientRect();
    if (r) {
      const popoverW = 360;
      const left = Math.max(8, Math.min(window.innerWidth - popoverW - 8, r.right - popoverW));
      setPos({ top: r.bottom + 6, left });
    }
    setOpen(true);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className="s-help-btn"
        onClick={toggle}
        aria-label={`Help: ${title}`}
        title={`Help: ${title}`}
      >
        ?
      </button>
      {open && (
        <>
          <div className="s-help-popover-backdrop" onClick={() => setOpen(false)} />
          <div
            className="s-help-popover"
            style={{ top: pos.top, left: pos.left }}
            role="dialog"
            aria-label={title}
          >
            <div className="s-help-popover__title">{title}</div>
            <div className="s-help-popover__body">{body}</div>
          </div>
        </>
      )}
    </>
  );
}
