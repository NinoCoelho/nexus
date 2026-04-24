/**
 * HoverTooltip — delayed tooltip that appears after hovering for `delay` ms.
 *
 * Renders the wrapped children inline; when the mouse has been over them
 * continuously for `delay` ms, a position:fixed bubble appears near the
 * cursor with `content`. Clears immediately on mouse-leave, click, or
 * unmount.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import "./HoverTooltip.css";

interface Props {
  /** Tooltip body. If null/undefined, the wrapper is a no-op pass-through. */
  content: React.ReactNode;
  /** ms to wait before showing. Default 2000. */
  delay?: number;
  children: React.ReactNode;
  /** Applied to the wrapper span. */
  className?: string;
}

export default function HoverTooltip({ content, delay = 2000, children, className }: Props) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastMouseRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });

  const clear = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setPos(null);
  }, []);

  useEffect(() => () => clear(), [clear]);

  if (content == null || content === false || content === "") {
    return <span className={className}>{children}</span>;
  }

  const handleEnter = (e: React.MouseEvent) => {
    lastMouseRef.current = { x: e.clientX, y: e.clientY };
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setPos({ ...lastMouseRef.current });
    }, delay);
  };

  const handleMove = (e: React.MouseEvent) => {
    lastMouseRef.current = { x: e.clientX, y: e.clientY };
    // Once shown, keep tooltip near cursor.
    if (pos) setPos({ x: e.clientX, y: e.clientY });
  };

  return (
    <span
      className={className}
      onMouseEnter={handleEnter}
      onMouseMove={handleMove}
      onMouseLeave={clear}
      onMouseDown={clear}
    >
      {children}
      {pos && (
        <div
          className="hover-tooltip"
          style={{
            // Offset from cursor; clamp to viewport.
            left: Math.min(pos.x + 12, window.innerWidth - 260),
            top: Math.min(pos.y + 16, window.innerHeight - 80),
          }}
          role="tooltip"
        >
          {content}
        </div>
      )}
    </span>
  );
}
