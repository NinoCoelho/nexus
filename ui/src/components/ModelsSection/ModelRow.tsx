import type { Model } from "../../api";

interface Props {
  model: Model;
  roles: string[];
  canEmbed: boolean;
  isDefault: boolean;
  locked: boolean;
  confirmRemove: string | null;
  roleSaving: boolean;
  onEdit: () => void;
  onRemove: () => void;
  onConfirmRemove: () => void;
  onCancelRemove: () => void;
  onAssignRole: (role: string, modelId: string) => void;
  onUnassignRole: (role: string) => void;
  onSetDefault: (modelId: string) => void;
}

export default function ModelRow({
  model: m,
  roles,
  canEmbed,
  isDefault,
  locked,
  confirmRemove,
  roleSaving,
  onEdit,
  onRemove,
  onConfirmRemove,
  onCancelRemove,
  onAssignRole,
  onUnassignRole,
  onSetDefault,
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
              className={`model-role-badge ${isDefault ? "model-role-badge--active" : ""}`}
              onClick={() => {
                if (!isDefault) onSetDefault(m.id);
              }}
              disabled={isDefault || roleSaving}
              title={
                isDefault
                  ? "Este já é o modelo padrão"
                  : "Usar este modelo como padrão para novas conversas"
              }
            >
              Padrão
            </button>
            <button
              type="button"
              className={`model-role-badge ${isEmb ? "model-role-badge--active" : ""} ${
                !canEmbed && !isEmb ? "model-role-badge--disabled" : ""
              }`}
              onClick={() => {
                if (isEmb) onUnassignRole("embedding");
                else if (canEmbed) onAssignRole("embedding", m.id);
              }}
              disabled={(!isEmb && !canEmbed) || roleSaving}
              title={
                isEmb
                  ? "Limpar (volta para o embedder local)"
                  : canEmbed
                  ? "Usar este modelo para gerar embeddings (GraphRAG)"
                  : "Edite o modelo e marque 'Capaz de embeddings' para habilitar"
              }
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
              title={
                isExt
                  ? "Limpar — usa o extrator local (spaCy NER)"
                  : "Usar este modelo para extrair entidades/relações"
              }
            >
              Extração
            </button>
          </div>
        </div>
        <div className="settings-card-actions">
          <button className="settings-icon-btn" title="Editar" onClick={onEdit}>
            ✎
          </button>
          {locked ? (
            <span
              className="settings-icon-btn settings-icon-btn--locked"
              title="Não é possível remover — limpe a função primeiro"
            >
              🔒
            </span>
          ) : confirmRemove === m.id ? (
            <>
              <button className="settings-icon-btn settings-icon-btn--bad" onClick={onRemove}>
                Confirmar
              </button>
              <button className="settings-icon-btn" onClick={onCancelRemove}>
                Cancelar
              </button>
            </>
          ) : (
            <button
              className="settings-icon-btn settings-icon-btn--bad"
              title="Remover"
              onClick={onConfirmRemove}
            >
              ✕
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
