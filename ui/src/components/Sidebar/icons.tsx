// Sidebar — all navigation icon components.

export function IconChat() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 3H2a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h4l4 3 4-3h4a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1z" />
    </svg>
  );
}

export function IconVault() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="14" height="14" rx="2" />
      <line x1="3" y1="8" x2="17" y2="8" />
      <line x1="8" y1="8" x2="8" y2="17" />
    </svg>
  );
}

export function IconGraph() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="4" r="2" />
      <circle cx="3" cy="16" r="2" />
      <circle cx="17" cy="16" r="2" />
      <line x1="10" y1="6" x2="3" y2="14" />
      <line x1="10" y1="6" x2="17" y2="14" />
      <line x1="5" y1="16" x2="15" y2="16" />
    </svg>
  );
}

export function IconInsights() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 14 7 10 11 13 17 5" />
      <polyline points="13 5 17 5 17 9" />
    </svg>
  );
}

export function IconKanban() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="14" height="14" rx="2" />
      <line x1="8" y1="3" x2="8" y2="17" />
      <line x1="13" y1="3" x2="13" y2="17" />
    </svg>
  );
}

export function IconDatabase() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="10" cy="5" rx="6" ry="2" />
      <path d="M4 5v5c0 1.1 2.7 2 6 2s6-0.9 6-2V5" />
      <path d="M4 10v5c0 1.1 2.7 2 6 2s6-0.9 6-2v-5" />
    </svg>
  );
}

export function IconCalendar() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="14" height="13" rx="2" />
      <line x1="3" y1="8" x2="17" y2="8" />
      <line x1="7" y1="2" x2="7" y2="5" />
      <line x1="13" y1="2" x2="13" y2="5" />
    </svg>
  );
}

export function IconGear() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2.5v2.5 M10 15v2.5 M2.5 10h2.5 M15 10h2.5 M4.7 4.7l1.8 1.8 M13.5 13.5l1.8 1.8 M4.7 15.3l1.8-1.8 M13.5 6.5l1.8-1.8" />
    </svg>
  );
}

export function IconCollapse({ collapsed }: { collapsed: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      {collapsed ? (
        <>
          <polyline points="7 4 13 10 7 16" />
          <polyline points="12 4 18 10 12 16" />
        </>
      ) : (
        <>
          <polyline points="13 4 7 10 13 16" />
          <polyline points="8 4 2 10 8 16" />
        </>
      )}
    </svg>
  );
}

export function IconHeartbeat() {
  return (
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 17s-7-4.35-7-8.5C3 5.36 5.5 3 8 3c1.5 0 2 .75 2 .75S10.5 3 12 3c2.5 0 5 2.36 5 5.5C17 12.65 10 17 10 17z" />
      <line x1="10" y1="7" x2="10" y2="11" />
      <line x1="8" y1="9" x2="12" y2="9" />
    </svg>
  );
}
