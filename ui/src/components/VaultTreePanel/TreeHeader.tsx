// Header action buttons for VaultTreePanel (upload, new folder, new file).

interface TreeHeaderProps {
  onUploadClick: () => void;
  onNewFolder: () => void;
  onNewFile: () => void;
  uploadInputRef: React.RefObject<HTMLInputElement | null>;
  uploadCtxDirRef: React.RefObject<HTMLInputElement | null>;
  onUploadChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onCtxUploadChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}

export function TreeHeader({
  onUploadClick,
  onNewFolder,
  onNewFile,
  uploadInputRef,
  uploadCtxDirRef,
  onUploadChange,
  onCtxUploadChange,
}: TreeHeaderProps) {
  return (
    <div className="vault-tree-header">
      <span className="vault-tree-title">Files</span>
      <div className="vault-tree-header-actions">
        <button className="vault-tree-add-btn" onClick={onUploadClick} title="Upload files">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
            <polyline points="7,8 10,4 13,8" />
            <line x1="10" y1="4" x2="10" y2="14" />
          </svg>
        </button>
        <button className="vault-tree-add-btn" onClick={onNewFolder} title="New folder">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6z" />
            <line x1="10" y1="9" x2="10" y2="14" /><line x1="7.5" y1="11.5" x2="12.5" y2="11.5" />
          </svg>
        </button>
        <button className="vault-tree-add-btn" onClick={onNewFile} title="New file">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="10" y1="4" x2="10" y2="16" /><line x1="4" y1="10" x2="16" y2="10" />
          </svg>
        </button>
        <input
          ref={uploadInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={onUploadChange}
        />
        <input
          ref={uploadCtxDirRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={onCtxUploadChange}
        />
      </div>
    </div>
  );
}
