/**
 * MobileTabBar — bottom tab bar shown on viewports <=768px.
 * Mirrors the Sidebar's view buttons in a touch-first layout.
 */
import type { ComponentType } from "react";
import { IconChat, IconCalendar, IconVault, IconKanban, IconGraph, IconInsights } from "./Sidebar/icons";

type View = "chat" | "calendar" | "vault" | "kanban" | "graph" | "insights";

interface Props {
  view: View;
  onViewChange: (v: View) => void;
  onOpenDrawer: () => void;
}

const TABS: ReadonlyArray<{ id: View; label: string; Icon: ComponentType }> = [
  { id: "chat", label: "Chat", Icon: IconChat },
  { id: "calendar", label: "Calendar", Icon: IconCalendar },
  { id: "vault", label: "Vault", Icon: IconVault },
  { id: "kanban", label: "Kanban", Icon: IconKanban },
  { id: "graph", label: "Graph", Icon: IconGraph },
  { id: "insights", label: "Insights", Icon: IconInsights },
];

export default function MobileTabBar({ view, onViewChange, onOpenDrawer }: Props) {
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
      {TABS.map(({ id, label, Icon }) => (
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
    </nav>
  );
}
