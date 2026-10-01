/**
 * routes — URL-based navigation model for the app shell.
 *
 * Hash routes (`#/…`) are the single source of truth for which top-level
 * view is active. `useAppRoute` keeps React state in sync with the URL:
 *  - `navigate()` pushes history entries (so Back/Forward work),
 *  - `popstate`/`hashchange` listeners re-parse on external navigation.
 *
 * Legacy routes (`#/data`, `#/graph`, `#/heartbeat`, `#/dream` — plus the
 * first-load `?view=…&path=…` query) are redirected/mapped on parse so old
 * links keep working.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

/** Views reachable from the main nav. */
export type View = "chat" | "projects" | "apps" | "vault" | "calendar" | "kanban" | "workflows";

/** Advanced area views — reachable via `#/advanced/<area>` and the
 * Settings drawer, not from the main nav. */
export type AdvancedArea = "graph" | "heartbeat" | "dream";

export type AnyView = View | AdvancedArea;

export interface AppRoute {
  view: AnyView;
  /** Optional deep-link payload: vault file path, app folder, project id. */
  path?: string | null;
}

const MAIN_VIEWS: ReadonlySet<string> = new Set(["chat", "projects", "apps", "vault", "calendar", "kanban", "workflows"]);
const ADVANCED_AREAS: ReadonlySet<string> = new Set(["graph", "heartbeat", "dream"]);

/** Old view ids → new routes (kept so shared/bookmarked links survive). */
const LEGACY_MAP: Record<string, { view: AnyView; path?: string | null }> = {
  database: { view: "apps" },
  data: { view: "apps" },
  graph: { view: "graph" },
  heartbeat: { view: "heartbeat" },
  dream: { view: "dream" },
};

export function serializeRoute(view: AnyView, path?: string | null): string {
  if (view === "graph" || view === "heartbeat" || view === "dream") {
    return `#/advanced/${view}`;
  }
  if (path) return `#/${view}/${path.replace(/^\/+/, "")}`;
  return `#/${view}`;
}

/** Parse a location into a route. Returns null when nothing recognizable. */
export function parseLocation(hash: string, search: string): AppRoute | null {
  // 1) Hash routes — the modern path.
  const h = hash.replace(/^#\/?/, "");
  if (h) {
    const seg = h.split("/").filter(Boolean);
    const head = decodeURIComponent(seg[0] ?? "");
    if (head === "advanced") {
      const area = seg[1];
      if (area && ADVANCED_AREAS.has(area)) return { view: area as AdvancedArea };
      return null;
    }
    if (MAIN_VIEWS.has(head)) {
      const rest = seg.slice(1).map(decodeURIComponent).join("/");
      return { view: head as View, path: rest || null };
    }
    if (LEGACY_MAP[head]) return { ...LEGACY_MAP[head] };
    return null;
  }

  // 2) Legacy `?view=…&path=…` deep link (only honored on first load,
  //    when there is no hash yet).
  const qs = new URLSearchParams(search);
  const v = qs.get("view");
  if (!v) return null;
  const path = qs.get("path");
  if (MAIN_VIEWS.has(v)) return { view: v as View, path };
  if (LEGACY_MAP[v]) return { ...LEGACY_MAP[v], path: v === "data" || v === "database" ? null : path };
  return null;
}

const DEFAULT_ROUTE: AppRoute = { view: "chat", path: null };

function readCurrent(): AppRoute {
  return (
    parseLocation(window.location.hash, window.location.search) ?? DEFAULT_ROUTE
  );
}

/** Redirect legacy hash URLs in place so the address bar heals itself. */
function maybeRedirectLegacy(): void {
  const h = window.location.hash;
  const m = h.match(/^#\/(database|data|graph|heartbeat|dream)\/?$/);
  if (!m) return;
  const target = LEGACY_MAP[m[1]];
  if (target) window.history.replaceState(null, "", serializeRoute(target.view, target.path ?? null));
}

export function useAppRoute(): {
  route: AppRoute;
  navigate: (view: AnyView, path?: string | null) => void;
} {
  const [route, setRoute] = useState<AppRoute>(() => {
    maybeRedirectLegacy();
    return readCurrent();
  });

  useEffect(() => {
    const sync = () => setRoute(readCurrent());
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, []);

  // Keep the URL bar in sync when the route came from the legacy
  // `?view=` query (replace the ugly query with a clean hash).
  useEffect(() => {
    const target = serializeRoute(route.view, route.path);
    if (window.location.hash !== target) {
      window.history.replaceState(null, "", target);
    }
  }, [route]);

  const navigate = useCallback((view: AnyView, path?: string | null) => {
    const next: AppRoute = { view, path: path ?? null };
    setRoute((prev) => {
      if (prev.view === next.view && (prev.path ?? null) === (next.path ?? null)) return prev;
      return next;
    });
    const target = serializeRoute(view, path);
    if (window.location.hash !== target) {
      window.history.pushState(null, "", target);
    }
  }, []);

  return useMemo(() => ({ route, navigate }), [route, navigate]);
}
