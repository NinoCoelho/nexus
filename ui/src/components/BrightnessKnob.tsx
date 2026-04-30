/**
 * BrightnessKnob — quick-access brightness slider.
 *
 * Mirrors the slider in Settings → Appearance so the user can rescue
 * legibility (when text/bg drift to the same tone at one extreme) without
 * opening the drawer.
 *
 * Two render modes:
 *  - expanded: inline horizontal slider, sun/moon icons flanking it.
 *  - collapsed: single icon button that opens a tiny popover anchored to
 *    the right of the sidebar with the same slider.
 */

import { useEffect, useRef, useState } from "react";
import { useTheme } from "../theme/ThemeContext";

function IconMoon({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M6 0a1 1 0 0 0-.89.55 8 8 0 1 0 10.34 10.34A1 1 0 0 0 14.56 9A6.5 6.5 0 0 1 6 0z"/>
    </svg>
  );
}

function IconSun({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM8 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0V.75A.75.75 0 0 1 8 0zm0 13a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 8 13zM2.34 2.34a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 0 1-1.06 1.06L2.34 3.4a.75.75 0 0 1 0-1.06zm9.2 9.2a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 1 1-1.06 1.06l-1.06-1.06a.75.75 0 0 1 0-1.06zM0 8a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5H.75A.75.75 0 0 1 0 8zm13 0a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5h-1.5A.75.75 0 0 1 13 8zM2.34 13.66a.75.75 0 0 1 0-1.06l1.06-1.06a.75.75 0 1 1 1.06 1.06L3.4 13.66a.75.75 0 0 1-1.06 0zm9.2-9.2a.75.75 0 0 1 0-1.06l1.06-1.06a.75.75 0 1 1 1.06 1.06l-1.06 1.06a.75.75 0 0 1-1.06 0z"/>
    </svg>
  );
}

function Slider({ brightness, onChange }: { brightness: number; onChange: (b: number) => void }) {
  return (
    <div className="brightness-knob__row">
      <IconMoon className="brightness-knob__icon" />
      <input
        type="range"
        className="brightness-knob__slider"
        min="0"
        max="1"
        step="0.01"
        value={brightness}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        aria-label="Brightness"
        title="Brightness — drag to adjust between dark and light"
      />
      <IconSun className="brightness-knob__icon" />
    </div>
  );
}

export default function BrightnessKnob({ collapsed }: { collapsed: boolean }) {
  const { brightness, setBrightness } = useTheme();
  const [open, setOpen] = useState(false);
  const popRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popRef.current?.contains(t) || btnRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!collapsed) {
    return (
      <div className="brightness-knob">
        <Slider brightness={brightness} onChange={setBrightness} />
      </div>
    );
  }

  return (
    <div className="brightness-knob brightness-knob--collapsed">
      <button
        ref={btnRef}
        className="sidebar-nav-item brightness-knob__btn"
        onClick={() => setOpen((v) => !v)}
        title="Brightness"
        aria-label="Brightness"
        aria-expanded={open}
      >
        <span className="sidebar-nav-icon">
          {brightness >= 0.5 ? <IconSun /> : <IconMoon />}
        </span>
      </button>
      {open && (
        <div ref={popRef} className="brightness-knob__popover" role="dialog" aria-label="Brightness">
          <Slider brightness={brightness} onChange={setBrightness} />
        </div>
      )}
    </div>
  );
}
