import type { ReactNode } from "react";
import { useTheme } from "../theme/ThemeContext";

interface Props {
  onReset: () => void;
  yoloMode?: boolean;
  onOpenMobileDrawer?: () => void;
  statusSlot?: ReactNode;
  notificationSlot?: ReactNode;
}

export default function Header({ onReset, yoloMode = false, onOpenMobileDrawer, statusSlot, notificationSlot }: Props) {
  const { darkMode, toggleDarkMode } = useTheme();

  return (
    <header className="header">
      <div className="header-inner">
        <div className="header-left">
          {onOpenMobileDrawer && (
            <button
              type="button"
              className="header-btn mobile-only"
              onClick={onOpenMobileDrawer}
              aria-label="Open menu"
              style={{ marginRight: 8 }}
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="4" y1="6" x2="20" y2="6" />
                <line x1="4" y1="12" x2="20" y2="12" />
                <line x1="4" y1="18" x2="20" y2="18" />
              </svg>
            </button>
          )}
          <div className="header-identity">
            <span className="header-name">Nexus</span>
            <span className="header-status">● Active Now</span>
          </div>
        </div>
        <div className="header-actions">
          {statusSlot}
          {notificationSlot}
          {yoloMode && (
            <span className="yolo-badge" title="YOLO mode: confirm prompts auto-approved">
              YOLO ON
            </span>
          )}
          <button
            className="header-btn"
            onClick={toggleDarkMode}
            title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
            aria-label={darkMode ? "Switch to light mode" : "Switch to dark mode"}
          >
            {darkMode ? (
              <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM8 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0V.75A.75.75 0 0 1 8 0zm0 13a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 8 13zM2.34 2.34a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 0 1-1.06 1.06L2.34 3.4a.75.75 0 0 1 0-1.06zm9.2 9.2a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 1 1-1.06 1.06l-1.06-1.06a.75.75 0 0 1 0-1.06zM0 8a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5H.75A.75.75 0 0 1 0 8zm13 0a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5h-1.5A.75.75 0 0 1 13 8zM2.34 13.66a.75.75 0 0 1 0-1.06l1.06-1.06a.75.75 0 1 1 1.06 1.06L3.4 13.66a.75.75 0 0 1-1.06 0zm9.2-9.2a.75.75 0 0 1 0-1.06l1.06-1.06a.75.75 0 1 1 1.06 1.06l-1.06 1.06a.75.75 0 0 1-1.06 0z" />
              </svg>
            ) : (
              <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor">
                <path d="M6 0a1 1 0 0 0-.89.55 8 8 0 1 0 10.34 10.34A1 1 0 0 0 14.56 9A6.5 6.5 0 0 1 6 0z" />
              </svg>
            )}
          </button>
          <button
            className="header-btn"
            onClick={onReset}
            title="New session"
            aria-label="New session"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3.5 10a6.5 6.5 0 1 0 1.1-3.6" />
              <polyline points="3.5 4 3.5 7 6.5 7" />
            </svg>
          </button>
        </div>
      </div>
    </header>
  );
}
