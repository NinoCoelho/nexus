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
}

export default function VaultView({ selectedPath, onDispatchToChat, onOpenInChat, onViewEntityGraph, onOpenCalendar }: VaultViewProps) {
  return (
    <div className="vault-view vault-view--editor-only">
      <VaultEditorPanel
        selectedPath={selectedPath}
        onDispatchToChat={onDispatchToChat}
        onOpenInChat={onOpenInChat}
        onViewEntityGraph={onViewEntityGraph}
        onOpenCalendar={onOpenCalendar}
      />
    </div>
  );
}
