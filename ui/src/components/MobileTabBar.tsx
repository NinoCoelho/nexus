/**
 * MobileTabBar — bottom navigation for small screens. Mirrors the main
 * nav: Chat, Projects, Apps, Vault, Calendar, Workflows (+ menu drawer).
 * App-database selection happens inside the Apps view, not as tabs.
 */

import type { ComponentType } from "react";
import { IconChat, IconCalendar, IconVault, IconWorkflow, IconDatabase, IconProjects } from "./Sidebar/icons";
import type { AnyView } from "../routes";

interface Props {
  view: AnyView;
  onViewChange: (v: AnyView) => void;
  onOpenDrawer: () => void;
}

const TABS: ReadonlyArray<{ id: AnyView; label: string; Icon: ComponentType }> = [
  { id: "chat", label: "Chat", Icon: IconChat },
  { id: "projects", label: "Projects", Icon: IconProjects },
  { id: "apps", label: "Apps", Icon: IconDatabase },
  { id: "vault", label: "Vault", Icon: IconVault },
  { id: "calendar", label: "Calendar", Icon: IconCalendar },
  { id: "workflows", label: "Flows", Icon: IconWorkflow },
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
