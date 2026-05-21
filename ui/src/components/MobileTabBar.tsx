import type { ComponentType } from "react";
import { IconChat, IconCalendar, IconVault, IconKanban, IconGraph, IconInsights } from "./Sidebar/icons";
import type { DatabaseSummary } from "../api/datatable";

type View = "chat" | "calendar" | "vault" | "kanban" | "data" | "graph" | "insights" | "heartbeat" | "dream";

interface Props {
  view: View;
  onViewChange: (v: View) => void;
  onOpenDrawer: () => void;
  databases?: DatabaseSummary[];
  selectedApp?: string | null;
  onAppSelect?: (folder: string) => void;
}

const STATIC_TABS: ReadonlyArray<{ id: View; label: string; Icon: ComponentType }> = [
  { id: "chat", label: "Chat", Icon: IconChat },
  { id: "calendar", label: "Calendar", Icon: IconCalendar },
  { id: "vault", label: "Vault", Icon: IconVault },
  { id: "kanban", label: "Kanban", Icon: IconKanban },
  { id: "graph", label: "Graph", Icon: IconGraph },
  { id: "insights", label: "Insights", Icon: IconInsights },
];

export default function MobileTabBar({ view, onViewChange, onOpenDrawer, databases, selectedApp, onAppSelect }: Props) {
  const appTabs = (databases ?? []).slice(0, 3);
  return (
    <nav className="mobile-tab-bar" aria-label="Primary">
      <button
        type="button"
        aria-label="Open menu"
        onClick={onOpenDrawer}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="4" y1="6" x2="20" y2="6" />
          <line x1="4" y1="12" x2="20" y2="12" />
          <line x1="4" y1="18" x2="20" y2="18" />
        </svg>
        <span>Menu</span>
      </button>
      {STATIC_TABS.map(({ id, label, Icon }) => (
        <button
          key={id}
          type="button"
          aria-label={label}
          aria-current={view === id ? "page" : undefined}
          className={view === id ? "is-active" : undefined}
          onClick={() => onViewChange(id)}
        >
          <Icon />
          <span>{label}</span>
        </button>
      ))}
      {appTabs.map((db) => {
        const active = view === "data" && selectedApp === db.folder;
        return (
          <button
            key={db.folder}
            type="button"
            aria-label={db.title}
            aria-current={active ? "page" : undefined}
            className={active ? "is-active" : undefined}
            onClick={() => onAppSelect?.(db.folder)}
          >
            <span className={`mobile-tab-letter${db.icon ? " mobile-tab-letter--emoji" : ""}`}>
              {db.icon || db.title.charAt(0).toUpperCase()}
            </span>
            <span>{db.title.length > 8 ? db.title.slice(0, 7) + "…" : db.title}</span>
          </button>
        );
      })}
    </nav>
  );
}
