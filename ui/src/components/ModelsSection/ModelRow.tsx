import type { Model } from "../../api";

interface Props {
  model: Model;
  roles: string[];
  canEmbed: boolean;
  locked: boolean;
  confirmRemove: string | null;
  roleSaving: boolean;
  onEdit: () => void;
  onRemove: () => void;
  onConfirmRemove: () => void;
  onCancelRemove: () => void;
  onAssignRole: (role: string, modelId: string) => void;
  onUnassignRole: (role: string) => void;
}

export default function ModelRow({
  model: m,
  roles,
  canEmbed,
  locked,
  confirmRemove,
  roleSaving,
  onEdit,
  onRemove,
  onConfirmRemove,
  onCancelRemove,
  onAssignRole,
  onUnassignRole,
}: Props) {
  const isEmb = roles.includes("embedding");
  const isExt = roles.includes("extraction");

  return (
    <div className="settings-card">
      <div className="settings-card-row">
        <div className="settings-card-info">
          <div className="settings-model-header">
            <span className="settings-model-id">{m.id}</span>
            <span className="settings-model-provider">{m.provider}</span>
            <span className={`model-tier-chip model-tier-chip--${m.tier}`}>{m.tier}</span>
          </div>
          {m.notes && <div className="settings-model-notes">{m.notes}</div>}
          <div className="settings-tag-row">
            {m.tags.map((t) => (
              <span key={t} className="settings-tag-chip">{t}</span>
            ))}
          </div>
          <div className="model-role-badges">
            <button
              type="button"
              className={`model-role-badge ${isEmb ? "model-role-badge--active" : ""} ${!canEmbed && !isEmb ? "model-role-badge--disabled" : ""}`}
              onClick={() => {
                if (isEmb) onUnassignRole("embedding");
                else if (canEmbed) onAssignRole("embedding", m.id);
              }}
              disabled={(!isEmb && !canEmbed) || roleSaving}
              title={isEmb ? "Click to clear (falls back to built-in fastembed)" : canEmbed ? "Set as embedding model" : "Edit the model and enable 'Embedding capable' to use this role"}
            >
              Embedding
            </button>
            <button
              type="button"
              className={`model-role-badge ${isExt ? "model-role-badge--active" : ""}`}
              onClick={() => {
                if (isExt) onUnassignRole("extraction");
                else onAssignRole("extraction", m.id);
              }}
              disabled={roleSaving}
              title={isExt ? "Click to clear extraction model" : "Set as extraction model"}
            >
              Extraction
            </button>
          </div>
        </div>
        <div className="settings-card-actions">
          <button className="settings-icon-btn" title="Edit" onClick={onEdit}>
            ✎
          </button>
          {locked ? (
            <span className="settings-icon-btn settings-icon-btn--locked" title="Cannot remove — reassign role first">
              🔒
            </span>
          ) : confirmRemove === m.id ? (
            <>
              <button className="settings-icon-btn settings-icon-btn--bad" onClick={onRemove}>
                Confirm
              </button>
              <button className="settings-icon-btn" onClick={onCancelRemove}>
                Cancel
              </button>
            </>
          ) : (
            <button className="settings-icon-btn settings-icon-btn--bad" title="Remove" onClick={onConfirmRemove}>
              ✕
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
