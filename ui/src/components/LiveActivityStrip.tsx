import React from "react";
import type { TraceEvent } from "../api";
import "./LiveActivityStrip.css";

const SKIP_TOOLS = new Set(["_meta", "iter", "reply"]);

interface ToolMeta {
  label: string;
  icon: React.ReactElement;
}

function subtitleFor(tool: string, args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const a = args as Record<string, unknown>;
  if (tool.startsWith("vault_")) {
    const p = a.path ?? a.query ?? a.tag ?? "";
    return typeof p === "string" ? p.split("/").pop() ?? "" : "";
  }
  if (tool === "http_call") {
    const url = typeof a.url === "string" ? a.url : "";
    try { return new URL(url).hostname; } catch { return url.slice(0, 24); }
  }
  if (tool === "kanban_manage") return typeof a.action === "string" ? a.action : "";
  if (tool === "skill_view") return typeof a.name === "string" ? a.name : "";
  return "";
}

/* ── Icons (16px inline SVG strokes) ──────────────────────────────── */
const IconFolder = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5H12A1.5 1.5 0 0 1 13.5 6v5A1.5 1.5 0 0 1 12 12.5H4A1.5 1.5 0 0 1 2.5 11z" />
  </svg>
);
const IconFile = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
    <polyline points="9 1.5 9 5 12 5" />
  </svg>
);
const IconPencil = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M11 2.5a1.414 1.414 0 0 1 2 2L5 13H3v-2z" />
  </svg>
);
const IconMagnifier = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="6.5" cy="6.5" r="4" />
    <line x1="9.5" y1="9.5" x2="13" y2="13" />
  </svg>
);
const IconHash = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="5" y1="3" x2="5" y2="13" />
    <line x1="11" y1="3" x2="11" y2="13" />
    <line x1="2.5" y1="6" x2="13.5" y2="6" />
    <line x1="2.5" y1="10" x2="13.5" y2="10" />
  </svg>
);
const IconLink = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6.5 9.5a3.536 3.536 0 0 0 5 0l2-2a3.536 3.536 0 0 0-5-5L7 4" />
    <path d="M9.5 6.5a3.536 3.536 0 0 0-5 0l-2 2a3.536 3.536 0 0 0 5 5L9 12" />
  </svg>
);
const IconKanban = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="2" y="3" width="3" height="8" rx="0.5" />
    <rect x="6.5" y="3" width="3" height="5" rx="0.5" />
    <rect x="11" y="3" width="3" height="10" rx="0.5" />
  </svg>
);
const IconGlobe = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="8" r="6" />
    <path d="M2 8h12M8 2a9 9 0 0 1 0 12M8 2a9 9 0 0 0 0 12" />
  </svg>
);
const IconBook = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 2.5A1.5 1.5 0 0 1 4.5 1H12v13H4.5A1.5 1.5 0 0 1 3 12.5z" />
    <line x1="3" y1="12.5" x2="12" y2="12.5" />
  </svg>
);
const IconList = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="5" y1="5" x2="13" y2="5" />
    <line x1="5" y1="8" x2="13" y2="8" />
    <line x1="5" y1="11" x2="13" y2="11" />
    <circle cx="2.5" cy="5" r="0.75" fill="currentColor" />
    <circle cx="2.5" cy="8" r="0.75" fill="currentColor" />
    <circle cx="2.5" cy="11" r="0.75" fill="currentColor" />
  </svg>
);
const IconDot = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="8" r="2.5" fill="currentColor" stroke="none" />
  </svg>
);

function metaFor(tool: string): ToolMeta {
  switch (tool) {
    case "vault_list":    return { label: "Listing vault",   icon: <IconFolder /> };
    case "vault_read":    return { label: "Reading",         icon: <IconFile /> };
    case "vault_write":   return { label: "Writing",         icon: <IconPencil /> };
    case "vault_search":  return { label: "Searching vault", icon: <IconMagnifier /> };
    case "vault_tags":    return { label: "Tags",            icon: <IconHash /> };
    case "vault_backlinks": return { label: "Backlinks",     icon: <IconLink /> };
    case "kanban_manage": return { label: "Kanban",          icon: <IconKanban /> };
    case "http_call":     return { label: "HTTP",            icon: <IconGlobe /> };
    case "skill_manage":  return { label: "Authoring skill", icon: <IconPencil /> };
    case "skill_view":    return { label: "Reading skill",   icon: <IconBook /> };
    case "skills_list":   return { label: "Listing skills",  icon: <IconList /> };
    default:              return { label: tool.replace("_", " "), icon: <IconDot /> };
  }
}

interface Props {
  events: TraceEvent[];
  streaming: boolean;
}

interface Chip {
  tool: string;
  sub: string;
  count: number;
  /** true if this group includes the latest (still-running) event */
  latest: boolean;
  /** true if every event in this group produced a result */
  allDone: boolean;
  /** full sub-titles for each event in the group — shown in the tooltip */
  details: string[];
}

/**
 * Fold consecutive events that share the same (tool, sub) into a single chip
 * with a × N count. This keeps the strip legible when the agent loops on one
 * host (e.g. 8× http_call to the same docs domain).
 */
function coalesce(events: TraceEvent[]): Chip[] {
  const chips: Chip[] = [];
  events.forEach((ev, idx) => {
    const tool = ev.tool!;
    const sub = subtitleFor(tool, ev.args);
    const last = chips[chips.length - 1];
    const isLatest = idx === events.length - 1;
    const done = !!ev.result;
    if (last && last.tool === tool && last.sub === sub) {
      last.count += 1;
      last.latest = last.latest || isLatest;
      last.allDone = last.allDone && done;
      last.details.push(sub);
    } else {
      chips.push({
        tool,
        sub,
        count: 1,
        latest: isLatest,
        allDone: done,
        details: [sub],
      });
    }
  });
  return chips;
}

export default function LiveActivityStrip({ events, streaming }: Props) {
  const visible = events.filter((e) => e.tool && !SKIP_TOOLS.has(e.tool));
  if (visible.length === 0) return null;

  const chips = coalesce(visible);

  return (
    <div className="live-strip">
      {chips.map((chip, idx) => {
        const { label, icon } = metaFor(chip.tool);
        const dotClass = chip.latest && streaming
          ? "live-strip-dot live-strip-dot--pulse"
          : chip.allDone
          ? "live-strip-dot live-strip-dot--done"
          : "live-strip-dot live-strip-dot--idle";
        const title = chip.count > 1 ? chip.details.join("\n") : undefined;

        return (
          <div key={idx} className="live-strip-card" title={title}>
            <span className="live-strip-icon">{icon}</span>
            <span className="live-strip-text">
              <span className="live-strip-label">{label}</span>
              {chip.sub && <span className="live-strip-sub">{chip.sub}</span>}
            </span>
            {chip.count > 1 && (
              <span className="live-strip-count">×{chip.count}</span>
            )}
            <span className={dotClass} />
          </div>
        );
      })}
    </div>
  );
}
