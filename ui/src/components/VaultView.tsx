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
  onOpenInChat?: (sessionId: string, seedMessage: string, title: string) => void;
  onViewEntityGraph?: (path: string) => void;
  onOpenCalendar?: (path: string) => void;
  /** Open another data-table by path — drives "Open table" buttons in
   *  RelatedRowsPanel and any future drill-down. */
  onOpenTable?: (path: string) => void;
}

export default function VaultView({ selectedPath, onDispatchToChat, onOpenInChat, onViewEntityGraph, onOpenCalendar, onOpenTable }: VaultViewProps) {
  return (
    <div className="vault-view vault-view--editor-only">
      <VaultEditorPanel
        selectedPath={selectedPath}
        onDispatchToChat={onDispatchToChat}
        onOpenInChat={onOpenInChat}
        onViewEntityGraph={onViewEntityGraph}
        onOpenCalendar={onOpenCalendar}
        onOpenTable={onOpenTable}
      />
    </div>
  );
}
