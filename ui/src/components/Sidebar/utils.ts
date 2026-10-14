// Sidebar — pure helpers: relative timestamp formatting and sidebar width persistence.

export const SIDEBAR_WIDTH_KEY = "sidebar-width";
export const SIDEBAR_MIN_WIDTH = 180;
export const SIDEBAR_MAX_WIDTH = 560;

/** Views-nav collapse flag — collapsed by default (users live in the bots
 * list; the content nav is occasional). */
export const VIEWS_COLLAPSED_KEY = "sidebar-views-collapsed";

export function loadViewsCollapsed(): boolean {
  try {
    return localStorage.getItem(VIEWS_COLLAPSED_KEY) !== "0";
  } catch {
    return true;
  }
}

/** Teams-section collapse flag — expanded by default (teams are meant to be
 * seen; collapse is opt-in). */
export const TEAMS_COLLAPSED_KEY = "sidebar-teams-collapsed";

export function loadTeamsCollapsed(): boolean {
  try {
    return localStorage.getItem(TEAMS_COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function loadStoredWidth(): number {
  try {
    const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY);
    if (!raw) return 220;
    const n = parseInt(raw, 10);
    if (!isFinite(n)) return 220;
    return Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, n));
  } catch {
    return 220;
  }
}

export function fmtRelative(raw: string | number | undefined): string {
  if (raw == null) return "";
  let ts: number;
  if (typeof raw === "number") {
    // Backend sends unix seconds; Date expects ms.
    ts = raw < 1e12 ? raw * 1000 : raw;
  } else {
    const parsed = new Date(raw).getTime();
    if (isNaN(parsed)) return "";
    ts = parsed;
  }
  const diff = Date.now() - ts;
  if (diff < 0) return "just now";
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}
