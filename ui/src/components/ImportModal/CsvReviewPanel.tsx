import { useTranslation } from "react-i18next";
import type { CsvProposal } from "../../api/vault";

interface CsvReviewPanelProps {
  proposal: CsvProposal | null;
  analyzing: boolean;
  onApprove: () => void;
  onSkip: () => void;
}

export function CsvReviewPanel({
  proposal,
  analyzing,
  onApprove,
  onSkip,
}: CsvReviewPanelProps) {
  const { t } = useTranslation("vault");

  if (analyzing) {
    return (
      <div className="import-csv-review">
        <div className="import-csv-review-loading">
          <div className="import-csv-spinner" />
          <div>{t("vault:import.csvAnalyzing")}</div>
        </div>
      </div>
    );
  }

  if (!proposal) {
    return (
      <div className="import-csv-review">
        <div className="import-csv-review-empty">
          {t("vault:import.csvNoProposal")}
        </div>
        <button className="modal-btn" onClick={onSkip}>
          {t("vault:import.skip")}
        </button>
      </div>
    );
  }

  return (
    <div className="import-csv-review">
      <div className="import-csv-review-title">{t("vault:import.csvReviewTitle")}</div>
      <div className="import-csv-review-desc">{t("vault:import.csvReviewDesc")}</div>

      <div className="import-csv-entities">
        {proposal.entities.map((entity, i) => (
          <div key={i} className="import-csv-entity">
            <div className="import-csv-entity-name">{entity.name}</div>
            <div className="import-csv-entity-fields">
              {entity.fields.map((f, j) => (
                <span key={j} className="import-csv-field-tag">
                  {f.name}
                  <span className="import-csv-field-kind">{f.kind}</span>
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>

      {proposal.relationships.length > 0 && (
        <div className="import-csv-rels">
          <div className="import-csv-rels-title">{t("vault:import.csvRelationships")}</div>
          {proposal.relationships.map((rel, i) => (
            <div key={i} className="import-csv-rel">
              <span className="import-csv-rel-from">{rel.from}</span>
              <span className="import-csv-rel-type">{rel.type}</span>
              <span className="import-csv-rel-to">{rel.to}</span>
              {rel.description && (
                <span className="import-csv-rel-desc">{rel.description}</span>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="import-csv-review-actions">
        <button className="modal-btn modal-btn--primary" onClick={onApprove}>
          {t("vault:import.csvApprove")}
        </button>
        <button className="modal-btn" onClick={onSkip}>
          {t("vault:import.skip")}
        </button>
      </div>
    </div>
  );
}
