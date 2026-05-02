import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("models");
  const isEmb = roles.includes("embedding");
  const isExt = roles.includes("extraction");
  const isVision = roles.includes("vision");

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
                  ? t("models:row.defaultAlready")
                  : t("models:row.defaultSet")
              }
            >
              {t("models:row.roleDefault")}
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
                  ? t("models:row.embeddingClear")
                  : canEmbed
                  ? t("models:row.embeddingAssign")
                  : t("models:row.embeddingDisabled")
              }
            >
              {t("models:row.roleEmbedding")}
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
                  ? t("models:row.extractionClear")
                  : t("models:row.extractionAssign")
              }
            >
              {t("models:row.roleExtraction")}
            </button>
            <button
              type="button"
              className={`model-role-badge ${isVision ? "model-role-badge--active" : ""}`}
              onClick={() => {
                if (isVision) onUnassignRole("vision");
                else onAssignRole("vision", m.id);
              }}
              disabled={roleSaving}
              title={
                isVision
                  ? t("models:row.visionClear")
                  : t("models:row.visionAssign")
              }
            >
              {t("models:row.roleVision")}
            </button>
          </div>
        </div>
        <div className="settings-card-actions">
          <button className="settings-icon-btn" title={t("models:row.editTitle")} onClick={onEdit}>
            ✎
          </button>
          {locked ? (
            <span
              className="settings-icon-btn settings-icon-btn--locked"
              title={t("models:row.lockedTitle")}
            >
              🔒
            </span>
          ) : confirmRemove === m.id ? (
            <>
              <button className="settings-icon-btn settings-icon-btn--bad" onClick={onRemove}>
                {t("models:row.confirmRemove")}
              </button>
              <button className="settings-icon-btn" onClick={onCancelRemove}>
                {t("models:row.cancelRemove")}
              </button>
            </>
          ) : (
            <button
              className="settings-icon-btn settings-icon-btn--bad"
              title={t("models:row.removeTitle")}
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
