/**
 * VaultView — thin wrapper around VaultEditorPanel.
 *
 * In the current layout there is no separate tree panel visible alongside
 * the editor; file selection happens in the Sidebar. This component exists
 * as a view target so App.tsx can switch to it (view="vault") and as a
 * place to mount future vault-level UI (search bar, breadcrumb nav, etc.).
 */

import VaultEditorPanel from "./VaultEditorPanel";
import "./VaultView.css";

interface VaultViewProps {
  selectedPath: string | null;
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string, model?: string) => void;
  onNavigateToSession?: (sessionId: string) => void;
  onViewEntityGraph?: (path: string) => void;
  onOpenCalendar?: (path: string) => void;
  /** Navigate the host app to open `path` in the Vault view — wired through
   *  to the preview modal so vault links opened from inside this view (e.g.
   *  backlinks, kanban card bodies) keep their "Open in Vault" affordance. */
  onOpenInVault?: (path: string) => void;
  /** Open another data-table by path — drives "Open table" buttons in
   *  RelatedRowsPanel and any future drill-down. */
  onOpenTable?: (path: string) => void;
}

export default function VaultView({ selectedPath, onDispatchToChat, onOpenInChat, onNavigateToSession, onViewEntityGraph, onOpenCalendar, onOpenInVault, onOpenTable }: VaultViewProps) {
  return (
    <div className="vault-view vault-view--editor-only">
      <VaultEditorPanel
        selectedPath={selectedPath}
        onDispatchToChat={onDispatchToChat}
        onOpenInChat={onOpenInChat}
        onNavigateToSession={onNavigateToSession}
        onViewEntityGraph={onViewEntityGraph}
        onOpenCalendar={onOpenCalendar}
        onOpenInVault={onOpenInVault}
        onOpenTable={onOpenTable}
      />
    </div>
  );
}
