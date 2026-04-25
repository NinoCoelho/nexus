interface Props {
  selectedModel: string;
  models?: string[];
  menuOpen: boolean;
  menuRef: React.RefObject<HTMLDivElement | null>;
  onToggleMenu: () => void;
  onSelectModel: (m: string) => void;
}

export default function ModelBadge({ selectedModel, models, menuOpen, menuRef, onToggleMenu, onSelectModel }: Props) {
  const badgeLabel = selectedModel.split("/").pop();
  return (
    <div className="input-model-badge-wrap" ref={menuRef}>
      <button
        type="button"
        className="input-model-badge"
        onClick={onToggleMenu}
        title="Change model"
      >
        <svg width="10" height="10" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="10" cy="10" r="3" />
          <path d="M10 2v2M10 16v2M2 10h2M16 10h2M4.2 4.2l1.4 1.4M14.4 14.4l1.4 1.4M4.2 15.8l1.4-1.4M14.4 5.6l1.4-1.4" />
        </svg>
        {badgeLabel}
      </button>
      {menuOpen && models && models.length >= 1 && (
        <div className="input-menu input-menu--badge">
          <div className="input-menu-group">
            <span className="input-menu-heading">Model</span>
            {models.map((m) => (
              <button
                key={m}
                className={`input-menu-item${selectedModel === m ? " is-active" : ""}`}
                onClick={() => onSelectModel(m)}
              >
                {m.split("/").pop()}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
