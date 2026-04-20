import VaultEditorPanel from "./VaultEditorPanel";
import "./VaultView.css";

interface VaultViewProps {
  selectedPath: string | null;
  onDispatchToChat?: (sessionId: string, seedMessage: string) => void;
}

export default function VaultView({ selectedPath, onDispatchToChat }: VaultViewProps) {
  return (
    <div className="vault-view vault-view--editor-only">
      <VaultEditorPanel selectedPath={selectedPath} onDispatchToChat={onDispatchToChat} />
    </div>
  );
}
