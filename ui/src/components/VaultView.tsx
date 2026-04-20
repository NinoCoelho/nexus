import VaultEditorPanel from "./VaultEditorPanel";
import "./VaultView.css";

interface VaultViewProps {
  selectedPath: string | null;
}

export default function VaultView({ selectedPath }: VaultViewProps) {
  return (
    <div className="vault-view vault-view--editor-only">
      <VaultEditorPanel selectedPath={selectedPath} />
    </div>
  );
}
