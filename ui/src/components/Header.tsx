interface Props {
  onReset: () => void;
  onOpenSkills: () => void;
  onOpenSettings: () => void;
}

export default function Header({ onReset, onOpenSkills, onOpenSettings }: Props) {
  return (
    <header className="header">
      <div className="header-inner">
        <div className="header-left">
          <div className="header-avatar" aria-hidden="true" />
          <div className="header-identity">
            <span className="header-name">Nexus</span>
            <span className="header-status">● Active Now</span>
          </div>
        </div>
        <div className="header-actions">
          <button
            className="header-btn"
            onClick={onReset}
            title="Reset session"
            aria-label="Reset session"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3.5 10a6.5 6.5 0 1 0 1.1-3.6" />
              <polyline points="3.5 4 3.5 7 6.5 7" />
            </svg>
          </button>
          <button
            className="header-btn"
            onClick={onOpenSkills}
            title="Skills"
            aria-label="Open skills"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <line x1="3" y1="6" x2="17" y2="6" />
              <line x1="3" y1="10" x2="17" y2="10" />
              <line x1="3" y1="14" x2="17" y2="14" />
            </svg>
          </button>
          <button
            className="header-btn"
            onClick={onOpenSettings}
            title="Settings"
            aria-label="Open settings"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="10" cy="10" r="2.5" />
              <path d="M10 2.5v2.5 M10 15v2.5 M2.5 10h2.5 M15 10h2.5 M4.7 4.7l1.8 1.8 M13.5 13.5l1.8 1.8 M4.7 15.3l1.8-1.8 M13.5 6.5l1.8-1.8" />
            </svg>
          </button>
          <button
            className="header-btn"
            title="Search (coming soon)"
            aria-label="Search"
            disabled
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="8.5" cy="8.5" r="5" />
              <line x1="13" y1="13" x2="17" y2="17" />
            </svg>
          </button>
        </div>
      </div>
    </header>
  );
}
