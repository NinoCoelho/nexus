interface Props {
  onReset: () => void;
  /** When true, the header shows a pulsing "YOLO ON" badge — an
   *  always-visible audit cue that prompts are being auto-approved. */
  yoloMode?: boolean;
}

export default function Header({ onReset, yoloMode = false }: Props) {
  return (
    <header className="header">
      <div className="header-inner">
        <div className="header-left">
          <div className="header-identity">
            <span className="header-name">Nexus</span>
            <span className="header-status">● Active Now</span>
          </div>
        </div>
        <div className="header-actions">
          {yoloMode && (
            <span className="yolo-badge" title="YOLO mode: confirm prompts auto-approved">
              YOLO ON
            </span>
          )}
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
