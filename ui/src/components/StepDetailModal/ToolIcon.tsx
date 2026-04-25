// StepDetailModal — icon component mapping tool names to SVG icons.

export default function ToolIcon({ tool }: { tool: string }) {
  switch (tool) {
    case "vault_list":
    case "vault_read":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
          <polyline points="9 1.5 9 5 12 5" />
        </svg>
      );
    case "vault_write":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 2.5a1.414 1.414 0 0 1 2 2L5 13H3v-2z" />
        </svg>
      );
    case "vault_search":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="6.5" cy="6.5" r="4" />
          <line x1="9.5" y1="9.5" x2="13" y2="13" />
        </svg>
      );
    case "http_call":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="8" cy="8" r="6" />
          <path d="M2 8h12M8 2a9 9 0 0 1 0 12M8 2a9 9 0 0 0 0 12" />
        </svg>
      );
    case "terminal":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="2 3.5 6 7.5 2 11.5" />
          <line x1="8" y1="11.5" x2="14" y2="11.5" />
        </svg>
      );
    case "kanban_manage":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="3" width="3" height="8" rx="0.5" />
          <rect x="6.5" y="3" width="3" height="5" rx="0.5" />
          <rect x="11" y="3" width="3" height="10" rx="0.5" />
        </svg>
      );
    case "skill_manage":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 2.5a1.414 1.414 0 0 1 2 2L5 13H3v-2z" />
        </svg>
      );
    case "skill_view":
    case "skills_list":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 2.5A1.5 1.5 0 0 1 4.5 1H12v13H4.5A1.5 1.5 0 0 1 3 12.5z" />
          <line x1="3" y1="12.5" x2="12" y2="12.5" />
        </svg>
      );
    case "vault_tags":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <line x1="5" y1="3" x2="5" y2="13" />
          <line x1="11" y1="3" x2="11" y2="13" />
          <line x1="2.5" y1="6" x2="13.5" y2="6" />
          <line x1="2.5" y1="10" x2="13.5" y2="10" />
        </svg>
      );
    case "vault_backlinks":
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6.5 9.5a3.536 3.536 0 0 0 5 0l2-2a3.536 3.536 0 0 0-5-5L7 4" />
          <path d="M9.5 6.5a3.536 3.536 0 0 0-5 0l-2 2a3.536 3.536 0 0 0 5 5L9 12" />
        </svg>
      );
    default:
      return (
        <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="8" cy="8" r="2.5" fill="currentColor" stroke="none" />
        </svg>
      );
  }
}
